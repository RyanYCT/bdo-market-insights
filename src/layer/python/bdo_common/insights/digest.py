"""Build a structured MarketDigest from the database.

Orchestrates the category registry and InsightRepo to produce the deterministic
digest that feeds both the LLM narrative and the fallback renderer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from bdo_common.insights.categories import available_categories, get_handler
from bdo_common.insights.models import (
    DigestEntry,
    DigestStats,
    MarketDigest,
    MoverRef,
    Period,
)
from bdo_common.insights.repositories import InsightRepo

if TYPE_CHECKING:
    import psycopg


def _compute_stats(entries: list[DigestEntry]) -> DigestStats | None:
    """Summarise a set of entries: breadth counts + notable superlatives.

    All values are derived from the entries themselves (ADR-0016) so the LLM
    narrator gets explicit headline material and never has to rank or invent it.
    Returns ``None`` for an empty digest.
    """
    if not entries:
        return None

    def _ref(entry: DigestEntry, value: float) -> MoverRef:
        return MoverRef(item_name=entry.item_name, sid=entry.sid, value=value)

    top_gainer_e = max(entries, key=lambda e: e.pct_change)
    top_loser_e = min(entries, key=lambda e: e.pct_change)
    most_traded_e = max(entries, key=lambda e: e.volume)

    # Only label a genuine move -- don't call the least-bad decline a "gainer".
    top_gainer = (
        _ref(top_gainer_e, top_gainer_e.pct_change) if top_gainer_e.pct_change > 0 else None
    )
    top_loser = _ref(top_loser_e, top_loser_e.pct_change) if top_loser_e.pct_change < 0 else None

    most_volatile: MoverRef | None = None
    vol_entries = [e for e in entries if e.volatility is not None]
    if vol_entries:
        mv = max(vol_entries, key=lambda e: e.volatility or 0.0)
        mv_value = mv.volatility
        if mv_value is not None:
            most_volatile = _ref(mv, mv_value)

    return DigestStats(
        total=len(entries),
        gainers=sum(1 for e in entries if e.pct_change > 0),
        losers=sum(1 for e in entries if e.pct_change < 0),
        flat=sum(1 for e in entries if e.pct_change == 0),
        anomalies=sum(1 for e in entries if e.anomaly),
        top_gainer=top_gainer,
        top_loser=top_loser,
        most_volatile=most_volatile,
        most_traded=_ref(most_traded_e, float(most_traded_e.volume)),
    )


def build_digest(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    period: Period,
    target_date: date,
    top_n: int = 5,
) -> MarketDigest:
    """Build a MarketDigest by iterating all registered categories.

    For each category, fetches the top movers from the database via
    InsightRepo.top_movers, then passes them through the category handler
    to produce enriched DigestEntry objects.
    """
    all_entries: list[DigestEntry] = []

    for category in available_categories():
        movers = InsightRepo.top_movers(
            conn,
            region=region,
            category=category,
            period=period,
            target_date=target_date,
            limit=top_n,
        )
        if not movers:
            continue

        handler = get_handler(category)
        entries = handler(conn, region, period, target_date, movers)
        all_entries.extend(entries)

    return MarketDigest(
        region=region,
        period=period,
        summary_date=target_date,
        top_n=top_n,
        entries=all_entries,
        stats=_compute_stats(all_entries),
        generated_at=datetime.now(tz=UTC),
    )
