"""marketQuery API Lambda: read-only market data over RDS (``/v1/market``).

Powertools REST resolver implementing FR-13..15: raw hourly snapshots, daily
rollups, and a combined analysis (per-tier expected enhancement cost via the
base-rate ``accessory_v1`` model, plus rolling-window volatility, liquidity and
anomaly flag). Runs in the VPC and reads RDS via IAM auth; read-only, so each
request rolls back to release its transaction on the warm-reused connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import psycopg
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
from aws_lambda_powertools.event_handler.exceptions import BadRequestError
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

from bdo_common import analytics, db, pricing
from bdo_common.models import SnapshotRow
from bdo_common.repositories import DailyRepo, SnapshotRepo

logger = Logger()
tracer = Tracer()
metrics = Metrics(namespace="BdoMarket")
app = APIGatewayRestResolver(enable_validation=True)

#: FR-13 hard cap on snapshots returned per request.
MAX_SNAPSHOT_LIMIT = 1000


@contextmanager
def _reading() -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
    """Yield the shared connection and roll back after (read-only)."""
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.rollback()


def _region() -> str:
    return app.current_event.get_query_string_value(name="region", default_value="tw") or "tw"


def _opt_int(name: str) -> int | None:
    raw = app.current_event.get_query_string_value(name=name, default_value=None)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise BadRequestError(f"{name} must be an integer") from None


def _opt_datetime(name: str) -> datetime | None:
    raw = app.current_event.get_query_string_value(name=name, default_value=None)
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise BadRequestError(f"{name} must be an ISO-8601 datetime") from None


def _opt_date(name: str) -> date | None:
    raw = app.current_event.get_query_string_value(name=name, default_value=None)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise BadRequestError(f"{name} must be an ISO-8601 date (YYYY-MM-DD)") from None


def _latest_price_by_sid(rows: list[SnapshotRow]) -> dict[int, float]:
    """Build a ``{sid: base_price}`` ladder from snapshots (newest-first input)."""
    prices: dict[int, float] = {}
    for row in rows:
        if row.sid not in prices:
            prices[row.sid] = float(row.base_price)
    return prices


@app.get("/v1/market/items/<item_id>/snapshots")
def get_snapshots(item_id: int) -> dict[str, Any]:
    """FR-13: raw hourly snapshots (capped at 1000), newest first."""
    region = _region()
    sid = _opt_int("sid")
    limit = max(1, min(_opt_int("limit") or MAX_SNAPSHOT_LIMIT, MAX_SNAPSHOT_LIMIT))
    with _reading() as conn:
        rows = SnapshotRepo.get_snapshots(
            conn,
            region=region,
            item_id=item_id,
            sid=sid,
            from_dt=_opt_datetime("from"),
            to_dt=_opt_datetime("to"),
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
def get_daily(item_id: int) -> dict[str, Any]:
    """FR-14: daily rollups, newest first."""
    region = _region()
    sid = _opt_int("sid")
    with _reading() as conn:
        rows = DailyRepo.get_daily(
            conn,
            region=region,
            item_id=item_id,
            sid=sid,
            from_date=_opt_date("from"),
            to_date=_opt_date("to"),
        )
    return {
        "item_id": item_id,
        "region": region,
        "sid": sid,
        "count": len(rows),
        "daily": [row.model_dump(mode="json") for row in rows],
    }


@app.get("/v1/market/items/<item_id>/analysis")
def get_analysis(item_id: int) -> dict[str, Any]:
    """FR-15: per-tier expected enhance cost + volatility/liquidity/anomaly."""
    region = _region()
    sid = _opt_int("sid") or 0
    window_days = _opt_int("window_days") or analytics.WINDOW_DAYS

    with _reading() as conn:
        ladder_rows = SnapshotRepo.get_snapshots(
            conn, region=region, item_id=item_id, limit=MAX_SNAPSHOT_LIMIT
        )
        window = DailyRepo.get_daily_window(
            conn, region=region, item_id=item_id, sid=sid, window_days=window_days
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
        "sid": sid,
        "window_days": window_days,
        "enhancement": enhancement,
        "analytics": market,
    }


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """API Gateway entrypoint; dispatches to the routes above."""
    metrics.add_metric(name="ApiKeyHits", unit=MetricUnit.Count, value=1)
    return app.resolve(event, context)
