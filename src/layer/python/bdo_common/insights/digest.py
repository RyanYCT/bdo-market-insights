"""Build a structured MarketDigest from the database.

Orchestrates the category registry and InsightRepo to produce the deterministic
digest that feeds both the LLM narrative and the fallback renderer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from bdo_common.insights.categories import available_categories, get_handler
from bdo_common.insights.models import DigestEntry, MarketDigest
from bdo_common.insights.repositories import InsightRepo

if TYPE_CHECKING:
    import psycopg


def build_digest(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    period: str,
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
        generated_at=datetime.now(tz=UTC),
    )
