"""Tests for bdo_common.arsha_client: normalize_response and ArshaClient.

The normalizer is exercised against arsha.io v2's real JSON response shapes
(a bare object, a list of objects, and a list of lists of objects) rather than
the legacy pipe-delimited ``resultMsg`` format.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bdo_common.arsha_client import ArshaClient, normalize_item_db, normalize_response

TIMESTAMP = 1717027200  # 2024-05-30T08:00:00 UTC


def _item(
    item_id: int,
    sid: int = 0,
    *,
    name: str = "Test Item",
    base_price: int = 100,
    current_stock: int = 10,
    total_trades: int = 100,
    last_sold_price: int = 95,
    last_sold_time: int = TIMESTAMP,
    max_enhance: int = 0,
    price_min: int = 1,
    price_max: int = 1_000_000,
) -> dict[str, Any]:
    """Build an arsha.io v2 GetWorldMarketSubList item object (camelCase keys)."""
    return {
        "id": item_id,
        "sid": sid,
        "name": name,
        "minEnhance": 0,
        "maxEnhance": max_enhance,
        "basePrice": base_price,
        "currentStock": current_stock,
        "totalTrades": total_trades,
        "priceMin": price_min,
        "priceMax": price_max,
        "lastSoldPrice": last_sold_price,
        "lastSoldTime": last_sold_time,
    }


# ---------------------------------------------------------------------------
# normalize_response - polymorphic JSON shapes
# ---------------------------------------------------------------------------


class TestNormalizeResponseShapes:
    """Cover arsha.io's polymorphic JSON response shapes."""

    def test_shape1_single_item_non_enhanceable(self) -> None:
        """A single non-enhanceable item is returned as a bare object."""
        raw = _item(
            11608,
            0,
            name="Deboreka Necklace",
            base_price=448_000_000,
            current_stock=1234,
            total_trades=5000,
            last_sold_price=445_000_000,
        )
        records = normalize_response(raw)
        assert len(records) == 1
        r = records[0]
        assert r.item_id == 11608
        assert r.sid == 0
        assert r.name == "Deboreka Necklace"
        assert r.base_price == 448_000_000
        assert r.current_stock == 1234
        assert r.total_trades == 5000
        assert r.last_sold_price == 445_000_000
        assert r.last_sold_at == datetime.fromtimestamp(TIMESTAMP, tz=UTC)

    def test_shape2_single_item_enhanceable(self) -> None:
        """A single enhanceable item is a list of objects, one per sid."""
        raw = [
            _item(11608, 0, base_price=448_000_000, max_enhance=5),
            _item(11608, 1, base_price=1_020_000_000, max_enhance=5),
            _item(11608, 2, base_price=3_600_000_000, max_enhance=5),
        ]
        records = normalize_response(raw)
        assert len(records) == 3
        assert [r.sid for r in records] == [0, 1, 2]
        assert all(r.item_id == 11608 for r in records)
        assert all(r.max_enhance == 5 for r in records)

    def test_shape3_multiple_items_non_enhanceable(self) -> None:
        """Multiple non-enhanceable items: a flat list of objects."""
        raw = [_item(11608, 0), _item(11629, 0)]
        records = normalize_response(raw)
        assert {r.item_id for r in records} == {11608, 11629}

    def test_shape4_multiple_items_enhanceable(self) -> None:
        """Multiple enhanceable items: a list of lists of objects."""
        raw = [
            [_item(11608, 0), _item(11608, 1)],
            [_item(11629, 0), _item(11629, 1)],
        ]
        records = normalize_response(raw)
        assert len(records) == 4
        assert len([r for r in records if r.item_id == 11608]) == 2
        assert len([r for r in records if r.item_id == 11629]) == 2

    def test_shape5_mixed(self) -> None:
        """Mixed: a list holding both nested lists and a bare object."""
        raw = [
            [_item(11608, 0), _item(11608, 1)],  # enhanceable -> nested list
            _item(99999, 0),  # non-enhanceable -> bare object
        ]
        records = normalize_response(raw)
        assert len(records) == 3
        plain = [r for r in records if r.item_id == 99999]
        assert len(plain) == 1
        assert plain[0].sid == 0

    def test_item_sid_fields_captured(self) -> None:
        """price_min/price_max/max_enhance feed item_sid and must round-trip."""
        raw = _item(11608, 2, max_enhance=5, price_min=100_000_000, price_max=5_000_000_000)
        (r,) = normalize_response(raw)
        assert r.max_enhance == 5
        assert r.price_min == 100_000_000
        assert r.price_max == 5_000_000_000


# ---------------------------------------------------------------------------
# normalize_response - edge cases
# ---------------------------------------------------------------------------


