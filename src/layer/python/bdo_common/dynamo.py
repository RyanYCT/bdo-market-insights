"""Typed DynamoDB wrappers for the per-stage items table (bdo-<stage>-items)."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from bdo_common.models import Item, MergedCatalogItem

logger = logging.getLogger(__name__)

_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "bdo-dev-items")
_GSI_NAME = "category-tracked-index"

#: Sparse GSI over all tracked items (ADR-0018). Its partition key is a marker
#: attribute written only on tracked items, so the index holds just the polled
#: subset regardless of catalog size. The ETL queries it instead of scanning.
_TRACKED_GSI_NAME = "tracked-index"
_TRACKED_MARKER_ATTR = "t"
_TRACKED_MARKER_VALUE = "1"


def _get_table() -> Any:  # boto3 Table resource (untyped)
    """Return the DynamoDB Table resource."""
    dynamodb = boto3.resource("dynamodb")
    return dynamodb.Table(_TABLE_NAME)


def _item_to_model(raw: dict[str, Any]) -> Item:
    """Convert a raw DynamoDB item dict to an Item model."""
    return Item(
        id=int(raw["id"]),
        name=raw.get("name", ""),
        names={str(k): str(v) for k, v in raw.get("names", {}).items()},
        grade=int(raw["grade"]) if raw.get("grade") is not None else None,
        category=raw.get("category"),
        main_category=raw.get("main_category"),
        sub_category=raw.get("sub_category"),
        tracked=raw.get("tracked", "true") == "true",
        model_id=raw.get("model_id", "accessory_v1"),
        cron_profile=raw.get("cron_profile", "standard"),
        icon_status=raw.get("icon_status", "unset"),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
    )


def get_item(item_id: int) -> Item | None:
    """Get a single item by ID, or None if not found."""
    table = _get_table()
    response: dict[str, Any] = table.get_item(Key={"id": item_id})
    raw: dict[str, Any] | None = response.get("Item")
    if raw is None:
        return None
    return _item_to_model(raw)


def _encode_cursor(key: dict[str, Any] | None) -> str | None:
    """Encode a DynamoDB LastEvaluatedKey as an opaque pagination token.

    Number keys come back as ``Decimal``; they are narrowed to ``int`` (item ids
    and index keys are integral) so the token is plain JSON, then URL-safe
    base64-encoded. ``None``/empty (no more pages) yields ``None``.
    """
    if not key:
        return None

    def _default(obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return int(obj)
        raise TypeError(f"unencodable cursor value: {obj!r}")

    raw = json.dumps(key, default=_default, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(token: str | None) -> dict[str, Any] | None:
    """Decode an opaque pagination token back into an ExclusiveStartKey.

    Returns ``None`` for an absent token and raises :class:`ValueError` for a
    malformed one, so the handler can map it to a 400 rather than a 500.
    """
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode())
        key = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid pagination cursor") from exc
    if not isinstance(key, dict):
        raise ValueError("invalid pagination cursor")
    return key


def list_items(
    *,
    category: str | None = None,
    tracked: bool | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> tuple[list[Item], str | None]:
    """List items with GSI-aware routing and optional bounded pagination.

    Routing (cheapest index that satisfies the filter):

    * ``tracked=True`` (no category) -> the sparse ``tracked-index`` GSI, which
      holds only the polled subset, so the read never scales with catalog size
      (ADR-0018). This is the API default (the handler defaults ``tracked`` to
      ``True`` when no filter is given).
    * ``category`` given -> the ``category-tracked-index`` GSI (with the
      ``tracked`` sort key when both are supplied), bounded by the category.
    * otherwise (e.g. ``tracked=False``) -> a table ``Scan`` with an optional
      ``tracked`` filter. This is the only unbounded access path, so callers
      should pass ``limit`` to bound it.

    When ``limit`` is given a single page of at most ``limit`` items is returned
    with an opaque ``next`` cursor (``None`` when exhausted); ``cursor`` resumes
    a previous page. When ``limit`` is ``None`` the full result is paginated
    internally and the cursor is always ``None``.
    """
    table = _get_table()
    kwargs: dict[str, Any] = {}
    run = table.scan

    if category is not None:
        kce: Any = Key("category").eq(category)
        if tracked is not None:
            kce = kce & Key("tracked").eq(str(tracked).lower())
        kwargs["IndexName"] = _GSI_NAME
        kwargs["KeyConditionExpression"] = kce
        run = table.query
    elif tracked is True:
        kwargs["IndexName"] = _TRACKED_GSI_NAME
        kwargs["KeyConditionExpression"] = Key(_TRACKED_MARKER_ATTR).eq(_TRACKED_MARKER_VALUE)
        run = table.query
    elif tracked is False:
        kwargs["FilterExpression"] = Attr("tracked").eq("false")

    start_key = _decode_cursor(cursor)
    if start_key is not None:
        kwargs["ExclusiveStartKey"] = start_key

    if limit is not None:
        # Single bounded page; hand back the LastEvaluatedKey as an opaque token.
        kwargs["Limit"] = limit
        response: dict[str, Any] = run(**kwargs)
        items_raw: list[dict[str, Any]] = response.get("Items", [])
        next_cursor = _encode_cursor(response.get("LastEvaluatedKey"))
        return [_item_to_model(raw) for raw in items_raw], next_cursor

    # Unbounded: walk every page (no caller passes this from the API path).
    response = run(**kwargs)
    items_raw = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = run(**kwargs)
        items_raw.extend(response.get("Items", []))
    return [_item_to_model(raw) for raw in items_raw], None


def put_item(item: Item) -> None:
    """Write an item to DynamoDB (full replace)."""
    table = _get_table()
    data: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "tracked": str(item.tracked).lower(),
        "model_id": item.model_id,
        "cron_profile": item.cron_profile,
        "icon_status": item.icon_status,
    }
    # Sparse tracked-index marker: present only when tracked (omitted otherwise
    # so untracked items stay out of the index).
    if item.tracked:
        data[_TRACKED_MARKER_ATTR] = _TRACKED_MARKER_VALUE
    if item.names:
        data["names"] = item.names
    if item.grade is not None:
        data["grade"] = item.grade
    if item.category is not None:
        data["category"] = item.category
    if item.main_category is not None:
        data["main_category"] = item.main_category
    if item.sub_category is not None:
        data["sub_category"] = item.sub_category
    if item.created_at is not None:
        data["created_at"] = item.created_at.isoformat()
    if item.updated_at is not None:
        data["updated_at"] = item.updated_at.isoformat()
    table.put_item(Item=data)


def _update_item_kwargs(item_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Build ``update_item`` kwargs for a partial update, or None if a no-op.

    Keeps the sparse tracked-index marker in lockstep with ``tracked``: set when
    it becomes ``"true"`` and removed when it becomes ``"false"``, so the
    ``tracked-index`` GSI always holds exactly the tracked items (ADR-0018).
    """
    set_parts: list[str] = []
    remove_parts: list[str] = []
    attr_names: dict[str, str] = {}
    attr_values: dict[str, Any] = {}

    for i, (key, value) in enumerate(updates.items()):
        placeholder_name = f"#k{i}"
        placeholder_value = f":v{i}"
        set_parts.append(f"{placeholder_name} = {placeholder_value}")
        attr_names[placeholder_name] = key
        attr_values[placeholder_value] = value

    # Keep the sparse tracked-index marker in sync with the `tracked` flag.
    if "tracked" in updates:
        attr_names["#tmark"] = _TRACKED_MARKER_ATTR
        if updates["tracked"] == "true":
            set_parts.append("#tmark = :tmark")
            attr_values[":tmark"] = _TRACKED_MARKER_VALUE
        else:
            remove_parts.append("#tmark")

    if not set_parts and not remove_parts:
        return None

    clauses: list[str] = []
    if set_parts:
        clauses.append("SET " + ", ".join(set_parts))
    if remove_parts:
        clauses.append("REMOVE " + ", ".join(remove_parts))

    kwargs: dict[str, Any] = {
        "Key": {"id": item_id},
        "UpdateExpression": " ".join(clauses),
        "ExpressionAttributeNames": attr_names,
    }
    if attr_values:
        kwargs["ExpressionAttributeValues"] = attr_values
    return kwargs


