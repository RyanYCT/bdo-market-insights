"""Tests for bdo_common.repositories with mocked psycopg connection."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from bdo_common.models import DailyRow, SnapshotRow
from bdo_common.repositories import DailyRepo, ItemRepo, ItemSidRepo, SnapshotRepo


@pytest.fixture()
def mock_conn() -> MagicMock:
    """Create a mock psycopg Connection with execute() and cursor()."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# ItemRepo
# ---------------------------------------------------------------------------


class TestItemRepo:
    """Test ItemRepo.upsert SQL generation."""

    def test_upsert_calls_execute_with_correct_params(self, mock_conn: MagicMock) -> None:
        ItemRepo.upsert(
            mock_conn,
            item_id=11608,
            name="Deboreka Ring",
            category="accessories",
            main_category="ring",
            sub_category="ring",
        )
        mock_conn.execute.assert_called_once()
        sql, params = mock_conn.execute.call_args[0]
        # Verify parameterized SQL
        assert "ON CONFLICT" in sql
        assert "%s" in sql
        # No string interpolation of values
        assert "11608" not in sql
        assert "Deboreka" not in sql
        # Verify params tuple
        assert params == (11608, "Deboreka Ring", "accessories", "ring", "ring")

    def test_upsert_with_none_optional_fields(self, mock_conn: MagicMock) -> None:
        ItemRepo.upsert(mock_conn, item_id=100, name="Test Item")
        sql, params = mock_conn.execute.call_args[0]
        assert params == (100, "Test Item", None, None, None)


# ---------------------------------------------------------------------------
# ItemSidRepo
# ---------------------------------------------------------------------------


class TestItemSidRepo:
    """Test ItemSidRepo.upsert SQL generation."""

    def test_upsert_calls_execute_with_correct_params(self, mock_conn: MagicMock) -> None:
        ItemSidRepo.upsert(
            mock_conn,
            region="tw",
            item_id=11608,
            sid=2,
            max_enhance=5,
            price_min=100_000_000,
            price_max=5_000_000_000,
        )
        mock_conn.execute.assert_called_once()
        sql, params = mock_conn.execute.call_args[0]
        assert "ON CONFLICT" in sql
        assert "%s" in sql
        assert params == ("tw", 11608, 2, 5, 100_000_000, 5_000_000_000)


# ---------------------------------------------------------------------------
# SnapshotRepo
# ---------------------------------------------------------------------------


class TestSnapshotRepo:
    """Test SnapshotRepo operations."""

    def test_bulk_insert_empty_returns_zero(self, mock_conn: MagicMock) -> None:
        result = SnapshotRepo.bulk_insert(mock_conn, [])
        assert result == 0
        mock_conn.cursor.assert_not_called()

    def test_bulk_insert_calls_executemany(self, mock_conn: MagicMock) -> None:
        ts = datetime(2024, 5, 30, 8, 0, 0, tzinfo=UTC)
        rows = [
            SnapshotRow(
                region="tw",
                snapshot_at=ts,
                item_id=11608,
                sid=0,
                base_price=448_000_000,
                current_stock=100,
                total_trades=5000,
                last_sold_price=445_000_000,
                last_sold_at=ts,
            ),
            SnapshotRow(
                region="tw",
                snapshot_at=ts,
                item_id=11629,
                sid=0,
                base_price=250_000_000,
                current_stock=200,
                total_trades=2000,
                last_sold_price=248_000_000,
                last_sold_at=ts,
            ),
        ]
        result = SnapshotRepo.bulk_insert(mock_conn, rows)
        assert result == 2
        cursor = mock_conn.cursor.return_value
        cursor.executemany.assert_called_once()
        sql, params_list = cursor.executemany.call_args[0]
        assert "ON CONFLICT" in sql
        assert "%s" in sql
        assert len(params_list) == 2
        # Verify first row tuple
        assert params_list[0] == ("tw", ts, 11608, 0, 448_000_000, 100, 5000, 445_000_000, ts)

    def test_get_snapshots_returns_models(self, mock_conn: MagicMock) -> None:
        ts = datetime(2024, 5, 30, 8, 0, 0, tzinfo=UTC)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("tw", ts, 11608, 0, 448_000_000, 100, 5000, 445_000_000, ts),
        ]
        mock_conn.execute.return_value = mock_result

        rows = SnapshotRepo.get_snapshots(mock_conn, region="tw", item_id=11608)
        assert len(rows) == 1
        assert isinstance(rows[0], SnapshotRow)
        assert rows[0].item_id == 11608
        assert rows[0].region == "tw"


