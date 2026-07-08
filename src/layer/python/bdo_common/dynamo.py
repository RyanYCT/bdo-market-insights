"""Typed DynamoDB wrappers for the per-stage items table (bdo-<stage>-items)."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from bdo_common.models import Item

logger = logging.getLogger(__name__)

_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "bdo-dev-items")
_GSI_NAME = "category-tracked-index"


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
        cron_table=raw.get("cron_table", "a"),
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


def list_items(*, category: str | None = None, tracked: bool | None = None) -> list[Item]:
    """List items, optionally filtering by category and/or tracked status."""
    table = _get_table()

    if category is not None and tracked is not None:
        # Use the GSI
        query_kwargs: dict[str, Any] = {
            "IndexName": _GSI_NAME,
            "KeyConditionExpression": Key("category").eq(category)
            & Key("tracked").eq(str(tracked).lower()),
        }
        response: dict[str, Any] = table.query(**query_kwargs)
        items_raw: list[dict[str, Any]] = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = table.query(**query_kwargs)
            items_raw.extend(response.get("Items", []))
    elif category is not None:
        # Query GSI with just partition key
        query_kwargs = {
            "IndexName": _GSI_NAME,
            "KeyConditionExpression": Key("category").eq(category),
        }
        response = table.query(**query_kwargs)
        items_raw = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = table.query(**query_kwargs)
            items_raw.extend(response.get("Items", []))
    else:
        # Scan (with optional filter)
        scan_kwargs: dict[str, Any] = {}
        if tracked is not None:
            scan_kwargs["FilterExpression"] = Attr("tracked").eq(str(tracked).lower())
        response = table.scan(**scan_kwargs)
        items_raw = response.get("Items", [])
        # Handle pagination
        while "LastEvaluatedKey" in response:
            scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
            response = table.scan(**scan_kwargs)
            items_raw.extend(response.get("Items", []))

    return [_item_to_model(raw) for raw in items_raw]


def put_item(item: Item) -> None:
    """Write an item to DynamoDB (full replace)."""
    table = _get_table()
    data: dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "tracked": str(item.tracked).lower(),
        "model_id": item.model_id,
        "cron_table": item.cron_table,
        "icon_status": item.icon_status,
    }
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


def update_item(item_id: int, updates: dict[str, Any]) -> None:
    """Partially update an item's attributes."""
    table = _get_table()
    expr_parts: list[str] = []
    attr_names: dict[str, str] = {}
    attr_values: dict[str, Any] = {}

    for i, (key, value) in enumerate(updates.items()):
        placeholder_name = f"#k{i}"
        placeholder_value = f":v{i}"
        expr_parts.append(f"{placeholder_name} = {placeholder_value}")
        attr_names[placeholder_name] = key
        attr_values[placeholder_value] = value

    if not expr_parts:
        return

    table.update_item(
        Key={"id": item_id},
        UpdateExpression="SET " + ", ".join(expr_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
    )


def upsert_catalog_item(
    *,
    item_id: int,
    name: str,
    grade: int | None = None,
    names: dict[str, str] | None = None,
) -> bool:
    """Partially upsert catalog-owned fields for an item; return True if new.

    Writes only ``name``/``names``/``grade`` plus ``updated_at`` (and
    ``created_at`` once, via ``if_not_exists``). Never touches the ETL-owned
    attributes (``tracked``/``model_id``/``cron_table``/``icon_status``), so the
    weekly catalog sync cannot clobber the polled subset (ADR-0018). Uses
    ``ReturnValues=ALL_OLD``: an empty old image means the row was created by
    this call (a newly discovered item).
    """
    table = _get_table()
    now = datetime.now(tz=UTC).isoformat()

    # ``name`` is a DynamoDB reserved word; ``names`` is aliased defensively.
    set_parts = [
        "#name = :name",
        "updated_at = :updated_at",
        "created_at = if_not_exists(created_at, :created_at)",
    ]
    attr_names: dict[str, str] = {"#name": "name"}
    attr_values: dict[str, Any] = {
        ":name": name,
        ":updated_at": now,
        ":created_at": now,
    }
    if grade is not None:
        set_parts.append("grade = :grade")
        attr_values[":grade"] = grade
    if names:
        set_parts.append("#names = :names")
        attr_names["#names"] = "names"
        attr_values[":names"] = names

    response = table.update_item(
        Key={"id": item_id},
        UpdateExpression="SET " + ", ".join(set_parts),
        ExpressionAttributeNames=attr_names,
        ExpressionAttributeValues=attr_values,
        ReturnValues="ALL_OLD",
    )
    return "Attributes" not in response


def scan_tracked_items() -> list[Item]:
    """Scan all items where tracked=true. Used by ETL retrieveItems."""
    return list_items(tracked=True)
