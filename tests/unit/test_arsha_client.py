"""Tests for bdo_common.arsha_client: normalize_response and ArshaClient."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from bdo_common.arsha_client import ArshaClient, normalize_response

# ---------------------------------------------------------------------------
# normalize_response - 5 response shapes
# ---------------------------------------------------------------------------

TIMESTAMP = 1717027200  # 2024-05-30T08:00:00 UTC


class TestNormalizeResponseShapes:
    """Cover all 5 arsha.io polymorphic response shapes."""

    def test_shape1_single_item_non_enhanceable(self) -> None:
        """Single item, sid=0 only."""
        raw: dict[str, Any] = {
            "resultCode": 0,
            "resultMsg": f"11608-0-448000000-1234-5000-448000000-445000000-{TIMESTAMP}",
        }
        records = normalize_response(raw)
        assert len(records) == 1
        r = records[0]
        assert r.item_id == 11608
        assert r.sid == 0
        assert r.base_price == 448_000_000
        assert r.current_stock == 1234
        assert r.total_trades == 5000
        assert r.last_sold_price == 445_000_000
        assert r.last_sold_at == datetime.fromtimestamp(TIMESTAMP, tz=UTC)

    def test_shape2_single_item_enhanceable(self) -> None:
        """Single item with multiple enhancement levels (rows separated by |)."""
        rows = [
            f"11608-0-448000000-100-1000-448000000-445000000-{TIMESTAMP}",
            f"11608-1-1020000000-50-800-1020000000-1000000000-{TIMESTAMP}",
            f"11608-2-3600000000-20-300-3600000000-3500000000-{TIMESTAMP}",
        ]
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": "|".join(rows)}
        records = normalize_response(raw)
        assert len(records) == 3
        assert records[0].sid == 0
        assert records[1].sid == 1
        assert records[2].sid == 2
        assert all(r.item_id == 11608 for r in records)

    def test_shape3_multiple_items_non_enhanceable(self) -> None:
        """Multiple different items, each with sid=0."""
        rows = [
            f"11608-0-448000000-100-1000-448000000-445000000-{TIMESTAMP}",
            f"11629-0-250000000-200-2000-250000000-248000000-{TIMESTAMP}",
        ]
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": "|".join(rows)}
        records = normalize_response(raw)
        assert len(records) == 2
        assert records[0].item_id == 11608
        assert records[1].item_id == 11629

    def test_shape4_multiple_items_enhanceable(self) -> None:
        """Multiple items, each with multiple sids."""
        rows = [
            f"11608-0-448000000-100-1000-448000000-445000000-{TIMESTAMP}",
            f"11608-1-1020000000-50-800-1020000000-1000000000-{TIMESTAMP}",
            f"11629-0-250000000-200-2000-250000000-248000000-{TIMESTAMP}",
            f"11629-1-600000000-80-500-600000000-590000000-{TIMESTAMP}",
        ]
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": "|".join(rows)}
        records = normalize_response(raw)
        assert len(records) == 4
        # Verify item grouping
        item_11608 = [r for r in records if r.item_id == 11608]
        item_11629 = [r for r in records if r.item_id == 11629]
        assert len(item_11608) == 2
        assert len(item_11629) == 2

    def test_shape5_mixed(self) -> None:
        """Mix of enhanceable and non-enhanceable items."""
        rows = [
            f"11608-0-448000000-100-1000-448000000-445000000-{TIMESTAMP}",
            f"11608-1-1020000000-50-800-1020000000-1000000000-{TIMESTAMP}",
            f"99999-0-10000-500-10000-10000-9900-{TIMESTAMP}",
        ]
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": "|".join(rows)}
        records = normalize_response(raw)
        assert len(records) == 3
        # Non-enhanceable item
        plain = [r for r in records if r.item_id == 99999]
        assert len(plain) == 1
        assert plain[0].sid == 0


# ---------------------------------------------------------------------------
# normalize_response - edge cases
# ---------------------------------------------------------------------------


class TestNormalizeResponseEdgeCases:
    """Edge cases that must not crash."""

    def test_result_code_non_zero_returns_empty(self) -> None:
        raw: dict[str, Any] = {"resultCode": 1, "resultMsg": "some-data"}
        assert normalize_response(raw) == []

    def test_empty_result_msg_returns_empty(self) -> None:
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": ""}
        assert normalize_response(raw) == []

    def test_missing_result_msg_returns_empty(self) -> None:
        raw: dict[str, Any] = {"resultCode": 0}
        assert normalize_response(raw) == []

    def test_null_result_msg_returns_empty(self) -> None:
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": None}
        assert normalize_response(raw) == []

    def test_malformed_row_skipped(self) -> None:
        """A row with non-numeric data is skipped, doesn't crash."""
        rows = [
            f"11608-0-448000000-1234-5000-448000000-445000000-{TIMESTAMP}",
            "bad-data-here-not-numeric-extra-fields-ok-fine",
            f"11629-0-250000000-200-2000-250000000-248000000-{TIMESTAMP}",
        ]
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": "|".join(rows)}
        records = normalize_response(raw)
        # The malformed row has 8 fields but "bad" etc are not ints
        # So it should be skipped
        assert len(records) == 2

    def test_wrong_number_of_fields_skipped(self) -> None:
        """A row with too few fields is skipped."""
        rows = [
            f"11608-0-448000000-1234-5000-448000000-445000000-{TIMESTAMP}",
            "11608-0-448000000",  # only 3 fields
        ]
        raw: dict[str, Any] = {"resultCode": 0, "resultMsg": "|".join(rows)}
        records = normalize_response(raw)
        assert len(records) == 1

    def test_trailing_pipe_ignored(self) -> None:
        """Trailing | produces empty string which is skipped."""
        raw: dict[str, Any] = {
            "resultCode": 0,
            "resultMsg": f"11608-0-448000000-1234-5000-448000000-445000000-{TIMESTAMP}|",
        }
        records = normalize_response(raw)
        assert len(records) == 1


