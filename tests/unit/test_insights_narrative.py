"""Tests for bdo_common.insights.narrative: render_narrative pure function."""

from __future__ import annotations

from datetime import UTC, date, datetime

from bdo_common.insights.models import DigestEntry, MarketDigest, Narrative, NarrativeCategory
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
        enhancement_cost_change=None,
    )


def _make_digest(entries: list[DigestEntry] | None = None) -> MarketDigest:
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 3, 15),
        top_n=5,
        entries=entries or [],
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
        assert "500,000,000" in narrative.categories[0].bullets[0]

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
        """Overall summary correctly counts gainers and losers."""
        entries = [
            _make_entry(item_id=1, pct_change=10.0),
            _make_entry(item_id=2, pct_change=-5.0),
            _make_entry(item_id=3, pct_change=3.0),
            _make_entry(item_id=4, pct_change=-1.0),
            _make_entry(item_id=5, pct_change=0.0),  # neither gainer nor loser
        ]
        digest = _make_digest(entries)
        narrative = render_narrative(digest)

        assert "5 items tracked" in narrative.overall
        assert "1 categories" in narrative.overall
        assert "2 gainers" in narrative.overall
        assert "2 losers" in narrative.overall

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
