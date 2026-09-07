"""iconSync Lambda: warm-prefetch icons for the tracked set.

Invoked on demand -- by the bootstrap orchestrator to pre-warm a fresh
environment's tracked icons, or manually. Queries the tracked set and fetches
each icon from the Pearl Abyss CDN into the delivery bucket. Idempotent -- the
S3 store simply re-writes, so a re-run is safe.

No longer scheduled: ongoing and whole-catalog materialization is handled on
demand by the cdn stack's read-through origin (ADR-0033), which fetches and
stores any icon on first request. This function only pre-warms the tracked
subset so a fresh environment's core icons are instant.
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
    """Warm-prefetch icons for every tracked item."""
    bucket = os.environ["ICONS_BUCKET"]
    region = os.environ.get("BDO_REGION", "tw")

    tracked = dynamo.list_tracked_items()
    stats = icons.sync_icons(tracked, bucket=bucket, region=region)

    metrics.add_metric(name="IconsStored", unit=MetricUnit.Count, value=stats.stored)
    metrics.add_metric(name="IconsMissing", unit=MetricUnit.Count, value=stats.missing)
    metrics.add_metric(name="IconErrors", unit=MetricUnit.Count, value=stats.errors)
    logger.info(
        "iconSync complete",
        extra={
            "tracked": len(tracked),
            "stored": stats.stored,
            "missing": stats.missing,
            "errors": stats.errors,
        },
    )
    return {
        "tracked": len(tracked),
        "stored": stats.stored,
        "missing": stats.missing,
        "errors": stats.errors,
    }
