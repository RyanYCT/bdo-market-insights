"""Tests for bdo_common.insights.categories: registry and handlers."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from bdo_common.insights.categories import (
    _handle_accessory,
    _handle_buff,
    available_categories,
    get_handler,
    register_handler,
)
from bdo_common.insights.models import DigestEntry

# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestCategoryRegistry:
    """Test the category handler registry."""

    def test_builtin_categories_registered(self) -> None:
        """buff and accessory should be registered at import time."""
        cats = available_categories()
        assert "buff" in cats
        assert "accessory" in cats

    def test_get_handler_returns_callable(self) -> None:
        handler = get_handler("buff")
        assert callable(handler)
        handler = get_handler("accessory")
        assert callable(handler)

    def test_get_handler_unknown_raises_keyerror(self) -> None:
        with pytest.raises(KeyError, match="unknown insight category"):
            get_handler("nonexistent_category")

    def test_register_handler_custom(self) -> None:
        """Can register a custom handler and retrieve it."""

        def _dummy(
            conn: MagicMock,
            region: str,
            period: str,
            target_date: date,
            movers: list[tuple[int, str, int, int, int, float, int]],
        ) -> list[DigestEntry]:
            return []

        register_handler("test_custom", _dummy)  # type: ignore[arg-type]
        assert get_handler("test_custom") is _dummy
        assert "test_custom" in available_categories()


# ---------------------------------------------------------------------------
# Buff handler tests
# ---------------------------------------------------------------------------


class TestBuffHandler:
    """Test _handle_buff with mocked DailyRepo.get_daily_window."""

    @patch("bdo_common.repositories.DailyRepo")
    def test_buff_handler_produces_entries(self, mock_daily_repo: MagicMock) -> None:
        """Buff handler enriches movers with volatility and liquidity."""
        from bdo_common.models import DailyRow

        # Mock DailyRepo.get_daily_window to return enough data for analytics
        mock_daily_repo.get_daily_window.return_value = [
            DailyRow(
                region="tw",
                trade_date=date(2026, 3, 15) - __import__("datetime").timedelta(days=i),
                item_id=11608,
                sid=0,
                open_price=100 + i,
                high_price=110 + i,
                low_price=90 + i,
                close_price=105 + i * 2,
                avg_price=100 + i,
                total_trades_delta=50 + i * 10,
                avg_stock=100,
                snapshot_count=24,
            )
            for i in range(5)
        ]

        mock_conn = MagicMock()
        movers: list[tuple[int, str, int, int, int, float, int]] = [
            (11608, "Deboreka Necklace", 0, 500_000_000, 480_000_000, 4.17, 1200),
        ]

        entries = _handle_buff(mock_conn, "tw", "daily", date(2026, 3, 15), movers)

        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, DigestEntry)
        assert entry.item_id == 11608
        assert entry.category == "buff"
        assert entry.close_price == 500_000_000
        assert entry.pct_change == 4.17
        # Volatility and liquidity should be computed
        assert entry.volatility is not None
        assert entry.volatility > 0
        assert entry.liquidity is not None
        assert entry.liquidity > 0
        # Buff never has enhancement_cost_change
        assert entry.enhancement_cost_change is None

    @patch("bdo_common.repositories.DailyRepo")
    def test_buff_handler_insufficient_data(self, mock_daily_repo: MagicMock) -> None:
        """Buff handler handles insufficient data for volatility gracefully."""
        from bdo_common.models import DailyRow

        # Only one data point -- not enough for daily_volatility
        mock_daily_repo.get_daily_window.return_value = [
            DailyRow(
                region="tw",
                trade_date=date(2026, 3, 15),
                item_id=1,
                sid=0,
                open_price=100,
                high_price=110,
                low_price=90,
                close_price=105,
                avg_price=100,
                total_trades_delta=50,
                avg_stock=100,
                snapshot_count=24,
            ),
        ]

        mock_conn = MagicMock()
        movers: list[tuple[int, str, int, int, int, float, int]] = [
            (1, "Test Item", 0, 105, 100, 5.0, 50),
        ]

        entries = _handle_buff(mock_conn, "tw", "daily", date(2026, 3, 15), movers)

        assert len(entries) == 1
        # With only 1 data point, volatility should be None (insufficient)
        assert entries[0].volatility is None
        # But liquidity should still work with 1 volume value
        assert entries[0].liquidity is not None

    @patch("bdo_common.repositories.DailyRepo")
    def test_buff_handler_multiple_movers(self, mock_daily_repo: MagicMock) -> None:
        """Buff handler processes multiple movers."""
        from bdo_common.models import DailyRow

        mock_daily_repo.get_daily_window.return_value = [
            DailyRow(
                region="tw",
                trade_date=date(2026, 3, 15) - __import__("datetime").timedelta(days=i),
                item_id=1,
                sid=0,
                open_price=100,
                high_price=110,
                low_price=90,
                close_price=100 + i * 5,
                avg_price=100,
                total_trades_delta=50,
                avg_stock=100,
                snapshot_count=24,
            )
            for i in range(3)
        ]

        mock_conn = MagicMock()
        movers: list[tuple[int, str, int, int, int, float, int]] = [
            (1, "Item A", 0, 100, 90, 11.1, 50),
            (2, "Item B", 0, 200, 210, -4.8, 30),
        ]

        entries = _handle_buff(mock_conn, "tw", "daily", date(2026, 3, 15), movers)
        assert len(entries) == 2
        assert entries[0].item_name == "Item A"
        assert entries[1].item_name == "Item B"


# ---------------------------------------------------------------------------
# Accessory handler tests
# ---------------------------------------------------------------------------


class TestAccessoryHandler:
    """Test _handle_accessory with mocked dependencies."""

    @patch("bdo_common.insights.categories.enhancement_analysis")
    @patch("bdo_common.repositories.DailyRepo")
    def test_accessory_handler_with_enhancement(
        self, mock_daily_repo: MagicMock, mock_enhancement: MagicMock
    ) -> None:
        """Accessory handler enriches with enhancement cost change."""
        from bdo_common.models import DailyRow

        mock_daily_repo.get_daily_window.return_value = [
            DailyRow(
                region="tw",
                trade_date=date(2026, 3, 15) - __import__("datetime").timedelta(days=i),
                item_id=11608,
                sid=3,
                open_price=500_000_000,
                high_price=510_000_000,
                low_price=490_000_000,
                close_price=500_000_000 + i * 1_000_000,
                avg_price=500_000_000,
                total_trades_delta=100 + i * 10,
                avg_stock=50,
                snapshot_count=24,
            )
            for i in range(5)
        ]

        # Mock _fetch_sid_prices by making conn.execute return sid prices
        mock_fetchall = MagicMock()
        mock_fetchall.fetchall.return_value = [
            (0, 100_000_000),
            (1, 200_000_000),
            (2, 350_000_000),
            (3, 500_000_000),
        ]
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_fetchall

        # Mock enhancement_analysis to return transitions with ROI
        mock_enhancement.return_value = {
            "transitions": [
                {"sid_from": 0, "sid_to": 1, "roi": 0.5},
                {"sid_from": 1, "sid_to": 2, "roi": 0.3},
                {"sid_from": 2, "sid_to": 3, "roi": 0.15},
            ],
        }

        movers: list[tuple[int, str, int, int, int, float, int]] = [
            (11608, "Deboreka Necklace", 3, 500_000_000, 480_000_000, 4.17, 1200),
        ]

        entries = _handle_accessory(mock_conn, "tw", "daily", date(2026, 3, 15), movers)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.category == "accessory"
        assert entry.enhancement_cost_change == 0.15
        assert entry.volatility is not None
        assert entry.liquidity is not None
        mock_enhancement.assert_called_once()

    @patch("bdo_common.insights.categories.enhancement_analysis")
    @patch("bdo_common.repositories.DailyRepo")
    def test_accessory_handler_no_sid0_price(
        self, mock_daily_repo: MagicMock, mock_enhancement: MagicMock
    ) -> None:
        """Accessory handler handles missing sid=0 gracefully."""
        from bdo_common.models import DailyRow

        mock_daily_repo.get_daily_window.return_value = [
            DailyRow(
                region="tw",
                trade_date=date(2026, 3, 15) - __import__("datetime").timedelta(days=i),
                item_id=11608,
                sid=3,
                open_price=500_000_000,
                high_price=510_000_000,
                low_price=490_000_000,
                close_price=500_000_000,
                avg_price=500_000_000,
                total_trades_delta=100,
                avg_stock=50,
                snapshot_count=24,
            )
            for i in range(3)
        ]

        # No sid=0 in fetchall results
        mock_fetchall = MagicMock()
        mock_fetchall.fetchall.return_value = [
            (3, 500_000_000),
        ]
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_fetchall

        movers: list[tuple[int, str, int, int, int, float, int]] = [
            (11608, "Deboreka Necklace", 3, 500_000_000, 480_000_000, 4.17, 1200),
        ]

        entries = _handle_accessory(mock_conn, "tw", "daily", date(2026, 3, 15), movers)

        assert len(entries) == 1
        # Without sid=0, enhancement_analysis should not be called
        # and enhancement_cost_change should be None
        assert entries[0].enhancement_cost_change is None

    @patch("bdo_common.insights.categories.enhancement_analysis")
    @patch("bdo_common.repositories.DailyRepo")
    def test_accessory_handler_preserves_mover_close_after_fetch(
        self, mock_daily_repo: MagicMock, mock_enhancement: MagicMock
    ) -> None:
        """Mover's authoritative close_price is not overwritten by _fetch_sid_prices."""
        from bdo_common.models import DailyRow

        mock_daily_repo.get_daily_window.return_value = [
            DailyRow(
                region="tw",
                trade_date=date(2026, 3, 15) - __import__("datetime").timedelta(days=i),
                item_id=11608,
                sid=3,
                open_price=500_000_000,
                high_price=510_000_000,
                low_price=490_000_000,
                close_price=500_000_000 + i * 1_000_000,
                avg_price=500_000_000,
                total_trades_delta=100 + i * 10,
                avg_stock=50,
                snapshot_count=24,
            )
            for i in range(5)
        ]

        # _fetch_sid_prices returns a STALE price for sid=3 (different from mover close)
        mock_fetchall = MagicMock()
        mock_fetchall.fetchall.return_value = [
            (0, 100_000_000),
            (3, 490_000_000),  # stale: differs from mover's 500_000_000
        ]
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_fetchall

        # Capture what prices dict is passed to enhancement_analysis
        def _capture_prices(prices: dict[int, float], model_id: str) -> dict[str, list[object]]:
            # Verify the mover's authoritative close was re-applied
            assert prices[3] == 500_000_000.0
            return {
                "transitions": [
                    {"sid_from": 0, "sid_to": 3, "roi": 0.2},
                ],
            }

        mock_enhancement.side_effect = _capture_prices

        movers: list[tuple[int, str, int, int, int, float, int]] = [
            (11608, "Deboreka Necklace", 3, 500_000_000, 480_000_000, 4.17, 1200),
        ]

        entries = _handle_accessory(mock_conn, "tw", "daily", date(2026, 3, 15), movers)

        assert len(entries) == 1
        # enhancement_analysis was called with the correct authoritative price
        mock_enhancement.assert_called_once()
        assert entries[0].enhancement_cost_change == 0.2
