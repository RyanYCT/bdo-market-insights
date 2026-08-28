"""itemRegistry API Lambda: DynamoDB-backed item registry (``/v1/items``).

Powertools REST resolver implementing FR-8..12. The ``bdo-<stage>-items`` DynamoDB
table is the authoritative registry (ADR-0010); the Postgres ``item`` table is
populated lazily by the next ETL run. ``POST`` validates the item id against
arsha.io before writing. Runs outside the VPC (DynamoDB via default egress +
arsha.io).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated, Any, Literal

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import (
    APIGatewayRestResolver,
    Response,
    content_types,
)
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from aws_lambda_powertools.event_handler.openapi.params import Query
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import BaseModel, Field

from bdo_common import dynamo
from bdo_common.arsha_client import ArshaClient
from bdo_common.config import get_settings
from bdo_common.icons import public_icon_url
from bdo_common.models import Item

#: Public delivery base for self-hosted item icons (a CDN in front of the icons
#: bucket). Empty unless configured for the stage, in which case ``icon_url`` is
#: ``None`` for every item (the icon bytes exist in a private bucket but are not
#: publicly served yet).
ICON_BASE_URL = os.environ.get("ICON_BASE_URL", "")

#: Default and hard-cap page size for ``GET /v1/items``. The full catalog (tens
#: of thousands of items) is delivered as a separate CDN artifact, not through
#: this endpoint, so a single page stays small and well within the API Gateway
#: timeout; larger result sets are walked via the ``next`` cursor.
DEFAULT_ITEM_LIMIT = 200
MAX_ITEM_LIMIT = 1000

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")
app = APIGatewayRestResolver(enable_validation=True)


class ItemCreate(BaseModel):
    """Request body for ``POST /v1/items``. ``name`` is taken from arsha.io."""

    id: int
    category: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    model_id: str = "accessory_v1"
    cron_profile: str = "none"
    tracked: bool = True


class ItemUpdate(BaseModel):
    """Request body for ``PATCH /v1/items/{id}`` (all fields optional)."""

    name: str | None = None
    category: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    model_id: str | None = None
    cron_profile: str | None = None
    tracked: bool | None = None


class ItemResponse(BaseModel):
    """Public shape of a registry item.

    A curated view of the stored :class:`Item` for API consumers: the internal
    fields (``model_id``, ``cron_profile``) are intentionally omitted from the
    contract.
    """

    id: int
    name: str  # canonical English name
    names: dict[str, str] = Field(default_factory=dict)  # localized names, e.g. {"tw": "..."}
    grade: int | None = None  # raw BDO grade code; mapped to colour in the client
    category: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    tracked: bool = True
    icon_status: Literal["unset", "stored", "missing"] = "unset"
    # Public icon URL when the icon is materialized and a delivery base is
    # configured; ``None`` otherwise (see ``icon_status`` for why).
    icon_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_item(cls, item: Item) -> ItemResponse:
        """Project a stored :class:`Item` onto the public response shape."""
        return cls(
            id=item.id,
            name=item.name,
            names=item.names,
            grade=item.grade,
            category=item.category,
            main_category=item.main_category,
            sub_category=item.sub_category,
            tracked=item.tracked,
            icon_status=item.icon_status,
            icon_url=public_icon_url(item.id, icon_status=item.icon_status, base=ICON_BASE_URL),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class ItemListResponse(BaseModel):
    """Response body for ``GET /v1/items``: one page of items plus a cursor.

    ``count`` is the number of items in *this* page. ``next`` is an opaque
    pagination cursor to fetch the following page, or ``None`` when the result
    is exhausted.
    """

    items: list[ItemResponse]
    count: int
    next: str | None = None


def _dynamo_updates(body: ItemUpdate) -> dict[str, Any]:
    """Map a partial update to DynamoDB attribute values (``tracked`` -> str)."""
    updates: dict[str, Any] = {}
    for key, value in body.model_dump(exclude_none=True).items():
        updates[key] = str(value).lower() if key == "tracked" else value
    return updates


def _reject_demo_writes() -> None:
    """Block writes from the public read-only demo key.

    API Gateway keys can't be scoped to specific methods, so read-only access is
    enforced here: if ``DEMO_API_KEY_ID`` is set (the demo key is published) and
    the caller authenticated with it, mutating routes return 403. A no-op when
    the env var is empty (demo key disabled) or any other key is used.
    """
    demo_key_id = os.environ.get("DEMO_API_KEY_ID", "").strip()
    if not demo_key_id:
        return
    caller_key_id = app.current_event.request_context.identity.api_key_id
    if caller_key_id == demo_key_id:
        raise ForbiddenError("the public demo key is read-only; writes require a private API key")


@app.get("/v1/items")
def list_items(
    category: Annotated[
        str | None,
        Query(description="Filter by item category (e.g. 'accessories')."),
    ] = None,
    tracked: Annotated[
        bool | None,
        Query(
            description=(
                "Filter by tracked flag. Defaults to true when neither category nor tracked "
                "is given, so a bare call returns the tracked set (the full catalog is served "
                "as a separate CDN artifact, not by this endpoint)."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Query(description="Max items per page (1-1000, clamped)."),
    ] = DEFAULT_ITEM_LIMIT,
    next_cursor: Annotated[
        str | None,
        Query(alias="next", description="Opaque pagination cursor from a previous response."),
    ] = None,
) -> ItemListResponse:
    """FR-8: list items, filtered by ``category``/``tracked``, one bounded page.

    A bare call defaults to ``tracked=true`` and is served from the sparse
    ``tracked-index`` GSI, so it never scans the full catalog. Results are
    paginated: pass ``next`` to fetch the following page.
    """
    limit = max(1, min(limit, MAX_ITEM_LIMIT))
    # Bare call (no filter) -> the tracked set, a small bounded read via the GSI.
    if category is None and tracked is None:
        tracked = True
    try:
        items, cursor = dynamo.list_items(
            category=category, tracked=tracked, limit=limit, cursor=next_cursor
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return ItemListResponse(
        items=[ItemResponse.from_item(item) for item in items],
        count=len(items),
        next=cursor,
    )


@app.get("/v1/items/<item_id>")
def get_item(item_id: int) -> ItemResponse:
    """FR-9: return one item, or 404."""
    item = dynamo.get_item(item_id)
    if item is None:
        raise NotFoundError(f"item {item_id} not found")
    return ItemResponse.from_item(item)


@app.post("/v1/items")
def create_item(body: ItemCreate) -> Response[ItemResponse]:
    """FR-10: validate the id against arsha.io, then register in DynamoDB."""
    _reject_demo_writes()
    settings = get_settings()
    records = ArshaClient(region=settings.region).fetch_sub_list([body.id])
    if not records:
        raise BadRequestError(f"item id {body.id} not found on arsha.io ({settings.region})")
    item = Item(
        id=body.id,
        name=records[0].name,
        category=body.category,
        main_category=body.main_category,
        sub_category=body.sub_category,
        tracked=body.tracked,
        model_id=body.model_id,
        cron_profile=body.cron_profile,
    )
    dynamo.put_item(item)
    logger.info("registered item", extra={"item_id": body.id})
    return Response(
        status_code=201,
        content_type=content_types.APPLICATION_JSON,
        body=ItemResponse.from_item(item),
    )


@app.patch("/v1/items/<item_id>")
def update_item(item_id: int, body: ItemUpdate) -> ItemResponse:
    """FR-11: update metadata (incl. ``tracked``) in DynamoDB."""
    _reject_demo_writes()
    if dynamo.get_item(item_id) is None:
        raise NotFoundError(f"item {item_id} not found")
    updates = _dynamo_updates(body)
    if updates:
        dynamo.update_item(item_id, updates)
    refreshed = dynamo.get_item(item_id)
    if refreshed is None:  # pragma: no cover - concurrent delete
        raise NotFoundError(f"item {item_id} not found")
    return ItemResponse.from_item(refreshed)


@app.delete("/v1/items/<item_id>")
def delete_item(item_id: int) -> dict[str, Any]:
    """FR-12: soft delete -> ``tracked = false`` in DynamoDB."""
    _reject_demo_writes()
    if dynamo.get_item(item_id) is None:
        raise NotFoundError(f"item {item_id} not found")
    dynamo.update_item(item_id, {"tracked": "false"})
    logger.info("soft-deleted item", extra={"item_id": item_id})
    return {"id": item_id, "tracked": False}


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """API Gateway entrypoint; dispatches to the routes above."""
    metrics.add_metric(name="ApiKeyHits", unit=MetricUnit.Count, value=1)
    return app.resolve(event, context)
