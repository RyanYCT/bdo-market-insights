"""purgeOldSnapshots ETL Lambda: enforce the 90-day snapshot retention.

Triggered by its own daily EventBridge rule (00:30 UTC). Deletes every
market_snapshot row older than 90 days across all regions (FR-7). market_daily
is retained indefinitely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common import db
from bdo_common.repositories import SnapshotRepo

logger = Logger()
tracer = Tracer()

RETENTION_DAYS = 90


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Delete snapshots older than the retention window."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=RETENTION_DAYS)

    conn = db.get_connection()
    try:
        deleted = SnapshotRepo.purge_older_than(conn, cutoff)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("purgeOldSnapshots failed; rolled back")
        raise

    logger.info(
        "purgeOldSnapshots complete",
        extra={"cutoff": cutoff.isoformat(), "deleted": deleted},
    )
    return {"cutoff": cutoff.isoformat(), "deleted": deleted}
