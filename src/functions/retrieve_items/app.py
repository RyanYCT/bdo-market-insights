"""retrieveItems ETL Lambda: project the active item list into the ETL run.

Queries the sparse ``tracked-index`` GSI for tracked items and emits them as
batches of <= 50, each carrying the full item metadata plus a per-execution
``snapshot_at`` (the Step Functions execution start time, truncated to the
hour, in UTC). Threading ``snapshot_at`` from here keeps snapshot writes
idempotent across retries (FR-5/NFR-4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common import dynamo
from bdo_common.models import Item

logger = Logger()
tracer = Tracer()

#: Max item IDs per arsha request / Map batch (FR-3).
BATCH_SIZE = 50


def _truncate_to_hour(execution_start_time: str | None) -> datetime:
    """Parse an ISO-8601 execution start time and floor it to the hour (UTC)."""
    if execution_start_time:
        raw = execution_start_time.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
    else:
        dt = datetime.now(tz=UTC)
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _item_meta(item: Item) -> dict[str, Any]:
    """Project the item attributes the downstream stages need."""
    return {
        "id": item.id,
        "name": item.name,
        "category": item.category,
        "main_category": item.main_category,
        "sub_category": item.sub_category,
        "model_id": item.model_id,
        "cron_table": item.cron_table,
    }


@tracer.capture_lambda_handler
@logger.inject_lambda_context
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Return ``{region, snapshot_at, is_day_first_run, batches}``."""
    region = event["region"]
    snapshot_dt = _truncate_to_hour(event.get("execution_start_time"))
    snapshot_at = snapshot_dt.isoformat()

    metas = [_item_meta(item) for item in dynamo.list_tracked_items()]
    batches = [
        {"region": region, "snapshot_at": snapshot_at, "items": metas[i : i + BATCH_SIZE]}
        for i in range(0, len(metas), BATCH_SIZE)
    ]

    logger.info(
        "retrieveItems complete",
        extra={"region": region, "item_count": len(metas), "batch_count": len(batches)},
    )
    return {
        "region": region,
        "snapshot_at": snapshot_at,
        "is_day_first_run": snapshot_dt.hour == 0,
        "batches": batches,
    }
