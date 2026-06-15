"""insightsStore Lambda: render narrative + upsert market_summary.

Receives the output of insights_compute (digest JSON, region, period,
target_date), reconstructs the MarketDigest, renders the deterministic
narrative, and upserts into market_summary. Commits on success, rolls back
on failure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aws_lambda_powertools import Logger, Tracer

from bdo_common import db
from bdo_common.insights.models import MarketDigest, Period
from bdo_common.insights.narrative import render_narrative
from bdo_common.insights.repositories import SummaryRepo

logger = Logger()
tracer = Tracer()


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Render narrative and upsert the full MarketSummary."""
    region: str = event["region"]
    period: Period = event["period"]
    target_date = date.fromisoformat(event["target_date"])
    digest = MarketDigest.model_validate(event["digest"])

    narrative = render_narrative(digest)

    conn = db.get_connection()
    try:
        SummaryRepo.upsert(
            conn,
            region=region,
            period=period,
            summary_date=target_date,
            lang="en",
            model_id="deterministic-v1",
            digest=digest,
            narrative=narrative,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception(
            "insightsStore failed; rolled back",
            extra={"region": region, "period": period, "target_date": target_date.isoformat()},
        )
        raise

    logger.info(
        "insightsStore complete",
        extra={"region": region, "period": period, "target_date": target_date.isoformat()},
    )
    return {
        "region": region,
        "period": period,
        "target_date": target_date.isoformat(),
        "status": "stored",
    }
