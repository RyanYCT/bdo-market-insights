"""Deterministic narrative renderer (fallback, no LLM).

Produces a valid Narrative from a MarketDigest by grouping entries by category
and rendering human-readable bullets summarizing each entry's price movement.
"""

from __future__ import annotations

from collections import defaultdict

from bdo_common.insights.models import (
    DigestEntry,
    MarketDigest,
    Narrative,
    NarrativeCategory,
)


def render_narrative(digest: MarketDigest) -> Narrative:
    """Render a deterministic Narrative from a MarketDigest.

    Groups entries by category, generates a headline, creates bullets per
    category summarizing each entry's movement, and produces an overall
    summary sentence.
    """
    # Group entries by category
    by_category: dict[str, list[DigestEntry]] = defaultdict(list)
    for entry in digest.entries:
        by_category[entry.category].append(entry)

    # Build category sections
    categories: list[NarrativeCategory] = []
    for cat_name, entries in by_category.items():
        bullets: list[str] = []
        for entry in entries:
            bullet = (
                f"{entry.item_name} (SID {entry.sid}): "
                f"{entry.pct_change:+.1f}% to {entry.close_price:,}"
            )
            # Surface the category-specific enrichment when present, so the
            # deterministic fallback isn't purely a price ticker.
            if entry.enhancement_cost_change is not None:
                bullet += f"; enhance cost {entry.enhancement_cost_change:+.1f}%"
            if entry.volatility is not None:
                bullet += f"; volatility {entry.volatility:.2f}"
            if entry.anomaly:
                bullet += "; unusual move"
            bullets.append(bullet)
        categories.append(NarrativeCategory(category=cat_name, bullets=bullets))

    # Headline
    headline = (
        f"Market summary for {digest.region} ({digest.period}) - {digest.summary_date.isoformat()}"
    )

    # Overall summary: breadth from the entries, plus the precomputed
    # superlatives/anomaly count from digest.stats when available.
    total_entries = len(digest.entries)
    if total_entries == 0:
        overall = "No significant market movements detected."
    else:
        gainers = sum(1 for e in digest.entries if e.pct_change > 0)
        losers = sum(1 for e in digest.entries if e.pct_change < 0)
        flat = sum(1 for e in digest.entries if e.pct_change == 0)
        parts = [
            f"{total_entries} items across {len(by_category)} categories: "
            f"{gainers} up, {losers} down, {flat} flat."
        ]
        stats = digest.stats
        if stats is not None:
            if stats.top_gainer is not None:
                parts.append(
                    f"Top gainer: {stats.top_gainer.item_name} {stats.top_gainer.value:+.1f}%."
                )
            if stats.top_loser is not None:
                parts.append(
                    f"Top loser: {stats.top_loser.item_name} {stats.top_loser.value:+.1f}%."
                )
            if stats.anomalies:
                parts.append(f"{stats.anomalies} unusual move(s) flagged.")
        overall = " ".join(parts)

    return Narrative(
        headline=headline,
        categories=categories,
        overall=overall,
    )
