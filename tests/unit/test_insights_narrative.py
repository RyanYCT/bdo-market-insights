"""Tests for bdo_common.insights.narrative: render_narrative pure function."""

from __future__ import annotations

from datetime import UTC, date, datetime

from bdo_common.insights.models import (
    DigestEntry,
    DigestStats,
    MarketDigest,
    MoverRef,
    Narrative,
    NarrativeCategory,
)
from bdo_common.insights.narrative import render_narrative


def _make_entry(
    *,
    item_id: int = 1,
    item_name: str = "Test Item",
    category: str = "buff",
    sid: int = 0,
    close_price: int = 100,
    prev_close_price: int = 90,
    pct_change: float = 11.1,
    volume: int = 50,
    anomaly: bool | None = None,
    enhancement_cost_change: float | None = None,
) -> DigestEntry:
    return DigestEntry(
        item_id=item_id,
        item_name=item_name,
        category=category,
        sid=sid,
        close_price=close_price,
        prev_close_price=prev_close_price,
        pct_change=pct_change,
        volume=volume,
        volatility=0.05,
        liquidity=50.0,
        enhancement_cost_change=enhancement_cost_change,
        anomaly=anomaly,
    )


def _make_digest(
    entries: list[DigestEntry] | None = None,
    *,
    stats: DigestStats | None = None,
) -> MarketDigest:
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 3, 15),
        top_n=5,
        entries=entries or [],
        stats=stats,
        generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
    )