class TestNormalizeResponseEdgeCases:
    """Edge cases that must not crash and must skip non-item data."""

    def test_empty_list_returns_empty(self) -> None:
        assert normalize_response([]) == []

    def test_empty_dict_returns_empty(self) -> None:
        assert normalize_response({}) == []

    def test_nested_empty_lists_return_empty(self) -> None:
        assert normalize_response([[], []]) == []

    def test_error_envelope_returns_empty(self) -> None:
        """An error payload (no id/sid) is not an item row."""
        assert normalize_response({"error": "not found", "statusCode": 404}) == []

    def test_malformed_item_skipped(self) -> None:
        """A non-numeric price is skipped; valid siblings survive."""
        bad = _item(11608, 0)
        bad["basePrice"] = "not-a-number"
        records = normalize_response([bad, _item(11629, 0)])
        assert [r.item_id for r in records] == [11629]

    def test_missing_field_skipped(self) -> None:
        """An item missing a required field is skipped, doesn't crash."""
        incomplete = _item(11608, 0)
        del incomplete["priceMin"]
        records = normalize_response([incomplete, _item(11629, 0)])
        assert [r.item_id for r in records] == [11629]

    def test_dict_without_identity_keys_skipped(self) -> None:
        records = normalize_response([{"name": "no id or sid"}, _item(11629, 0)])
        assert [r.item_id for r in records] == [11629]

    def test_scalars_ignored(self) -> None:
        records = normalize_response([None, 42, "string", _item(11608, 0)])
        assert [r.item_id for r in records] == [11608]


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
        response_data = _item(11608, 0)  # bare object (single item shape)
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
        response_data: list[Any] = []  # empty list shape

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


# ---------------------------------------------------------------------------
# normalize_item_db - util/db catalog rows
# ---------------------------------------------------------------------------


class TestNormalizeItemDb:
    """Parse the flat util/db catalog list into CatalogEntry objects."""

    def test_parses_flat_list(self) -> None:
        raw = [
            {"id": 37364, "name": "Wild Herb", "grade": 4},
            {"id": 3679, "name": "[Manor] Cypress Tree", "grade": 2},
        ]
        entries = normalize_item_db(raw)
        assert [(e.item_id, e.name, e.grade) for e in entries] == [
            (37364, "Wild Herb", 4),
            (3679, "[Manor] Cypress Tree", 2),
        ]

    def test_grade_optional(self) -> None:
        (entry,) = normalize_item_db([{"id": 1, "name": "No Grade"}])
        assert entry.grade is None

    def test_grade_zero_preserved(self) -> None:
        (entry,) = normalize_item_db([{"id": 7, "name": "White Item", "grade": 0}])
        assert entry.grade == 0

    def test_non_list_returns_empty(self) -> None:
        assert normalize_item_db({"error": "boom"}) == []

    def test_skips_rows_without_id(self) -> None:
        entries = normalize_item_db([{"name": "orphan"}, {"id": 2, "name": "Keep", "grade": 0}])
        assert [e.item_id for e in entries] == [2]

    def test_skips_malformed_row(self) -> None:
        entries = normalize_item_db(
            [{"id": "not-an-int", "name": "Bad"}, {"id": 9, "name": "Good", "grade": 1}]
        )
        assert [e.item_id for e in entries] == [9]

    def test_ignores_non_dict_elements(self) -> None:
        entries = normalize_item_db([None, 42, "x", {"id": 5, "name": "Ok", "grade": 3}])
        assert [e.item_id for e in entries] == [5]


# ---------------------------------------------------------------------------
# ArshaClient.fetch_item_db (mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchItemDb:
    """Test the util/db full-catalog fetch."""

    def test_build_item_db_url(self) -> None:
        client = ArshaClient(util_base_url="https://api.arsha.io/util")
        assert client._build_item_db_url("tw") == "https://api.arsha.io/util/db?lang=tw"

    def test_build_item_db_url_trailing_slash_stripped(self) -> None:
        client = ArshaClient(util_base_url="https://api.arsha.io/util/")
        assert client._build_item_db_url("en") == "https://api.arsha.io/util/db?lang=en"

    def test_unsupported_lang_raises(self) -> None:
        client = ArshaClient()
        with pytest.raises(ValueError, match="unsupported lang"):
            client.fetch_item_db("xx")

    def test_fetch_success(self) -> None:
        client = ArshaClient()
        payload = [
            {"id": 37364, "name": "Wild Herb", "grade": 4},
            {"id": 3679, "name": "Cypress Tree", "grade": 2},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("bdo_common.arsha_client.urllib.request.urlopen", return_value=mock_resp):
            entries = client.fetch_item_db("en")

        assert {e.item_id for e in entries} == {37364, 3679}

    def test_network_error_returns_empty(self) -> None:
        client = ArshaClient()
        with patch(
            "bdo_common.arsha_client.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            assert client.fetch_item_db("en") == []
