"""Tests for bdo_common.insights.models: instantiation and serialization."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from bdo_common.insights.models import (
    DigestEntry,
    DigestStats,
    MarketDigest,
    MarketSummary,
    MoverRef,
    Narrative,
    NarrativeCategory,
)


def _entry(*, pct_change: float = 11.1, anomaly: bool | None = None) -> DigestEntry:
    """Minimal DigestEntry for trend/anomaly assertions."""
    return DigestEntry(
        item_id=1,
        item_name="Item",
        category="buff",
        sid=0,
        close_price=100,
        prev_close_price=90,
        pct_change=pct_change,
        volume=50,
        volatility=0.05,
        liquidity=50.0,
        enhancement_cost_change=None,
        anomaly=anomaly,
    )


class TestDigestEntry:
    """DigestEntry model tests."""

    def test_instantiation(self) -> None:
        entry = DigestEntry(
            item_id=11608,
            item_name="Deboreka Necklace",
            category="accessory",
            sid=3,
            close_price=500_000_000,
            prev_close_price=480_000_000,
            pct_change=4.17,
            volume=1200,
            volatility=0.05,
            liquidity=800.0,
            enhancement_cost_change=0.12,
        )
        assert entry.item_id == 11608
        assert entry.item_name == "Deboreka Necklace"
        assert entry.category == "accessory"
        assert entry.sid == 3
        assert entry.close_price == 500_000_000
        assert entry.prev_close_price == 480_000_000
        assert entry.pct_change == 4.17
        assert entry.volume == 1200
        assert entry.volatility == 0.05
        assert entry.liquidity == 800.0
        assert entry.enhancement_cost_change == 0.12

    def test_optional_fields_none(self) -> None:
        entry = DigestEntry(
            item_id=1,
            item_name="Item",
            category="buff",
            sid=0,
            close_price=100,
            prev_close_price=90,
            pct_change=11.1,
            volume=50,
            volatility=None,
            liquidity=None,
            enhancement_cost_change=None,
        )
        assert entry.volatility is None
        assert entry.liquidity is None
        assert entry.enhancement_cost_change is None
        # anomaly is optional and defaults to None when not supplied
        assert entry.anomaly is None

    def test_trend_is_computed_from_pct_change(self) -> None:
        """``trend`` is derived from pct_change and serialised in the output."""
        up = _entry(pct_change=5.0)
        down = _entry(pct_change=-5.0)
        flat = _entry(pct_change=0.0)
        assert up.trend == "up"
        assert down.trend == "down"
        assert flat.trend == "flat"
        # It appears in the serialised payload the LLM/API consume.
        assert up.model_dump()["trend"] == "up"
        assert json.loads(up.model_dump_json())["trend"] == "up"

    def test_anomaly_roundtrips(self) -> None:
        entry = _entry(pct_change=20.0, anomaly=True)
        restored = DigestEntry.model_validate_json(entry.model_dump_json())
        assert restored.anomaly is True
        assert restored == entry

    def test_frozen(self) -> None:
        entry = DigestEntry(
            item_id=1,
            item_name="Item",
            category="buff",
            sid=0,
            close_price=100,
            prev_close_price=90,
            pct_change=11.1,
            volume=50,
            volatility=None,
            liquidity=None,
            enhancement_cost_change=None,
        )
        import pydantic
        import pytest

        with pytest.raises(pydantic.ValidationError):
            entry.item_id = 2  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        entry = DigestEntry(
            item_id=11608,
            item_name="Deboreka Necklace",
            category="accessory",
            sid=3,
            close_price=500_000_000,
            prev_close_price=480_000_000,
            pct_change=4.17,
            volume=1200,
            volatility=0.05,
            liquidity=800.0,
            enhancement_cost_change=0.12,
        )
        data = entry.model_dump()
        restored = DigestEntry.model_validate(data)
        assert restored == entry


class TestMarketDigest:
    """MarketDigest model tests."""

    def test_instantiation_empty_entries(self) -> None:
        digest = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        assert digest.region == "tw"
        assert digest.period == "daily"
        assert digest.summary_date == date(2026, 3, 15)
        assert digest.top_n == 5
        assert digest.entries == []

    def test_instantiation_with_entries(self) -> None:
        entry = DigestEntry(
            item_id=1,
            item_name="Item",
            category="buff",
            sid=0,
            close_price=100,
            prev_close_price=90,
            pct_change=11.1,
            volume=50,
            volatility=None,
            liquidity=None,
            enhancement_cost_change=None,
        )
        digest = MarketDigest(
            region="na",
            period="weekly",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[entry],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        assert len(digest.entries) == 1
        assert digest.entries[0].item_id == 1

    def test_json_roundtrip(self) -> None:
        digest = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        json_str = digest.model_dump_json()
        restored = MarketDigest.model_validate_json(json_str)
        assert restored == digest

    def test_stats_defaults_none_and_roundtrips(self) -> None:
        """stats defaults to None (so pre-stats digests validate) and roundtrips."""
        empty = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        assert empty.stats is None
        # A digest persisted before this field (no "stats" key) still validates.
        legacy = empty.model_dump(mode="json")
        legacy.pop("stats", None)
        assert MarketDigest.model_validate(legacy).stats is None

        stats = DigestStats(
            total=1,
            gainers=1,
            losers=0,
            flat=0,
            anomalies=0,
            top_gainer=MoverRef(item_name="Item", sid=0, value=11.1),
        )
        with_stats = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            stats=stats,
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        restored = MarketDigest.model_validate_json(with_stats.model_dump_json())
        assert restored == with_stats
        assert restored.stats is not None
        assert restored.stats.top_gainer == MoverRef(item_name="Item", sid=0, value=11.1)


class TestDigestStats:
    """DigestStats model tests."""

    def test_instantiation_with_defaults(self) -> None:
        """Superlative refs default to None so a digest with no clear mover validates."""
        stats = DigestStats(total=3, gainers=2, losers=1, flat=0, anomalies=0)
        assert stats.total == 3
        assert stats.top_gainer is None
        assert stats.most_traded is None


class TestNarrativeCategory:
    """NarrativeCategory model tests."""

    def test_instantiation(self) -> None:
        cat = NarrativeCategory(
            category="accessory",
            bullets=["Item A: +5.0% to 100,000", "Item B: -2.0% to 80,000"],
        )
        assert cat.category == "accessory"
        assert len(cat.bullets) == 2


class TestNarrative:
    """Narrative model tests."""

    def test_instantiation(self) -> None:
        narrative = Narrative(
            headline="Market summary for tw (daily) - 2026-03-15",
            categories=[
                NarrativeCategory(category="buff", bullets=["Bullet 1"]),
            ],
            overall="1 items tracked across 1 categories: 1 gainers, 0 losers.",
        )
        assert "tw" in narrative.headline
        assert len(narrative.categories) == 1
        assert narrative.overall != ""

    def test_json_roundtrip(self) -> None:
        narrative = Narrative(
            headline="Test headline",
            categories=[],
            overall="No data.",
        )
        json_str = narrative.model_dump_json()
        restored = Narrative.model_validate_json(json_str)
        assert restored == narrative


class TestMarketSummary:
    """MarketSummary model tests."""

    def test_instantiation(self) -> None:
        digest = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        narrative = Narrative(
            headline="Test headline",
            categories=[],
            overall="No data.",
        )
        summary = MarketSummary(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="deterministic_v1",
            digest=digest,
            narrative=narrative,
            created_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        assert summary.region == "tw"
        assert summary.model_id == "deterministic_v1"
        assert summary.digest == digest
        assert summary.narrative == narrative

    def test_json_roundtrip(self) -> None:
        digest = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        narrative = Narrative(
            headline="Test",
            categories=[],
            overall="Test.",
        )
        summary = MarketSummary(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="deterministic_v1",
            digest=digest,
            narrative=narrative,
            created_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        json_str = summary.model_dump_json()
        restored = MarketSummary.model_validate_json(json_str)
        assert restored == summary
