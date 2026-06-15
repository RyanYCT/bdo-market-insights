"""marketQuery API Lambda: read-only market data over RDS (``/v1/market``).

Powertools REST resolver implementing FR-13..15: raw hourly snapshots, daily
rollups, and a combined analysis (per-tier expected enhancement cost via the
base-rate ``accessory_v1`` model, plus rolling-window volatility, liquidity and
anomaly flag). Runs in the VPC and reads RDS via IAM auth; read-only, so each
request rolls back to release its transaction on the warm-reused connection.

Query parameters are declared as typed Powertools ``Query`` params so they are
part of the generated OpenAPI contract (``scripts/export_openapi.py`` ->
``infra/openapi.yaml``) and render in Swagger UI. Validation failures are mapped
to HTTP 400 (see ``handle_validation_error``) to keep the client contract stable
rather than Powertools' default 422.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Annotated, Any, Literal

import psycopg
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response, content_types
from aws_lambda_powertools.event_handler.openapi.exceptions import RequestValidationError
from aws_lambda_powertools.event_handler.openapi.params import Query
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

from bdo_common import analytics, db, pricing
from bdo_common.insights.models import Period
from bdo_common.insights.repositories import SummaryRepo
from bdo_common.models import SnapshotRow
from bdo_common.repositories import DailyRepo, SnapshotRepo

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")
app = APIGatewayRestResolver(enable_validation=True)

#: FR-13 hard cap on snapshots returned per request.
MAX_SNAPSHOT_LIMIT = 1000

#: Valid BDO server regions. Mirrors the ``BdoRegion`` AllowedValues enum in
#: ``template.yaml`` (the IaC source); an unknown region is rejected with 400.
Region = Literal[
    "na",
    "eu",
    "sea",
    "mena",
    "kr",
    "ru",
    "jp",
    "th",
    "tw",
    "sa",
    "console_eu",
    "console_na",
    "console_asia",
]

#: Default region when the caller omits ``region``.
DEFAULT_REGION: Region = "tw"


def handle_validation_error(exc: RequestValidationError) -> Response[str]:
    """Map query-param validation failures to 400 (not Powertools' default 422).

    Keeps the existing client contract (bad ``sid``/``from``/``to`` already
    returned 400) while now also covering the ``region`` enum and the
    ``window_days`` bounds.
    """
    detail = [{"loc": err.get("loc"), "type": err.get("type")} for err in exc.errors()]
    logger.warning("request validation failed", extra={"errors": detail})
    return Response(
        status_code=400,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps(
            {"statusCode": 400, "message": "Invalid request parameters", "detail": detail}
        ),
    )


# Registered via a call (not decorator syntax): Powertools' ``exception_handler``
# is unannotated upstream, which would trip mypy's ``disallow_untyped_decorators``.
app.exception_handler(RequestValidationError)(handle_validation_error)


@contextmanager
def _reading() -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
    """Yield the shared connection and roll back after (read-only)."""
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.rollback()


def _latest_price_by_sid(rows: list[SnapshotRow]) -> dict[int, float]:
    """Build a ``{sid: base_price}`` ladder from snapshots (newest-first input)."""
    prices: dict[int, float] = {}
    for row in rows:
        if row.sid not in prices:
            prices[row.sid] = float(row.base_price)
    return prices


@app.get("/v1/market/items/<item_id>/snapshots")
def get_snapshots(
    item_id: int,
    region: Annotated[Region, Query(description="BDO server region.")] = DEFAULT_REGION,
    sid: Annotated[int | None, Query(description="Enhancement sub-id; omit for all sids.")] = None,
    from_: Annotated[
        datetime | None,
        Query(alias="from", description="ISO-8601 datetime lower bound (inclusive)."),
    ] = None,
    to: Annotated[
        datetime | None, Query(description="ISO-8601 datetime upper bound (inclusive).")
    ] = None,
    limit: Annotated[
        int,
        Query(description="Max rows, 1-1000 (default 1000). Out-of-range values are clamped."),
    ] = MAX_SNAPSHOT_LIMIT,
) -> dict[str, Any]:
    """FR-13: raw hourly snapshots (capped at 1000), newest first."""
    limit = max(1, min(limit, MAX_SNAPSHOT_LIMIT))
    with _reading() as conn:
        rows = SnapshotRepo.get_snapshots(
            conn,
            region=region,
            item_id=item_id,
            sid=sid,
            from_dt=from_,
            to_dt=to,
            limit=limit,
        )
    return {
        "item_id": item_id,
        "region": region,
        "sid": sid,
        "count": len(rows),
        "snapshots": [row.model_dump(mode="json") for row in rows],
    }


@app.get("/v1/market/items/<item_id>/daily")
def get_daily(
    item_id: int,
    region: Annotated[Region, Query(description="BDO server region.")] = DEFAULT_REGION,
    sid: Annotated[int | None, Query(description="Enhancement sub-id; omit for all sids.")] = None,
    from_: Annotated[
        date | None,
        Query(alias="from", description="ISO-8601 date lower bound, YYYY-MM-DD (inclusive)."),
    ] = None,
    to: Annotated[
        date | None, Query(description="ISO-8601 date upper bound, YYYY-MM-DD (inclusive).")
    ] = None,
) -> dict[str, Any]:
    """FR-14: daily rollups, newest first."""
    with _reading() as conn:
        rows = DailyRepo.get_daily(
            conn,
            region=region,
            item_id=item_id,
            sid=sid,
            from_date=from_,
            to_date=to,
        )
    return {
        "item_id": item_id,
        "region": region,
        "sid": sid,
        "count": len(rows),
        "daily": [row.model_dump(mode="json") for row in rows],
    }


@app.get("/v1/market/items/<item_id>/analysis")
def get_analysis(
    item_id: int,
    region: Annotated[Region, Query(description="BDO server region.")] = DEFAULT_REGION,
    sid: Annotated[
        int | None, Query(description="Enhancement sub-id (default 0, the base item).")
    ] = None,
    window_days: Annotated[
        int,
        Query(ge=1, le=90, description="Trailing analytics window in days, 1-90 (default 14)."),
    ] = analytics.WINDOW_DAYS,
) -> dict[str, Any]:
    """FR-15: per-tier expected enhance cost + volatility/liquidity/anomaly."""
    sid_val = sid if sid is not None else 0

    with _reading() as conn:
        ladder_rows = SnapshotRepo.get_snapshots(
            conn, region=region, item_id=item_id, limit=MAX_SNAPSHOT_LIMIT
        )
        window = DailyRepo.get_daily_window(
            conn, region=region, item_id=item_id, sid=sid_val, window_days=window_days
        )

    prices = _latest_price_by_sid(ladder_rows)
    enhancement = (
        pricing.enhancement_analysis(prices, model_id="accessory_v1", intent="personal")
        if prices
        else None
    )

    # get_daily_window returns newest-first; analytics expects chronological so
    # that the latest close is the last element for the z-score.
    closes = [float(row.close_price) for row in reversed(window)]
    volumes = [float(row.total_trades_delta) for row in reversed(window)]
    market = analytics.market_analytics(closes, volumes, window_days=window_days)

    if not market.get("insufficient_data") and market["anomaly"]["is_anomalous"]:
        metrics.add_metric(name="AnomaliesDetected", unit=MetricUnit.Count, value=1)

    return {
        "item_id": item_id,
        "region": region,
        "sid": sid_val,
        "window_days": window_days,
        "enhancement": enhancement,
        "analytics": market,
    }


@app.get("/v1/insights")
def get_insights(
    region: Annotated[Region, Query(description="BDO server region.")] = DEFAULT_REGION,
    period: Annotated[Period, Query(description="Summary period: 'daily' or 'weekly'.")] = "daily",
    date_: Annotated[
        date | None,
        Query(alias="date", description="Summary date (YYYY-MM-DD); omit for latest."),
    ] = None,
    lang: Annotated[str, Query(description="Language code (default 'en').")] = "en",
) -> Response[str]:
    """Market insights summary (digest + narrative)."""
    with _reading() as conn:
        summary = SummaryRepo.get(
            conn, region=region, period=period, summary_date=date_, lang=lang
        )
    if summary is None:
        return Response(
            status_code=404,
            content_type=content_types.APPLICATION_JSON,
            body=json.dumps({"statusCode": 404, "message": "No summary found"}),
        )
    return Response(
        status_code=200,
        content_type=content_types.APPLICATION_JSON,
        body=json.dumps(
            {
                "region": summary.region,
                "period": summary.period,
                "summary_date": summary.summary_date.isoformat(),
                "lang": summary.lang,
                "model_id": summary.model_id,
                "digest": summary.digest.model_dump(mode="json"),
                "narrative": summary.narrative.model_dump(mode="json"),
            }
        ),
    )


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """API Gateway entrypoint; dispatches to the routes above."""
    metrics.add_metric(name="ApiKeyHits", unit=MetricUnit.Count, value=1)
    return app.resolve(event, context)
