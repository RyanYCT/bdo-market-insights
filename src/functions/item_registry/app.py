"""itemRegistry API Lambda: DynamoDB-backed item registry (``/v1/items``).

Powertools REST resolver implementing FR-8..12. The ``bdo-<stage>-items`` DynamoDB
table is the authoritative registry (ADR-0010); the Postgres ``item`` table is
populated lazily by the next ETL run. ``POST`` validates the item id against
arsha.io before writing. Runs outside the VPC (DynamoDB via default egress +
arsha.io).
"""

from __future__ import annotations

import os
from typing import Any, Literal

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
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import BaseModel

from bdo_common import dynamo
from bdo_common.arsha_client import ArshaClient
from bdo_common.config import get_settings
from bdo_common.models import Item

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
    cron_table: Literal["a", "b"] = "a"
    tracked: bool = True


class ItemUpdate(BaseModel):
    """Request body for ``PATCH /v1/items/{id}`` (all fields optional)."""

    name: str | None = None
    category: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    model_id: str | None = None
    cron_table: Literal["a", "b"] | None = None
    tracked: bool | None = None


def _parse_bool(value: str | None) -> bool | None:
    """Parse a query-string flag into a tri-state bool (None = unset)."""
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes")


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
def list_items() -> dict[str, Any]:
    """FR-8: list items, optionally filtered by ``category`` and ``tracked``."""
    category = app.current_event.get_query_string_value(name="category", default_value=None)
    tracked = _parse_bool(
        app.current_event.get_query_string_value(name="tracked", default_value=None)
    )
    items = dynamo.list_items(category=category, tracked=tracked)
    return {"items": [item.model_dump(mode="json") for item in items], "count": len(items)}


@app.get("/v1/items/<item_id>")
def get_item(item_id: int) -> dict[str, Any]:
    """FR-9: return one item, or 404."""
    item = dynamo.get_item(item_id)
    if item is None:
        raise NotFoundError(f"item {item_id} not found")
    return item.model_dump(mode="json")


@app.post("/v1/items")
def create_item(body: ItemCreate) -> Response[dict[str, Any]]:
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
        cron_table=body.cron_table,
    )
    dynamo.put_item(item)
    logger.info("registered item", extra={"item_id": body.id})
    return Response(
        status_code=201,
        content_type=content_types.APPLICATION_JSON,
        body=item.model_dump(mode="json"),
    )


@app.patch("/v1/items/<item_id>")
def update_item(item_id: int, body: ItemUpdate) -> dict[str, Any]:
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
    return refreshed.model_dump(mode="json")


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
