"""rollupDaily ETL Lambda: aggregate the previous UTC day into market_daily.

Triggered by the state machine's Choice branch on the day-first (00:00 UTC)
run. The previous day is ``snapshot_at - 1 day`` (snapshot_at is midnight UTC
of the new day), so the completed day's hourly snapshots are all present
(FR-6). Aggregation runs server-side and is idempotent.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common import db
from bdo_common.repositories import DailyRepo

logger = Logger()
tracer = Tracer()


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Roll up the UTC day before ``snapshot_at`` into market_daily."""
    region = event["region"]
    snapshot_at = datetime.fromisoformat(event["snapshot_at"])
    trade_date = (snapshot_at - timedelta(days=1)).date()

    conn = db.get_connection()
    try:
        daily_rows = DailyRepo.rollup_day(conn, region=region, trade_date=trade_date)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception(
            "rollupDaily failed; rolled back",
            extra={"region": region, "trade_date": trade_date.isoformat()},
        )
        raise

    logger.info(
        "rollupDaily complete",
        extra={"region": region, "trade_date": trade_date.isoformat(), "daily_rows": daily_rows},
    )
    return {"region": region, "trade_date": trade_date.isoformat(), "daily_rows": daily_rows}