# ---------------------------------------------------------------------------
# DailyRepo
# ---------------------------------------------------------------------------


class TestDailyRepo:
    """Test DailyRepo operations."""

    def test_upsert_calls_execute_with_correct_params(self, mock_conn: MagicMock) -> None:
        row = DailyRow(
            region="tw",
            trade_date=date(2024, 5, 30),
            item_id=11608,
            sid=0,
            open_price=445_000_000,
            high_price=450_000_000,
            low_price=440_000_000,
            close_price=448_000_000,
            avg_price=446_000_000,
            total_trades_delta=500,
            avg_stock=120,
            snapshot_count=24,
        )
        DailyRepo.upsert(mock_conn, row)
        mock_conn.execute.assert_called_once()
        sql, params = mock_conn.execute.call_args[0]
        assert "ON CONFLICT" in sql
        assert "%s" in sql
        assert params == (
            "tw",
            date(2024, 5, 30),
            11608,
            0,
            445_000_000,
            450_000_000,
            440_000_000,
            448_000_000,
            446_000_000,
            500,
            120,
            24,
        )

    def test_get_daily_window_returns_models(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            (
                "tw",
                date(2024, 5, 30),
                11608,
                0,
                445_000_000,
                450_000_000,
                440_000_000,
                448_000_000,
                446_000_000,
                500,
                120,
                24,
            ),
            (
                "tw",
                date(2024, 5, 29),
                11608,
                0,
                440_000_000,
                448_000_000,
                438_000_000,
                445_000_000,
                442_000_000,
                450,
                115,
                24,
            ),
        ]
        mock_conn.execute.return_value = mock_result

        rows = DailyRepo.get_daily_window(
            mock_conn, region="tw", item_id=11608, sid=0, window_days=14
        )
        assert len(rows) == 2
        assert all(isinstance(r, DailyRow) for r in rows)
        assert rows[0].trade_date == date(2024, 5, 30)
        assert rows[1].trade_date == date(2024, 5, 29)

    def test_get_daily_window_sql_uses_parameterized_limit(self, mock_conn: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        DailyRepo.get_daily_window(mock_conn, region="tw", item_id=11608, sid=0, window_days=7)
        sql, params = mock_conn.execute.call_args[0]
        assert "LIMIT %s" in sql
        assert params == ("tw", 11608, 0, 7)


# ---------------------------------------------------------------------------
# SnapshotRepo.purge_older_than
# ---------------------------------------------------------------------------


class TestSnapshotPurge:
    """Test the retention sweep DELETE."""

    def test_purge_older_than(self, mock_conn: MagicMock) -> None:
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 42
        cutoff = datetime(2026, 3, 3, tzinfo=UTC)

        result = SnapshotRepo.purge_older_than(mock_conn, cutoff)

        assert result == 42
        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert "DELETE FROM market_snapshot" in sql
        assert "snapshot_at < %s" in sql
        assert params == (cutoff,)


# ---------------------------------------------------------------------------
# DailyRepo.rollup_day
# ---------------------------------------------------------------------------


class TestDailyRollup:
    """Test the server-side daily aggregation."""

    def test_rollup_day_params_and_bounds(self, mock_conn: MagicMock) -> None:
        cursor = mock_conn.cursor.return_value
        cursor.rowcount = 7

        result = DailyRepo.rollup_day(mock_conn, region="tw", trade_date=date(2026, 5, 31))

        assert result == 7
        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert "INSERT INTO market_daily" in sql
        assert "ON CONFLICT" in sql
        # OHLC open/close via ordered array_agg on base_price (canonical price)
        assert "array_agg(base_price ORDER BY snapshot_at ASC)" in sql
        assert "array_agg(base_price ORDER BY snapshot_at DESC)" in sql
        region, day_start, day_end, region2, trade_date = params
        assert region == "tw"
        assert region2 == "tw"
        # half-open UTC day [trade_date, trade_date+1)
        assert day_start == datetime(2026, 5, 31, 0, 0, tzinfo=UTC)
        assert day_end == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        assert trade_date == date(2026, 5, 31)
