"""Tests for bdo_common.insights.digest: build_digest orchestration."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

from bdo_common.insights.digest import build_digest
from bdo_common.insights.models import DigestEntry, MarketDigest


class TestBuildDigest:
    """Test build_digest end-to-end with mocked repositories and handlers."""

    @patch("bdo_common.insights.digest.get_handler")
    @patch("bdo_common.insights.digest.available_categories")
    @patch("bdo_common.insights.digest.InsightRepo")
    def test_build_digest_combines_categories(
        self,
        mock_insight_repo: MagicMock,
        mock_available: MagicMock,
        mock_get_handler: MagicMock,
    ) -> None:
        """build_digest iterates categories, fetches movers, calls handlers."""
        mock_available.return_value = ["buff", "accessory"]

        # InsightRepo.top_movers returns different data per category
        buff_movers = [(1, "Item A", 0, 100, 90, 11.1, 50)]
        acc_movers = [(2, "Item B", 3, 500, 480, 4.2, 120)]
        mock_insight_repo.top_movers.side_effect = [buff_movers, acc_movers]

        # Category handlers return DigestEntry objects
        buff_entry = DigestEntry(
            item_id=1,
            item_name="Item A",
            category="buff",
            sid=0,
            close_price=100,
            prev_close_price=90,
            pct_change=11.1,
            volume=50,
            volatility=0.05,
            liquidity=50.0,
            enhancement_cost_change=None,
        )
        acc_entry = DigestEntry(
            item_id=2,
            item_name="Item B",
            category="accessory",
            sid=3,
            close_price=500,
            prev_close_price=480,
            pct_change=4.2,
            volume=120,
            volatility=0.03,
            liquidity=120.0,
            enhancement_cost_change=0.15,
        )

        buff_handler = MagicMock(return_value=[buff_entry])
        acc_handler = MagicMock(return_value=[acc_entry])
        mock_get_handler.side_effect = [buff_handler, acc_handler]

        mock_conn = MagicMock()
        result = build_digest(
            mock_conn,
            region="tw",
            period="daily",
            target_date=date(2026, 3, 15),
            top_n=5,
        )

        assert isinstance(result, MarketDigest)
        assert result.region == "tw"
        assert result.period == "daily"
        assert result.summary_date == date(2026, 3, 15)
        assert result.top_n == 5
        assert len(result.entries) == 2
        assert result.entries[0] == buff_entry
        assert result.entries[1] == acc_entry
        assert result.generated_at is not None

        # Verify InsightRepo.top_movers was called for each category
        assert mock_insight_repo.top_movers.call_count == 2
        calls = mock_insight_repo.top_movers.call_args_list
        assert calls[0][1]["category"] == "buff"
        assert calls[1][1]["category"] == "accessory"

        # Verify handlers were called with movers
        buff_handler.assert_called_once()
        acc_handler.assert_called_once()

    @patch("bdo_common.insights.digest.get_handler")
    @patch("bdo_common.insights.digest.available_categories")
    @patch("bdo_common.insights.digest.InsightRepo")
    def test_build_digest_skips_empty_movers(
        self,
        mock_insight_repo: MagicMock,
        mock_available: MagicMock,
        mock_get_handler: MagicMock,
    ) -> None:
        """build_digest skips categories with no movers."""
        mock_available.return_value = ["buff", "accessory"]
        # First category has no movers, second has some
        acc_movers = [(2, "Item B", 3, 500, 480, 4.2, 120)]
        mock_insight_repo.top_movers.side_effect = [[], acc_movers]

        acc_entry = DigestEntry(
            item_id=2,
            item_name="Item B",
            category="accessory",
            sid=3,
            close_price=500,
            prev_close_price=480,
            pct_change=4.2,
            volume=120,
            volatility=0.03,
            liquidity=120.0,
            enhancement_cost_change=0.15,
        )
        acc_handler = MagicMock(return_value=[acc_entry])
        mock_get_handler.return_value = acc_handler

        mock_conn = MagicMock()
        result = build_digest(
            mock_conn,
            region="tw",
            period="daily",
            target_date=date(2026, 3, 15),
        )

        # Only the accessory entries are in the digest
        assert len(result.entries) == 1
        assert result.entries[0].category == "accessory"
        # Handler should only be called once (for the non-empty category)
        assert mock_get_handler.call_count == 1

    @patch("bdo_common.insights.digest.get_handler")
    @patch("bdo_common.insights.digest.available_categories")
    @patch("bdo_common.insights.digest.InsightRepo")
    def test_build_digest_no_categories(
        self,
        mock_insight_repo: MagicMock,
        mock_available: MagicMock,
        mock_get_handler: MagicMock,
    ) -> None:
        """build_digest with no registered categories returns empty digest."""
        mock_available.return_value = []

        mock_conn = MagicMock()
        result = build_digest(
            mock_conn,
            region="tw",
            period="weekly",
            target_date=date(2026, 3, 15),
            top_n=3,
        )

        assert isinstance(result, MarketDigest)
        assert result.entries == []
        assert result.period == "weekly"
        assert result.top_n == 3
        mock_insight_repo.top_movers.assert_not_called()

    @patch("bdo_common.insights.digest.get_handler")
    @patch("bdo_common.insights.digest.available_categories")
    @patch("bdo_common.insights.digest.InsightRepo")
    def test_build_digest_generated_at_is_utc(
        self,
        mock_insight_repo: MagicMock,
        mock_available: MagicMock,
        mock_get_handler: MagicMock,
    ) -> None:
        """build_digest sets generated_at to UTC."""
        mock_available.return_value = []

        mock_conn = MagicMock()
        before = datetime.now(tz=UTC)
        result = build_digest(
            mock_conn,
            region="tw",
            period="daily",
            target_date=date(2026, 3, 15),
        )
        after = datetime.now(tz=UTC)

        assert result.generated_at >= before
        assert result.generated_at <= after
        assert result.generated_at.tzinfo is not None
