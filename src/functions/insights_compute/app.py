"""insightsCompute Lambda: build a structured MarketDigest for a region/period.

Receives ``{region, period}`` from Step Functions, computes target_date as
yesterday (UTC), and delegates to ``build_digest``. The digest is returned as
JSON for the next state (insights_store). Read-only (rolls back after reading).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common import db
from bdo_common.insights.digest import build_digest
from bdo_common.insights.models import Period

logger = Logger()
tracer = Tracer()


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Build a MarketDigest and return it as serialisable JSON."""
    region: str = event["region"]
    period: Period = event["period"]
    target_date = (datetime.now(tz=UTC) - timedelta(days=1)).date()

    conn = db.get_connection()
    try:
        digest = build_digest(conn, region=region, period=period, target_date=target_date)
    finally:
        conn.rollback()

    logger.info(
        "insightsCompute complete",
        extra={
            "region": region,
            "period": period,
            "target_date": target_date.isoformat(),
            "entries": len(digest.entries),
        },
    )
    return {
        "region": region,
        "period": period,
        "target_date": target_date.isoformat(),
        "digest": digest.model_dump(mode="json"),
    }
