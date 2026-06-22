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

        # stats computed across the combined entries
        assert result.stats is not None
        assert result.stats.total == 2
        assert result.stats.gainers == 2
        assert result.stats.losers == 0
        assert result.stats.flat == 0
        assert result.stats.top_gainer is not None
        assert result.stats.top_gainer.item_name == "Item A"  # +11.1 > +4.2
        assert result.stats.top_loser is None  # no decliners
        assert result.stats.most_traded is not None
        assert result.stats.most_traded.item_name == "Item B"  # volume 120 > 50
        assert result.stats.most_volatile is not None
        assert result.stats.most_volatile.item_name == "Item A"  # cv 0.05 > 0.03

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
        assert result.stats is None  # no entries -> no stats
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

    @patch("bdo_common.insights.digest.get_handler")
    @patch("bdo_common.insights.digest.available_categories")
    @patch("bdo_common.insights.digest.InsightRepo")
    def test_build_digest_drops_flat_filler(
        self,
        mock_insight_repo: MagicMock,
        mock_available: MagicMock,
        mock_get_handler: MagicMock,
    ) -> None:
        """Flat 0% rows with no other signal are dropped; flat rows carrying an
        anomaly or a notable enhancement_cost_change are kept."""
        mock_available.return_value = ["accessory"]
        mock_insight_repo.top_movers.return_value = [(1, "X", 0, 100, 100, 0.0, 10)]

        def _entry(
            name: str, *, pct: float, anomaly: bool | None, enh: float | None
        ) -> DigestEntry:
            return DigestEntry(
                item_id=1,
                item_name=name,
                category="accessory",
                sid=0,
                close_price=100,
                prev_close_price=100,
                pct_change=pct,
                volume=10,
                volatility=0.01,
                liquidity=10.0,
                enhancement_cost_change=enh,
                anomaly=anomaly,
            )

        real = _entry("Mover", pct=20.0, anomaly=False, enh=None)
        flat_noise = _entry("Flat", pct=0.0, anomaly=False, enh=None)
        flat_enh = _entry("EnhMover", pct=0.0, anomaly=False, enh=5.0)
        flat_anomaly = _entry("Spiker", pct=0.0, anomaly=True, enh=None)
        mock_get_handler.return_value = MagicMock(
            return_value=[real, flat_noise, flat_enh, flat_anomaly]
        )

        result = build_digest(
            MagicMock(), region="tw", period="daily", target_date=date(2026, 3, 15)
        )

        names = {e.item_name for e in result.entries}
        assert names == {"Mover", "EnhMover", "Spiker"}
        assert "Flat" not in names

    @patch("bdo_common.insights.digest.get_handler")
    @patch("bdo_common.insights.digest.available_categories")
    @patch("bdo_common.insights.digest.InsightRepo")
    def test_build_digest_collects_enhancement_cost_movers(
        self,
        mock_insight_repo: MagicMock,
        mock_available: MagicMock,
        mock_get_handler: MagicMock,
    ) -> None:
        """stats.enhancement_cost_movers lists every notable, correctly-labelled
        enhancement move, sorted by |value| desc; nulls and sub-threshold moves
        are excluded."""
        mock_available.return_value = ["accessory"]
        mock_insight_repo.top_movers.return_value = [(1, "X", 0, 100, 100, 0.0, 10)]

        def _entry(sid: int, *, pct: float, enh: float | None) -> DigestEntry:
            return DigestEntry(
                item_id=4,
                item_name="Ring of Cadry",
                category="accessory",
                sid=sid,
                close_price=100,
                prev_close_price=100,
                pct_change=pct,
                volume=10,
                volatility=0.01,
                liquidity=10.0,
                enhancement_cost_change=enh,
            )

        mock_get_handler.return_value = MagicMock(
            return_value=[
                _entry(1, pct=0.0, enh=5.0),  # flat price, big enhancement move
                _entry(2, pct=0.0, enh=1.43),
                _entry(3, pct=-12.0, enh=0.71),
                _entry(0, pct=5.0, enh=None),  # no enhancement move
                _entry(4, pct=8.0, enh=0.2),  # sub-threshold enhancement move
            ]
        )

        result = build_digest(
            MagicMock(), region="tw", period="daily", target_date=date(2026, 3, 15)
        )

        assert result.stats is not None
        movers = [(m.sid, round(m.value, 2)) for m in result.stats.enhancement_cost_movers]
        assert movers == [(1, 5.0), (2, 1.43), (3, 0.71)]