def update_item(item_id: int, updates: dict[str, Any]) -> None:
    """Partially update an item's attributes (see :func:`_update_item_kwargs`)."""
    kwargs = _update_item_kwargs(item_id, updates)
    if kwargs is not None:
        _get_table().update_item(**kwargs)


def bulk_update_items(
    items: list[tuple[int, dict[str, Any]]],
    *,
    max_workers: int = 16,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Apply many partial updates concurrently; return the count applied.

    Each item uses the same partial ``UpdateItem`` as :func:`update_item` (the
    tracked-index marker is kept in sync), run on a bounded thread pool with a
    per-thread Table resource. No-op updates are skipped. ``progress(done,
    total)`` is invoked as each write completes, when provided.
    """
    work = [
        (item_id, kwargs)
        for item_id, updates in items
        if (kwargs := _update_item_kwargs(item_id, updates)) is not None
    ]
    total = len(work)
    if total == 0:
        return 0

    def _one(job: tuple[int, dict[str, Any]]) -> None:
        _thread_local_table().update_item(**job[1])

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for _ in as_completed([executor.submit(_one, job) for job in work]):
            done += 1
            if progress is not None:
                progress(done, total)
    return total


def _catalog_update_kwargs(
    item_id: int, name: str, grade: int | None, names: dict[str, str] | None
) -> dict[str, Any]:
    """Build the ``update_item`` kwargs for a catalog partial upsert.

    Writes only ``name``/``names``/``grade`` plus ``updated_at`` (and
    ``created_at`` once, via ``if_not_exists``). ``tracked`` is initialized to
    ``"false"`` on newly created rows but preserved on existing ones (also via
    ``if_not_exists``), so a catalog-created item is untracked by default while
    the polled subset is never clobbered. The remaining ETL-owned attributes
    (``model_id``/``cron_profile``/``icon_status``) are left untouched (ADR-0018).
    ``ReturnValues`` is ``ALL_OLD`` so callers can detect a newly created item
    (empty old image).
    """
    now = datetime.now(tz=UTC).isoformat()
    # ``name`` is a DynamoDB reserved word; ``names``/``tracked`` are aliased
    # defensively. ``if_not_exists`` on ``tracked`` seeds new rows as untracked
    # without ever overwriting an already-tracked item's flag.
    set_parts = [
        "#name = :name",
        "updated_at = :updated_at",
        "created_at = if_not_exists(created_at, :created_at)",
        "#tracked = if_not_exists(#tracked, :untracked)",
    ]
    attr_names: dict[str, str] = {"#name": "name", "#tracked": "tracked"}
    attr_values: dict[str, Any] = {
        ":name": name,
        ":updated_at": now,
        ":created_at": now,
        ":untracked": "false",
    }
    if grade is not None:
        set_parts.append("grade = :grade")
        attr_values[":grade"] = grade
    if names:
        set_parts.append("#names = :names")
        attr_names["#names"] = "names"
        attr_values[":names"] = names

    return {
        "Key": {"id": item_id},
        "UpdateExpression": "SET " + ", ".join(set_parts),
        "ExpressionAttributeNames": attr_names,
        "ExpressionAttributeValues": attr_values,
        "ReturnValues": "ALL_OLD",
    }


def upsert_catalog_item(
    *,
    item_id: int,
    name: str,
    grade: int | None = None,
    names: dict[str, str] | None = None,
) -> bool:
    """Partially upsert catalog-owned fields for an item; return True if new.

    Preserves the ETL-owned attributes (partial ``UpdateItem``); an empty old
    image (``ReturnValues=ALL_OLD``) means the row was created by this call.
    """
    response = _get_table().update_item(**_catalog_update_kwargs(item_id, name, grade, names))
    return "Attributes" not in response


# boto3 resources are not safe to share across threads, so each worker thread
# builds its own Table once (bounded by the pool size, not per item).
_thread_local = threading.local()


def _thread_local_table() -> Any:
    """Return a per-thread Table resource for concurrent catalog writes."""
    table = getattr(_thread_local, "catalog_table", None)
    if table is None:
        table = boto3.resource("dynamodb").Table(_TABLE_NAME)
        _thread_local.catalog_table = table
    return table


def bulk_upsert_catalog_items(
    items: Iterable[MergedCatalogItem],
    *,
    max_workers: int = 16,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Concurrently partial-upsert many catalog items; return (total, newly_created).

    Each item is written with the same partial ``UpdateItem`` as
    :func:`upsert_catalog_item`, so ETL-owned attributes are preserved. Writes
    run on a bounded thread pool (each thread with its own Table resource) to
    stay well within a single Lambda invocation for the full ~tens-of-thousands
    catalog. ``newly_created`` counts items whose old image was empty.
    ``progress(done, total)`` is invoked as each write completes, when provided.
    """
    item_list = list(items)
    if not item_list:
        return (0, 0)

    def _one(item: MergedCatalogItem) -> bool:
        response = _thread_local_table().update_item(
            **_catalog_update_kwargs(item.item_id, item.name, item.grade, item.names)
        )
        return "Attributes" not in response

    total = len(item_list)
    created = 0
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for future in as_completed([executor.submit(_one, item) for item in item_list]):
            if future.result():
                created += 1
            done += 1
            if progress is not None:
                progress(done, total)
    return (total, created)


def _collect_fingerprints(
    raw_items: list[dict[str, Any]],
    out: dict[int, tuple[str, int | None, dict[str, str]]],
) -> None:
    """Accumulate (name, grade, names) fingerprints from scanned raw rows."""
    for raw in raw_items:
        grade = int(raw["grade"]) if raw.get("grade") is not None else None
        names = {str(k): str(v) for k, v in raw.get("names", {}).items()}
        out[int(raw["id"])] = (raw.get("name", ""), grade, names)


def scan_catalog_fingerprints() -> dict[int, tuple[str, int | None, dict[str, str]]]:
    """Scan all items, projecting the fields the catalog sync diffs against.

    Returns ``{id: (name, grade, names)}`` for every row, so the catalog sync
    can write only the items whose stored values differ from ``util/db``.
    """
    table = _get_table()
    scan_kwargs: dict[str, Any] = {
        "ProjectionExpression": "id, #name, grade, #names",
        "ExpressionAttributeNames": {"#name": "name", "#names": "names"},
    }
    fingerprints: dict[int, tuple[str, int | None, dict[str, str]]] = {}
    response = table.scan(**scan_kwargs)
    _collect_fingerprints(response.get("Items", []), fingerprints)
    while "LastEvaluatedKey" in response:
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**scan_kwargs)
        _collect_fingerprints(response.get("Items", []), fingerprints)
    return fingerprints


