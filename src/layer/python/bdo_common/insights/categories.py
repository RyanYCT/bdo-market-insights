"""Category registry for market-insights digest construction.

Mirrors the pricing.py register_model/get_model/available_models pattern
(ADR-0012). Each category handler enriches raw mover tuples into DigestEntry
objects with category-specific analytics.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from bdo_common.analytics import MIN_POINTS, daily_liquidity, daily_volatility, detect_anomaly
from bdo_common.insights.models import DigestEntry
from bdo_common.pricing import enhancement_analysis

if TYPE_CHECKING:
    import psycopg

# Type alias for category handler functions.
# Signature: (conn, region, period, target_date, movers_data) -> list[DigestEntry]
# movers_data is the output of InsightRepo.top_movers:
#   list of (item_id, item_name, sid, close_price, prev_close_price, pct_change, volume)
CategoryHandler = Callable[
    [
        "psycopg.Connection[tuple[Any, ...]]",
        str,
        str,
        date,
        list[tuple[int, str, int, int, int, float, int]],
    ],
    list[DigestEntry],
]

_HANDLERS: dict[str, CategoryHandler] = {}


def register_handler(category: str, handler: CategoryHandler) -> None:
    """Register a handler function for a category."""
    _HANDLERS[category] = handler


def get_handler(category: str) -> CategoryHandler:
    """Retrieve the registered handler for a category."""
    try:
        return _HANDLERS[category]
    except KeyError:
        raise KeyError(f"unknown insight category: {category!r}") from None


def available_categories() -> list[str]:
    """Currently registered category names."""
    return list(_HANDLERS)


# --------------------------------------------------------------------------- #
# Built-in category handlers
# --------------------------------------------------------------------------- #


def _get_price_window(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    item_id: int,
    sid: int,
    window_days: int = 14,
) -> tuple[list[float], list[float]]:
    """Fetch recent close_prices and volumes for analytics.

    Uses DailyRepo.get_daily_window from the existing repository layer.
    """
    from bdo_common.repositories import DailyRepo

    rows = DailyRepo.get_daily_window(
        conn, region=region, item_id=item_id, sid=sid, window_days=window_days
    )
    # Rows come in DESC order; reverse for chronological
    rows_asc = list(reversed(rows))
    close_prices = [float(r.close_price) for r in rows_asc]
    volumes = [float(r.total_trades_delta) for r in rows_asc]
    return close_prices, volumes


def _anomaly_flag(close_prices: list[float]) -> bool | None:
    """Whether the latest close is a statistical outlier over its window.

    Mirrors the ``/v1/market`` analysis anomaly (ADR-0016): needs at least
    ``MIN_POINTS`` daily closes to judge (else ``None``), then flags
    ``|z-score| > ANOMALY_Z`` via ``analytics.detect_anomaly``.
    """
    if len(close_prices) < MIN_POINTS:
        return None
    return bool(detect_anomaly(close_prices)["is_anomalous"])


def _handle_buff(
    conn: psycopg.Connection[tuple[Any, ...]],
    region: str,
    period: str,
    target_date: date,
    movers: list[tuple[int, str, int, int, int, float, int]],
) -> list[DigestEntry]:
    """Buff category: price/volatility/liquidity only, no enhancement cost."""
    entries: list[DigestEntry] = []
    for item_id, item_name, sid, close_price, prev_close, pct_change, volume in movers:
        volatility: float | None = None
        liquidity: float | None = None

        close_prices, volumes = _get_price_window(conn, region=region, item_id=item_id, sid=sid)
        if len(close_prices) >= 2:
            vol_stats = daily_volatility(close_prices)
            volatility = vol_stats["cv"]
        if volumes:
            liquidity = daily_liquidity(volumes)
        anomaly = _anomaly_flag(close_prices)

        entries.append(
            DigestEntry(
                item_id=item_id,
                item_name=item_name,
                category="buff",
                sid=sid,
                close_price=close_price,
                prev_close_price=prev_close,
                pct_change=pct_change,
                volume=volume,
                volatility=volatility,
                liquidity=liquidity,
                enhancement_cost_change=None,
                anomaly=anomaly,
            )
        )
    return entries


def _handle_accessory(
    conn: psycopg.Connection[tuple[Any, ...]],
    region: str,
    period: str,
    target_date: date,
    movers: list[tuple[int, str, int, int, int, float, int]],
) -> list[DigestEntry]:
    """Accessory category: price/vol/liq + enhancement-cost movement.

    ``enhancement_cost_change`` is the percent change in the expected silver to
    enhance the item up to its current ``sid`` (the ``(sid-1) -> sid`` transition
    under ``accessory_v1``), comparing the target date to the same prior
    reference the price move uses (the previous day for ``daily``; ~7 days back
    for ``weekly``). It is ``None`` for base items (``sid == 0``) or when either
    period lacks the prices the model needs. The figure is computed via
    ``pricing.enhancement_analysis`` so it matches the per-tier API model
    (ADR-0016).
    """
    entries: list[DigestEntry] = []
    for item_id, item_name, sid, close_price, prev_close, pct_change, volume in movers:
        volatility: float | None = None
        liquidity: float | None = None
        enhancement_cost_change: float | None = None

        close_prices, volumes = _get_price_window(conn, region=region, item_id=item_id, sid=sid)
        if len(close_prices) >= 2:
            vol_stats = daily_volatility(close_prices)
            volatility = vol_stats["cv"]
        if volumes:
            liquidity = daily_liquidity(volumes)
        anomaly = _anomaly_flag(close_prices)

        # Expected enhancement cost as of the target date vs the prior reference.
        # Price ladders are taken as-of each date so re-runs/backfills don't leak
        # future prices. (A handful of small queries per mover -- acceptable for a
        # once-daily batch job; not worth batching.)
        prices_now = _fetch_sid_prices_asof(
            conn, region=region, item_id=item_id, on_or_before=target_date
        )
        prices_now[sid] = float(close_price)
        if period == "weekly":
            prices_prev = _fetch_sid_prices_asof(
                conn, region=region, item_id=item_id, on_or_before=target_date - timedelta(days=7)
            )
        else:
            prices_prev = _fetch_sid_prices_asof(
                conn, region=region, item_id=item_id, on_or_before=target_date, strict=True
            )
        prices_prev[sid] = float(prev_close)

        cost_now = _expected_cost_to_reach(prices_now, sid)
        cost_prev = _expected_cost_to_reach(prices_prev, sid)
        if cost_now is not None and cost_prev:
            enhancement_cost_change = (cost_now - cost_prev) / cost_prev * 100.0

        entries.append(
            DigestEntry(
                item_id=item_id,
                item_name=item_name,
                category="accessory",
                sid=sid,
                close_price=close_price,
                prev_close_price=prev_close,
                pct_change=pct_change,
                volume=volume,
                volatility=volatility,
                liquidity=liquidity,
                enhancement_cost_change=enhancement_cost_change,
                anomaly=anomaly,
            )
        )
    return entries


def _fetch_sid_prices_asof(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    item_id: int,
    on_or_before: date,
    strict: bool = False,
) -> dict[int, float]:
    """Latest ``close_price`` per sid as of a date.

    Returns ``{sid: close_price}`` using each sid's most recent row on or before
    ``on_or_before`` (strictly before it when ``strict`` is set). Date-bounding
    keeps historical/backfill digests from using prices newer than their date.
    """
    if strict:
        sql = """
            SELECT DISTINCT ON (sid) sid, close_price
            FROM market_daily
            WHERE region = %s AND item_id = %s AND trade_date < %s
            ORDER BY sid, trade_date DESC
        """
    else:
        sql = """
            SELECT DISTINCT ON (sid) sid, close_price
            FROM market_daily
            WHERE region = %s AND item_id = %s AND trade_date <= %s
            ORDER BY sid, trade_date DESC
        """
    rows: Sequence[tuple[Any, ...]] = conn.execute(sql, (region, item_id, on_or_before)).fetchall()
    return {int(r[0]): float(r[1]) for r in rows}


def _expected_cost_to_reach(prices: dict[int, float], sid: int) -> float | None:
    """Expected silver to enhance up to ``sid`` (the ``(sid-1) -> sid`` step).

    Returns ``None`` for base items (``sid <= 0``) or when the ladder lacks the
    clean (``sid 0``) or target price the model needs. Reuses
    ``pricing.enhancement_analysis`` so the figure matches the per-tier API
    model (ADR-0016).
    """
    if sid <= 0 or 0 not in prices or sid not in prices:
        return None
    try:
        analysis = enhancement_analysis(prices, model_id="accessory_v1")
    except (KeyError, ValueError):
        return None
    for t in analysis["transitions"]:
        if t["sid_to"] == sid:
            return float(t["expected_cost"])
    return None


# Register built-in handlers
register_handler("buff", _handle_buff)
register_handler("accessory", _handle_accessory)