# ---------------------------------------------------------------------------
# ArshaClient._build_url
# ---------------------------------------------------------------------------


class TestArshaClientBuildUrl:
    """Test URL construction."""

    def test_build_url_single_id(self) -> None:
        client = ArshaClient(base_url="https://api.arsha.io/v2", region="tw")
        url = client._build_url([11608])
        assert url == "https://api.arsha.io/v2/tw/GetWorldMarketSubList?id=11608"

    def test_build_url_multiple_ids(self) -> None:
        client = ArshaClient(base_url="https://api.arsha.io/v2", region="na")
        url = client._build_url([100, 200, 300])
        assert url == "https://api.arsha.io/v2/na/GetWorldMarketSubList?id=100,200,300"

    def test_build_url_trailing_slash_stripped(self) -> None:
        client = ArshaClient(base_url="https://api.arsha.io/v2/", region="eu")
        url = client._build_url([1])
        assert url == "https://api.arsha.io/v2/eu/GetWorldMarketSubList?id=1"


# ---------------------------------------------------------------------------
# ArshaClient._split_batch_by_url_length
# ---------------------------------------------------------------------------


class TestSplitBatchByUrlLength:
    """Test that long URLs are split into sub-batches."""

    def test_short_batch_not_split(self) -> None:
        client = ArshaClient()
        ids = list(range(1, 10))
        batches = client._split_batch_by_url_length(ids)
        assert batches == [ids]

    def test_long_batch_is_split(self) -> None:
        """IDs with many digits should force a split when URL > 1900 chars."""
        client = ArshaClient()
        # Use 10-digit IDs to inflate URL length
        ids = list(range(1000000000, 1000000000 + 200))
        batches = client._split_batch_by_url_length(ids)
        assert len(batches) > 1
        # All original IDs are preserved
        flat = [i for batch in batches for i in batch]
        assert sorted(flat) == sorted(ids)
        # Each batch URL is within limit
        for batch in batches:
            url = client._build_url(batch)
            assert len(url) <= 1900


# ---------------------------------------------------------------------------
# ArshaClient.fetch_sub_list (mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchSubList:
    """Test fetch_sub_list with mocked HTTP."""

    def test_fetch_sub_list_success(self) -> None:
        client = ArshaClient()
        response_data = {
            "resultCode": 0,
            "resultMsg": f"11608-0-448000000-100-1000-448000000-445000000-{TIMESTAMP}",
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("bdo_common.arsha_client.urllib.request.urlopen", return_value=mock_resp):
            records = client.fetch_sub_list([11608])

        assert len(records) == 1
        assert records[0].item_id == 11608

    def test_fetch_sub_list_empty_ids(self) -> None:
        client = ArshaClient()
        records = client.fetch_sub_list([])
        assert records == []

    def test_fetch_sub_list_network_error_skipped(self) -> None:
        """Network error logs but does not raise."""
        client = ArshaClient()
        with patch(
            "bdo_common.arsha_client.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            records = client.fetch_sub_list([11608])
        assert records == []

    def test_batching_100_ids_produces_multiple_batches(self) -> None:
        """100 IDs should produce at least 2 batches of <=50."""
        client = ArshaClient()
        ids = list(range(1, 101))

        call_count = 0
        response_data = {"resultCode": 0, "resultMsg": ""}

        def fake_urlopen(url: str, timeout: int = 10) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(response_data).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch(
            "bdo_common.arsha_client.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            client.fetch_sub_list(ids)

        assert call_count >= 2