def scan_catalog_items() -> list[Item]:
    """Scan every item, projecting the public catalog fields for the artifact.

    Backs the static ``catalog.json`` published by ``catalogSync`` (the full
    catalog is delivered as a CDN artifact, not through the paginated
    ``/v1/items`` API). Projects only the fields the artifact exposes -- the
    internal ETL-owned attributes (``model_id``/``cron_profile``/``tracked``) are
    left out of the read. Ordering is left to the caller.
    """
    table = _get_table()
    scan_kwargs: dict[str, Any] = {
        "ProjectionExpression": (
            "id, #name, #names, grade, category, main_category, sub_category, icon_status"
        ),
        "ExpressionAttributeNames": {"#name": "name", "#names": "names"},
    }
    items_raw: list[dict[str, Any]] = []
    response = table.scan(**scan_kwargs)
    items_raw.extend(response.get("Items", []))
    while "LastEvaluatedKey" in response:
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.scan(**scan_kwargs)
        items_raw.extend(response.get("Items", []))
    return [_item_to_model(raw) for raw in items_raw]


def catalog_is_empty() -> bool:
    """Return True if the items table has no rows.

    A cheap ``Scan`` with ``Limit=1`` projecting only the key -- used by the
    bootstrap first-create guard (ADR-0028) to decide whether to seed a fresh
    environment. Directly reflects "is there any data?" rather than a proxy.
    """
    response = _get_table().scan(Limit=1, ProjectionExpression="id")
    return not response.get("Items")


def list_tracked_items() -> list[Item]:
    """Query the sparse tracked-index for all tracked items (ETL retrieveItems).

    Reads the ``tracked-index`` GSI, whose partition key is the marker attribute
    present only on tracked items. The read scales with the tracked subset, not
    the full catalog, so catalog growth never inflates the hourly ETL cost.
    """
    table = _get_table()
    query_kwargs: dict[str, Any] = {
        "IndexName": _TRACKED_GSI_NAME,
        "KeyConditionExpression": Key(_TRACKED_MARKER_ATTR).eq(_TRACKED_MARKER_VALUE),
    }
    response: dict[str, Any] = table.query(**query_kwargs)
    items_raw: list[dict[str, Any]] = response.get("Items", [])
    while "LastEvaluatedKey" in response:
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = table.query(**query_kwargs)
        items_raw.extend(response.get("Items", []))
    return [_item_to_model(raw) for raw in items_raw]
