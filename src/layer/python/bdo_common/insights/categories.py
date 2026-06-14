"""Category registry for market-insights digest construction.

Mirrors the pricing.py register_model/get_model/available_models pattern
(ADR-0012). Each category handler enriches raw mover tuples into DigestEntry
objects with category-specific analytics.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from typing import TYPE_CHECKING, Any

from bdo_common.analytics import daily_liquidity, daily_volatility
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
    """Accessory category: price/vol/liq + enhancement cost movement."""
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

        # Enhancement cost analysis: build prices map from current close
        # We use close_price as the current price for this sid
        prices: dict[int, float] = {sid: float(close_price)}
        # Attempt to get sid=0 price for clean cost
        _fetch_sid_prices(conn, region=region, item_id=item_id, prices=prices)
        # Re-apply the mover's authoritative close to prevent stale overwrite
        prices[sid] = float(close_price)

        if 0 in prices and sid in prices:
            try:
                analysis = enhancement_analysis(prices, model_id="accessory_v1")
                transitions = analysis.get("transitions", [])
                # Find the transition ending at this sid
                for t in transitions:
                    if t["sid_to"] == sid:
                        # enhancement_cost_change is the ROI as a proxy
                        enhancement_cost_change = float(t["roi"])
                        break
            except (KeyError, ValueError):
                pass

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
            )
        )
    return entries


def _fetch_sid_prices(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    item_id: int,
    prices: dict[int, float],
) -> None:
    """Fetch the latest close_price for all sids of an item into prices dict."""
    sql = """
        SELECT DISTINCT ON (sid) sid, close_price
        FROM market_daily
        WHERE region = %s AND item_id = %s
        ORDER BY sid, trade_date DESC
    """
    rows: Sequence[tuple[Any, ...]] = conn.execute(sql, (region, item_id)).fetchall()
    for r in rows:
        prices[int(r[0])] = float(r[1])


# Register built-in handlers
register_handler("buff", _handle_buff)
register_handler("accessory", _handle_accessory)
