"""insightsStore Lambda: resolve narrative + upsert market_summary.

Receives the output of insights_summarize (or ComputeDigest on Summarize
failure): region, period, target_date, digest, and optionally narrative +
model_id. If a valid LLM narrative is present, uses it directly; otherwise
falls back to the deterministic renderer.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit

from bdo_common import db
from bdo_common.insights.models import MarketDigest, Narrative, Period
from bdo_common.insights.narrative import render_narrative
from bdo_common.insights.repositories import SummaryRepo

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")


def _resolve_narrative(event: dict[str, Any], digest: MarketDigest) -> tuple[Narrative, str]:
    """Return (narrative, model_id) preferring LLM output over deterministic."""
    raw_narrative = event.get("narrative")
    if raw_narrative is not None:
        try:
            narrative = Narrative.model_validate(raw_narrative)
            model_id: str = event.get("model_id", "deterministic-v1")
            return narrative, model_id
        except Exception:
            logger.warning("LLM narrative validation failed; using deterministic fallback")

    return render_narrative(digest), "deterministic-v1"


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Resolve narrative and upsert the full MarketSummary."""
    region: str = event["region"]
    period: Period = event["period"]
    target_date = date.fromisoformat(event["target_date"])
    digest = MarketDigest.model_validate(event["digest"])

    narrative, model_id = _resolve_narrative(event, digest)

    conn = db.get_connection()
    try:
        SummaryRepo.upsert(
            conn,
            region=region,
            period=period,
            summary_date=target_date,
            lang="en",
            model_id=model_id,
            digest=digest,
            narrative=narrative,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        metrics.add_metric(name="InsightFailures", unit=MetricUnit.Count, value=1)
        logger.exception(
            "insightsStore failed; rolled back",
            extra={"region": region, "period": period, "target_date": target_date.isoformat()},
        )
        raise

    metrics.add_metric(name="SummariesGenerated", unit=MetricUnit.Count, value=1)
    logger.info(
        "insightsStore complete",
        extra={
            "region": region,
            "period": period,
            "target_date": target_date.isoformat(),
            "model_id": model_id,
        },
    )
    return {
        "region": region,
        "period": period,
        "target_date": target_date.isoformat(),
        "model_id": model_id,
        "status": "stored",
    }
