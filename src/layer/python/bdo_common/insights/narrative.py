"""Deterministic narrative renderer.

Builds a Narrative from a MarketDigest with exact, human-readable bullets. Under
the hybrid design (ADR-0016) these bullets are ALWAYS used -- the LLM only
supplies the headline + overall -- so the per-item figures never depend on the
model. Also serves as the full fallback when the LLM is skipped or fails.
"""

from __future__ import annotations

from collections import defaultdict

from bdo_common.insights.models import (
    DigestEntry,
    MarketDigest,
    Narrative,
    NarrativeCategory,
)

#: |pct_change| below this reads as "flat" once rounded to one decimal.
_FLAT_EPSILON = 0.05


def _silver(value: int) -> str:
    """Compact, rounded silver: 675000 -> '675k', 1_094_649_579 -> '1.09b'."""
    n = abs(value)
    if n >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}b"
    if n >= 1_000_000:
        return f"{value / 1_000_000:.2f}m"
    if n >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


def _label(entry: DigestEntry) -> str:
    """Human label for an entry. Accessories read as enhancement tiers."""
    if entry.category == "accessory":
        return f"{entry.item_name} (tier {entry.sid})"
    if entry.sid:
        return f"{entry.item_name} (SID {entry.sid})"
    return entry.item_name


def _bullet(entry: DigestEntry) -> str:
    """One exact, readable bullet for an entry."""
    if abs(entry.pct_change) < _FLAT_EPSILON:
        move = f"flat at {_silver(entry.close_price)}"
    else:
        move = f"{entry.pct_change:+.1f}% to {_silver(entry.close_price)}"
    bullet = f"{_label(entry)}: {move}"
    if entry.anomaly:
        bullet += " - unusual move"
    if entry.enhancement_cost_change is not None:
        bullet += f"; enhancing to tier {entry.sid} {entry.enhancement_cost_change:+.1f}%"
    return bullet


def render_narrative(digest: MarketDigest) -> Narrative:
    """Render a deterministic Narrative from a MarketDigest.

    Groups entries by category into exact per-item bullets, and produces a
    headline + overall (used as the fallback when the LLM does not supply them).
    """
    by_category: dict[str, list[DigestEntry]] = defaultdict(list)
    for entry in digest.entries:
        by_category[entry.category].append(entry)

    categories = [
        NarrativeCategory(category=cat_name, bullets=[_bullet(e) for e in entries])
        for cat_name, entries in by_category.items()
    ]

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

    return Narrative(headline=headline, categories=categories, overall=overall)
