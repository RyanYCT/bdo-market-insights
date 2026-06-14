"""Integration tests for the Phase 1 insights digest engine.

Exercises InsightRepo.top_movers, build_digest, render_narrative, and
SummaryRepo against a real Postgres database with the schema built via Alembic
migrations up to 0004. Skipped unless ``TEST_DATABASE_URL`` is set.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest

from bdo_common.insights.digest import build_digest
from bdo_common.insights.models import MarketDigest, MarketSummary, Narrative
from bdo_common.insights.narrative import render_narrative
from bdo_common.insights.repositories import InsightRepo, SummaryRepo

pytestmark = pytest.mark.integration


def _seed_item(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    item_id: int,
    name: str,
    category: str,
) -> None:
    """Insert a row into the item table."""
    conn.execute(
        "INSERT INTO item (id, name, category) VALUES (%s, %s, %s)",
        (item_id, name, category),
    )


def _seed_daily(
    conn: psycopg.Connection[tuple[Any, ...]],
    *,
    region: str,
    trade_date: date,
    item_id: int,
    sid: int,
    close_price: int,
    total_trades_delta: int = 100,
) -> None:
    """Insert a row into market_daily with sensible defaults for OHLC.

    ``market_daily`` has a foreign key to ``item_sid`` (region, item_id, sid),
    so ensure that parent reference row exists first.
    """
    conn.execute(
        """
        INSERT INTO item_sid (region, item_id, sid, max_enhance, price_min, price_max)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (region, item_id, sid) DO NOTHING
        """,
        (region, item_id, sid, 5, 1, 1_000_000_000_000),
    )
    conn.execute(
        """
        INSERT INTO market_daily
            (region, trade_date, item_id, sid, open_price, high_price, low_price,
             close_price, avg_price, total_trades_delta, avg_stock, snapshot_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            region,
            trade_date,
            item_id,
            sid,
            close_price - 10,  # open
            close_price + 10,  # high
            close_price - 20,  # low
            close_price,
            close_price - 5,  # avg
            total_trades_delta,
            50,  # avg_stock
            24,  # snapshot_count
        ),
    )