class TestRenderNarrative:
    """Test the deterministic narrative renderer."""

    def test_empty_digest_produces_no_movements(self) -> None:
        """Empty digest produces no-movements overall message."""
        digest = _make_digest([])
        narrative = render_narrative(digest)

        assert isinstance(narrative, Narrative)
        assert "tw" in narrative.headline
        assert "daily" in narrative.headline
        assert "2026-03-15" in narrative.headline
        assert narrative.categories == []
        assert narrative.overall == "No significant market movements detected."

    def test_single_entry_single_category(self) -> None:
        """Single entry produces one category with one bullet."""
        entry = _make_entry(item_name="Deboreka Necklace", close_price=500_000_000)
        digest = _make_digest([entry])
        narrative = render_narrative(digest)

        assert len(narrative.categories) == 1
        assert narrative.categories[0].category == "buff"
        assert len(narrative.categories[0].bullets) == 1
        assert "Deboreka Necklace" in narrative.categories[0].bullets[0]
        assert "+11.1%" in narrative.categories[0].bullets[0]
        # Silver is rendered compactly (500,000,000 -> 500.00m).
        assert "500.00m" in narrative.categories[0].bullets[0]

    def test_accessory_bullet_uses_tier_label_and_enhancement(self) -> None:
        """Accessory bullets read as tiers, show compact silver, the anomaly
        marker, and the exact enhancement-cost move."""
        entry = _make_entry(
            item_name="Ring of Cadry",
            category="accessory",
            sid=3,
            pct_change=-12.0,
            close_price=1_094_649_579,
            anomaly=True,
            enhancement_cost_change=0.7142857,
        )
        bullet = render_narrative(_make_digest([entry])).categories[0].bullets[0]

        assert "Ring of Cadry (tier 3)" in bullet
        assert "-12.0% to 1.09b" in bullet
        assert "unusual move" in bullet
        assert "enhancing to tier 3 +0.7%" in bullet

    def test_flat_entry_renders_flat_with_enhancement(self) -> None:
        """A flat-price tier still surfaces its enhancement-cost move exactly."""
        entry = _make_entry(
            item_name="Ring of Cadry",
            category="accessory",
            sid=1,
            pct_change=0.0,
            close_price=259_149_995,
            enhancement_cost_change=5.0,
        )
        bullet = render_narrative(_make_digest([entry])).categories[0].bullets[0]

        assert "flat at 259.15m" in bullet
        assert "enhancing to tier 1 +5.0%" in bullet

    def test_multiple_categories(self) -> None:
        """Multiple categories are grouped correctly."""
        entries = [
            _make_entry(item_id=1, item_name="Buff Item", category="buff", pct_change=5.0),
            _make_entry(item_id=2, item_name="Acc Item", category="accessory", pct_change=-3.0),
        ]
        digest = _make_digest(entries)
        narrative = render_narrative(digest)

        assert len(narrative.categories) == 2
        cat_names = [c.category for c in narrative.categories]
        assert "buff" in cat_names
        assert "accessory" in cat_names

    def test_multiple_entries_same_category(self) -> None:
        """Multiple entries in the same category produce multiple bullets."""
        entries = [
            _make_entry(item_id=1, item_name="Item A", category="buff", pct_change=10.0),
            _make_entry(item_id=2, item_name="Item B", category="buff", pct_change=-5.5),
        ]
        digest = _make_digest(entries)
        narrative = render_narrative(digest)

        assert len(narrative.categories) == 1
        assert narrative.categories[0].category == "buff"
        assert len(narrative.categories[0].bullets) == 2

    def test_overall_summary_counts(self) -> None:
        """Overall summary correctly counts gainers, losers, and flat."""
        entries = [
            _make_entry(item_id=1, pct_change=10.0),
            _make_entry(item_id=2, pct_change=-5.0),
            _make_entry(item_id=3, pct_change=3.0),
            _make_entry(item_id=4, pct_change=-1.0),
            _make_entry(item_id=5, pct_change=0.0),  # flat
        ]
        digest = _make_digest(entries)
        narrative = render_narrative(digest)

        assert "5 items across" in narrative.overall
        assert "1 categories" in narrative.overall
        assert "2 up" in narrative.overall
        assert "2 down" in narrative.overall
        assert "1 flat" in narrative.overall

    def test_overall_uses_stats_superlatives(self) -> None:
        """When digest.stats is present, overall surfaces the superlatives."""
        entries = [
            _make_entry(item_id=1, item_name="Rocket", pct_change=12.5, anomaly=True),
            _make_entry(item_id=2, item_name="Dropper", pct_change=-8.0),
        ]
        stats = DigestStats(
            total=2,
            gainers=1,
            losers=1,
            flat=0,
            anomalies=1,
            top_gainer=MoverRef(item_name="Rocket", sid=0, value=12.5),
            top_loser=MoverRef(item_name="Dropper", sid=0, value=-8.0),
            most_volatile=None,
            most_traded=MoverRef(item_name="Rocket", sid=0, value=50.0),
        )
        narrative = render_narrative(_make_digest(entries, stats=stats))

        assert "Top gainer: Rocket +12.5%" in narrative.overall
        assert "Top loser: Dropper -8.0%" in narrative.overall
        assert "1 unusual move(s) flagged" in narrative.overall

    def test_anomaly_marked_in_bullet(self) -> None:
        """An entry flagged as an anomaly gets an 'unusual move' marker."""
        entry = _make_entry(item_name="Spiker", pct_change=30.0, anomaly=True)
        narrative = render_narrative(_make_digest([entry]))

        bullet = narrative.categories[0].bullets[0]
        assert "unusual move" in bullet

    def test_no_anomaly_marker_when_not_flagged(self) -> None:
        """A normal entry has no 'unusual move' marker."""
        entry = _make_entry(anomaly=False)
        narrative = render_narrative(_make_digest([entry]))

        assert "unusual move" not in narrative.categories[0].bullets[0]

    def test_headline_format(self) -> None:
        """Headline includes region, period, and date."""
        digest = MarketDigest(
            region="na",
            period="weekly",
            summary_date=date(2026, 6, 1),
            top_n=3,
            entries=[],
            generated_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        )
        narrative = render_narrative(digest)

        assert narrative.headline == "Market summary for na (weekly) - 2026-06-01"

    def test_negative_pct_change_formatting(self) -> None:
        """Negative pct_change is formatted with minus sign."""
        entry = _make_entry(
            item_name="Falling Item", pct_change=-12.3, close_price=42_000_000, sid=2
        )
        digest = _make_digest([entry])
        narrative = render_narrative(digest)

        bullet = narrative.categories[0].bullets[0]
        assert "-12.3%" in bullet
        assert "SID 2" in bullet

    def test_return_type_is_narrative(self) -> None:
        """render_narrative returns a valid Narrative model."""
        digest = _make_digest([_make_entry()])
        narrative = render_narrative(digest)

        assert isinstance(narrative, Narrative)
        assert isinstance(narrative.categories[0], NarrativeCategory)
        # Verify it serializes cleanly
        json_str = narrative.model_dump_json()
        restored = Narrative.model_validate_json(json_str)
        assert restored == narrative
