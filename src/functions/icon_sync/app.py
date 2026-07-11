"""iconSync Lambda: materialize icons for tracked items missing them.

Daily EventBridge-triggered. Queries the tracked set and, for each item whose
``icon_status`` is ``unset``, fetches the icon from the Pearl Abyss CDN and
stores it in the icons bucket (marking the item ``stored`` or ``missing``). Runs
independently of the ETL so it never touches the hourly hot path; new tracked
items get their icon by the next daily run. Idempotent -- items already
``stored``/``missing`` are skipped.
"""

from __future__ import annotations

import os
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common import dynamo, icons

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")


@metrics.log_metrics
@tracer.capture_lambda_handler
@logger.inject_lambda_context
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Materialize icons for tracked items with icon_status=unset."""
    bucket = os.environ["ICONS_BUCKET"]
    region = os.environ.get("BDO_REGION", "tw")

    pending = [item for item in dynamo.list_tracked_items() if item.icon_status == "unset"]
    stats = icons.sync_icons(pending, bucket=bucket, region=region)

    metrics.add_metric(name="IconsStored", unit=MetricUnit.Count, value=stats.stored)
    metrics.add_metric(name="IconsMissing", unit=MetricUnit.Count, value=stats.missing)
    metrics.add_metric(name="IconErrors", unit=MetricUnit.Count, value=stats.errors)
    logger.info(
        "iconSync complete",
        extra={
            "pending": len(pending),
            "stored": stats.stored,
            "missing": stats.missing,
            "errors": stats.errors,
        },
    )
    return {
        "pending": len(pending),
        "stored": stats.stored,
        "missing": stats.missing,
        "errors": stats.errors,
    }