class TestInsightRepoIntegration:
    """Integration test for InsightRepo.top_movers."""

    def test_top_movers_daily(self, db_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
        """top_movers returns items sorted by |pct_change| DESC for daily period."""
        _seed_item(db_conn, item_id=1, name="Item A", category="buff")
        _seed_item(db_conn, item_id=2, name="Item B", category="buff")

        # Prior day data
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 14),
            item_id=1,
            sid=0,
            close_price=100,
        )
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 14),
            item_id=2,
            sid=0,
            close_price=200,
        )

        # Target day data
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 15),
            item_id=1,
            sid=0,
            close_price=120,  # +20%
        )
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 15),
            item_id=2,
            sid=0,
            close_price=180,  # -10%
        )

        rows = InsightRepo.top_movers(
            db_conn,
            region="tw",
            category="buff",
            period="daily",
            target_date=date(2026, 3, 15),
            limit=5,
        )

        assert len(rows) == 2
        # Sorted by |pct_change| DESC: +20% first, then -10%
        assert rows[0][1] == "Item A"
        assert rows[0][5] == pytest.approx(20.0, rel=0.01)
        assert rows[1][1] == "Item B"
        assert rows[1][5] == pytest.approx(-10.0, rel=0.01)

    def test_top_movers_weekly(self, db_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
        """top_movers compares target_date to a row >= 7 days prior for weekly."""
        _seed_item(db_conn, item_id=1, name="Item A", category="buff")

        # A row 7 days before the target (the weekly reference point).
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 8),
            item_id=1,
            sid=0,
            close_price=100,
        )
        # A nearer row (within the 7-day window) that must NOT be used as prior.
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 13),
            item_id=1,
            sid=0,
            close_price=130,
        )
        # Target day.
        _seed_daily(
            db_conn,
            region="tw",
            trade_date=date(2026, 3, 15),
            item_id=1,
            sid=0,
            close_price=150,
        )

        rows = InsightRepo.top_movers(
            db_conn,
            region="tw",
            category="buff",
            period="weekly",
            target_date=date(2026, 3, 15),
            limit=5,
        )

        assert len(rows) == 1
        # Compared against the 2026-03-08 close (100), not the 2026-03-13 close:
        # (150 - 100) / 100 * 100 = +50%.
        assert rows[0][4] == 100  # prev_close_price
        assert rows[0][5] == pytest.approx(50.0, rel=0.01)

    def test_top_movers_respects_limit(self, db_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
        """top_movers limits the number of results returned."""
        for i in range(1, 6):
            _seed_item(db_conn, item_id=i, name=f"Item {i}", category="accessory")
            _seed_daily(
                db_conn,
                region="tw",
                trade_date=date(2026, 3, 14),
                item_id=i,
                sid=0,
                close_price=100,
            )
            _seed_daily(
                db_conn,
                region="tw",
                trade_date=date(2026, 3, 15),
                item_id=i,
                sid=0,
                close_price=100 + i * 10,
            )

        rows = InsightRepo.top_movers(
            db_conn,
            region="tw",
            category="accessory",
            period="daily",
            target_date=date(2026, 3, 15),
            limit=3,
        )
        assert len(rows) == 3

    def test_top_movers_filters_by_category(
        self, db_conn: psycopg.Connection[tuple[Any, ...]]
    ) -> None:
        """top_movers only returns items matching the requested category."""
        _seed_item(db_conn, item_id=1, name="Buff Item", category="buff")
        _seed_item(db_conn, item_id=2, name="Acc Item", category="accessory")

        for item_id in (1, 2):
            _seed_daily(
                db_conn,
                region="tw",
                trade_date=date(2026, 3, 14),
                item_id=item_id,
                sid=0,
                close_price=100,
            )
            _seed_daily(
                db_conn,
                region="tw",
                trade_date=date(2026, 3, 15),
                item_id=item_id,
                sid=0,
                close_price=150,
            )

        buff_rows = InsightRepo.top_movers(
            db_conn,
            region="tw",
            category="buff",
            period="daily",
            target_date=date(2026, 3, 15),
        )
        assert len(buff_rows) == 1
        assert buff_rows[0][1] == "Buff Item"


class TestBuildDigestIntegration:
    """Integration test for build_digest."""

    def test_build_digest_produces_market_digest(
        self, db_conn: psycopg.Connection[tuple[Any, ...]]
    ) -> None:
        """build_digest produces a valid MarketDigest from seeded data."""
        _seed_item(db_conn, item_id=1, name="Buff Item", category="buff")

        # Seed enough daily data for volatility/liquidity (14 days)
        for i in range(14):
            _seed_daily(
                db_conn,
                region="tw",
                trade_date=date(2026, 3, 1) + timedelta(days=i),
                item_id=1,
                sid=0,
                close_price=100 + i * 5,
                total_trades_delta=50 + i * 2,
            )

        result = build_digest(
            db_conn,
            region="tw",
            period="daily",
            target_date=date(2026, 3, 14),
            top_n=5,
        )

        assert isinstance(result, MarketDigest)
        assert result.region == "tw"
        assert result.period == "daily"
        # Should have entries from the buff category
        assert len(result.entries) >= 1
        assert result.entries[0].category == "buff"
        assert result.entries[0].item_name == "Buff Item"


class TestRenderNarrativeIntegration:
    """Integration test for render_narrative."""

    def test_render_narrative_from_digest(
        self, db_conn: psycopg.Connection[tuple[Any, ...]]
    ) -> None:
        """render_narrative produces a Narrative from a real digest."""
        _seed_item(db_conn, item_id=1, name="Buff Item", category="buff")

        for i in range(14):
            _seed_daily(
                db_conn,
                region="tw",
                trade_date=date(2026, 3, 1) + timedelta(days=i),
                item_id=1,
                sid=0,
                close_price=100 + i * 5,
                total_trades_delta=50 + i * 2,
            )

        digest = build_digest(
            db_conn,
            region="tw",
            period="daily",
            target_date=date(2026, 3, 14),
            top_n=5,
        )
        narrative = render_narrative(digest)

        assert isinstance(narrative, Narrative)
        assert "tw" in narrative.headline
        assert len(narrative.categories) >= 1
        assert narrative.overall != ""


class TestSummaryRepoIntegration:
    """Integration test for SummaryRepo upsert and get."""

    def test_upsert_and_get(self, db_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
        """Can upsert a summary and retrieve it."""
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

        SummaryRepo.upsert(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="deterministic_v1",
            digest=digest,
            narrative=narrative,
        )

        result = SummaryRepo.get(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
        )

        assert result is not None
        assert isinstance(result, MarketSummary)
        assert result.region == "tw"
        assert result.period == "daily"
        assert result.summary_date == date(2026, 3, 15)
        assert result.model_id == "deterministic_v1"
        assert result.digest == digest
        assert result.narrative == narrative

    def test_upsert_updates_on_conflict(
        self, db_conn: psycopg.Connection[tuple[Any, ...]]
    ) -> None:
        """Upserting same (region, period, date, lang) updates the row."""
        digest1 = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        narrative1 = Narrative(headline="V1", categories=[], overall="First.")

        digest2 = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=10,
            entries=[],
            generated_at=datetime(2026, 3, 15, 13, 0, tzinfo=UTC),
        )
        narrative2 = Narrative(headline="V2", categories=[], overall="Second.")

        SummaryRepo.upsert(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="v1",
            digest=digest1,
            narrative=narrative1,
        )
        SummaryRepo.upsert(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="v2",
            digest=digest2,
            narrative=narrative2,
        )

        result = SummaryRepo.get(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
        )
        assert result is not None
        assert result.model_id == "v2"
        assert result.narrative.headline == "V2"

    def test_get_latest_without_date(self, db_conn: psycopg.Connection[tuple[Any, ...]]) -> None:
        """get without summary_date returns the latest entry."""
        digest = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 14),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 14, 12, 0, tzinfo=UTC),
        )
        narrative = Narrative(headline="Old", categories=[], overall="Older.")

        digest2 = MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        narrative2 = Narrative(headline="New", categories=[], overall="Newer.")

        SummaryRepo.upsert(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 14),
            lang="en",
            model_id="v1",
            digest=digest,
            narrative=narrative,
        )
        SummaryRepo.upsert(
            db_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="v1",
            digest=digest2,
            narrative=narrative2,
        )

        result = SummaryRepo.get(
            db_conn,
            region="tw",
            period="daily",
            lang="en",
        )
        assert result is not None
        assert result.summary_date == date(2026, 3, 15)
        assert result.narrative.headline == "New"
