"""Tests for bdo_common.insights.repositories with mocked psycopg connection."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from bdo_common.insights.models import MarketDigest, MarketSummary, Narrative, NarrativeCategory
from bdo_common.insights.repositories import InsightRepo, SummaryRepo


@pytest.fixture()
def mock_conn() -> MagicMock:
    """Create a mock psycopg Connection with execute()."""
    conn = MagicMock()
    return conn


# ---------------------------------------------------------------------------
# InsightRepo.top_movers
# ---------------------------------------------------------------------------


class TestInsightRepoTopMovers:
    """Test InsightRepo.top_movers SQL generation and result parsing."""

    def test_daily_period_params(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (11608, "Deboreka Necklace", 3, 500_000_000, 480_000_000, 4.17, 1200),
        ]
        mock_conn.execute.return_value = mock_result

        rows = InsightRepo.top_movers(
            mock_conn,
            region="tw",
            category="accessory",
            period="daily",
            target_date=date(2026, 3, 15),
            limit=5,
        )

        mock_conn.execute.assert_called_once()
        sql, params = mock_conn.execute.call_args[0]
        # Should use parameterized SQL
        assert "%s" in sql
        # daily query uses region, target_date, category twice + limit
        assert params == [
            "tw",
            date(2026, 3, 15),
            "accessory",
            "tw",
            date(2026, 3, 15),
            "accessory",
            5,
        ]
        # Verify result parsing
        assert len(rows) == 1
        assert rows[0] == (11608, "Deboreka Necklace", 3, 500_000_000, 480_000_000, 4.17, 1200)

    def test_weekly_period_params(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (11629, "Deboreka Ring", 2, 300_000_000, 270_000_000, 11.11, 800),
        ]
        mock_conn.execute.return_value = mock_result

        rows = InsightRepo.top_movers(
            mock_conn,
            region="na",
            category="buff",
            period="weekly",
            target_date=date(2026, 3, 15),
            limit=3,
        )

        sql, params = mock_conn.execute.call_args[0]
        assert "%s" in sql
        # weekly query also has region, target_date, category x2 + limit
        assert params == [
            "na",
            date(2026, 3, 15),
            "buff",
            "na",
            date(2026, 3, 15),
            "buff",
            3,
        ]
        assert len(rows) == 1
        assert rows[0][1] == "Deboreka Ring"

    def test_empty_results(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        rows = InsightRepo.top_movers(
            mock_conn,
            region="tw",
            category="accessory",
            period="daily",
            target_date=date(2026, 3, 15),
        )
        assert rows == []

    def test_multiple_movers_returned(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (1, "Item A", 0, 100, 90, 11.1, 50),
            (2, "Item B", 0, 200, 210, -4.8, 30),
            (3, "Item C", 1, 300, 250, 20.0, 100),
        ]
        mock_conn.execute.return_value = mock_result

        rows = InsightRepo.top_movers(
            mock_conn,
            region="tw",
            category="buff",
            period="daily",
            target_date=date(2026, 3, 15),
            limit=5,
        )
        assert len(rows) == 3
        assert all(isinstance(r, tuple) for r in rows)
        # Types should be (int, str, int, int, int, float, int)
        assert isinstance(rows[0][0], int)
        assert isinstance(rows[0][1], str)
        assert isinstance(rows[0][5], float)


# ---------------------------------------------------------------------------
# SummaryRepo.upsert
# ---------------------------------------------------------------------------


class TestSummaryRepoUpsert:
    """Test SummaryRepo.upsert SQL generation."""

    def test_upsert_calls_execute(self, mock_conn: MagicMock) -> None:
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
            overall="Summary.",
        )

        SummaryRepo.upsert(
            mock_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
            model_id="deterministic_v1",
            digest=digest,
            narrative=narrative,
        )

        mock_conn.execute.assert_called_once()
        sql, params = mock_conn.execute.call_args[0]
        assert "INSERT INTO market_summary" in sql
        assert "ON CONFLICT" in sql
        assert "%s" in sql
        # Verify params tuple
        assert params[0] == "tw"
        assert params[1] == "daily"
        assert params[2] == date(2026, 3, 15)
        assert params[3] == "en"
        assert params[4] == "deterministic_v1"
        # params[5] and params[6] are JSON strings
        assert isinstance(params[5], str)
        assert isinstance(params[6], str)


# ---------------------------------------------------------------------------
# SummaryRepo.get
# ---------------------------------------------------------------------------


class TestSummaryRepoGet:
    """Test SummaryRepo.get SQL generation and parsing."""

    def _make_digest(self) -> MarketDigest:
        return MarketDigest(
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            top_n=5,
            entries=[],
            generated_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )

    def _make_narrative(self) -> Narrative:
        return Narrative(
            headline="Test",
            categories=[NarrativeCategory(category="buff", bullets=["Bullet 1"])],
            overall="Overall.",
        )

    def test_get_with_date(self, mock_conn: MagicMock) -> None:
        digest = self._make_digest()
        narrative = self._make_narrative()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (
            "tw",
            "daily",
            date(2026, 3, 15),
            "en",
            "deterministic_v1",
            digest.model_dump(),
            narrative.model_dump(),
            datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        )
        mock_conn.execute.return_value = mock_result

        result = SummaryRepo.get(
            mock_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
            lang="en",
        )

        assert result is not None
        assert isinstance(result, MarketSummary)
        assert result.region == "tw"
        assert result.period == "daily"
        assert result.digest == digest
        assert result.narrative == narrative

        sql, params = mock_conn.execute.call_args[0]
        assert "summary_date = %s" in sql
        assert params == ["tw", "daily", date(2026, 3, 15), "en"]

    def test_get_without_date_latest(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_conn.execute.return_value = mock_result

        result = SummaryRepo.get(
            mock_conn,
            region="tw",
            period="daily",
            lang="en",
        )

        assert result is None
        sql, params = mock_conn.execute.call_args[0]
        assert "ORDER BY summary_date DESC" in sql
        assert "LIMIT 1" in sql
        assert params == ["tw", "daily", "en"]

    def test_get_returns_none_when_not_found(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_conn.execute.return_value = mock_result

        result = SummaryRepo.get(
            mock_conn,
            region="tw",
            period="daily",
            summary_date=date(2026, 3, 15),
        )
        assert result is None
