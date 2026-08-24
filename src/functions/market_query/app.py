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
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

import psycopg
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response, content_types
from aws_lambda_powertools.event_handler.openapi.exceptions import RequestValidationError
from aws_lambda_powertools.event_handler.openapi.params import Query
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from pydantic import BaseModel

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

#: Default snapshots per request when the caller omits ``limit``. Sized for the
#: common daily (24) and weekly (168) hourly charts; longer ranges should use the
#: daily endpoint. Kept well below the FR-13 cap so a bare call is cheap.
DEFAULT_SNAPSHOT_LIMIT = 168

#: One hour, for hourly-bucket coverage math (snapshots are top-of-hour UTC).
_ONE_HOUR = timedelta(hours=1)

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


class Coverage(BaseModel):
    """Hourly coverage of the returned window, so clients can render gaps.

    Computed over the requested window when ``from``/``to`` are supplied, else
    over the span of the returned rows. ``present_hours`` counts *distinct*
    hourly ``snapshot_at`` values (snapshots are top-of-hour UTC), so it is
    correct even for ``sid=null`` responses that carry several rows per hour.
    Both endpoints are inclusive. ``truncated`` is true when the row ``limit``
    bounded the result -- i.e. older data exists beyond ``window_start`` -- so a
    high ``missing_hours`` there reflects the cap, not gaps.
    """

    window_start: datetime
    window_end: datetime
    expected_hours: int
    present_hours: int
    missing_hours: int
    truncated: bool


class SnapshotsResponse(BaseModel):
    """Response body for ``GET /v1/market/items/{item_id}/snapshots`` (FR-13)."""

    item_id: int
    region: str
    sid: int | None
    count: int
    coverage: Coverage | None
    snapshots: list[SnapshotRow]


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


def _floor_to_hour(dt: datetime) -> datetime:
    """Floor a datetime to the top of its UTC hour (tz-aware).

    Naive inputs are treated as UTC so comparisons against the tz-aware
    ``snapshot_at`` values never mix naive/aware datetimes.
    """
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _snapshot_coverage(
    rows: list[SnapshotRow],
    from_: datetime | None,
    to: datetime | None,
    limit: int,
) -> Coverage | None:
    """Hourly coverage of the returned snapshots, or None if no window is defined.

    The window is the requested ``[from, to]`` when given, else the span of the
    returned rows. Returns None only when there is neither data nor an explicit
    window (nothing to describe).
    """
    present = {_floor_to_hour(row.snapshot_at) for row in rows}
    start = _floor_to_hour(from_) if from_ is not None else (min(present) if present else None)
    end = _floor_to_hour(to) if to is not None else (max(present) if present else None)
    if start is None or end is None or end < start:
        return None
    expected = int((end - start) / _ONE_HOUR) + 1
    present_in_window = sum(1 for hour in present if start <= hour <= end)
    return Coverage(
        window_start=start,
        window_end=end,
        expected_hours=expected,
        present_hours=present_in_window,
        missing_hours=max(0, expected - present_in_window),
        truncated=len(rows) == limit,
    )


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
    region: Annotated[
        Region,
        Query(
            description=(
                "BDO server region. Accepted: na, eu, sea, mena, kr, ru, jp, th, tw, sa, "
                "console_eu, console_na, console_asia. Default: tw."
            )
        ),
    ] = DEFAULT_REGION,
    sid: Annotated[int | None, Query(description="Enhancement sub-id. Default: all sids.")] = None,
    from_: Annotated[
        datetime | None,
        Query(
            alias="from",
            description="Lower bound, inclusive, ISO-8601 datetime. Default: unbounded.",
        ),
    ] = None,
    to: Annotated[
        datetime | None,
        Query(description="Upper bound, inclusive, ISO-8601 datetime. Default: unbounded."),
    ] = None,
    limit: Annotated[
        int,
        Query(
            description=(
                "Max snapshots returned. Range: 1-1000. Default: 168 (one week of hourly "
                "points). Out-of-range is clamped. For longer ranges use the daily endpoint."
            )
        ),
    ] = DEFAULT_SNAPSHOT_LIMIT,
) -> SnapshotsResponse:
    """FR-13: raw hourly snapshots (default 168, capped at 1000), newest first."""
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
    return SnapshotsResponse(
        item_id=item_id,
        region=region,
        sid=sid,
        count=len(rows),
        coverage=_snapshot_coverage(rows, from_, to, limit),
        snapshots=rows,
    )


@app.get("/v1/market/items/<item_id>/daily")
def get_daily(
    item_id: int,
    region: Annotated[
        Region,
        Query(
            description=(
                "BDO server region. Accepted: na, eu, sea, mena, kr, ru, jp, th, tw, sa, "
                "console_eu, console_na, console_asia. Default: tw."
            )
        ),
    ] = DEFAULT_REGION,
    sid: Annotated[int | None, Query(description="Enhancement sub-id. Default: all sids.")] = None,
    from_: Annotated[
        date | None,
        Query(
            alias="from",
            description="Lower bound, inclusive, ISO-8601 date (YYYY-MM-DD). Default: unbounded.",
        ),
    ] = None,
    to: Annotated[
        date | None,
        Query(
            description="Upper bound, inclusive, ISO-8601 date (YYYY-MM-DD). Default: unbounded."
        ),
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
    region: Annotated[
        Region,
        Query(
            description=(
                "BDO server region. Accepted: na, eu, sea, mena, kr, ru, jp, th, tw, sa, "
                "console_eu, console_na, console_asia. Default: tw."
            )
        ),
    ] = DEFAULT_REGION,
    sid: Annotated[
        int | None, Query(description="Enhancement sub-id. Default: 0 (the base item).")
    ] = None,
    window_days: Annotated[
        int,
        Query(
            ge=1,
            le=90,
            description=(
                "Trailing analytics window in days. Range: 1-90. Default: 14. "
                "Needs at least 7 daily points."
            ),
        ),
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
    region: Annotated[
        Region,
        Query(
            description=(
                "BDO server region. Accepted: na, eu, sea, mena, kr, ru, jp, th, tw, sa, "
                "console_eu, console_na, console_asia. Default: tw."
            )
        ),
    ] = DEFAULT_REGION,
    period: Annotated[
        Period, Query(description="Summary cadence. Accepted: daily, weekly. Default: daily.")
    ] = "daily",
    date_: Annotated[
        date | None,
        Query(
            alias="date",
            description="Summary date, ISO-8601 (YYYY-MM-DD). Default: latest available.",
        ),
    ] = None,
    lang: Annotated[str, Query(description="Narrative language code. Default: en.")] = "en",
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
