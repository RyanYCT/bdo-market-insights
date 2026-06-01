"""Tests for bdo_common.models Pydantic v2 schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from bdo_common.models import DailyRow, Item, ItemSid, Record, SnapshotRow

# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


class TestRecord:
    """Record model validation."""

    def test_valid_data_parses(self) -> None:
        r = Record(
            item_id=11608,
            sid=0,
            name="Deboreka Ring",
            base_price=448_000_000,
            current_stock=100,
            total_trades=5000,
            last_sold_price=445_000_000,
            last_sold_at=datetime(2024, 5, 30, 8, 0, 0, tzinfo=UTC),
            max_enhance=5,
            price_min=100_000_000,
            price_max=5_000_000_000,
        )
        assert r.item_id == 11608
        assert r.sid == 0
        assert r.base_price == 448_000_000
        assert r.name == "Deboreka Ring"
        assert r.price_max == 5_000_000_000

    def test_invalid_type_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Record(
                item_id="not_an_int",
                sid=0,
                name="x",
                base_price=100,
                current_stock=1,
                total_trades=1,
                last_sold_price=100,
                last_sold_at="not a datetime",
                max_enhance=0,
                price_min=1,
                price_max=10,
            )

    def test_frozen_rejects_mutation(self) -> None:
        r = Record(
            item_id=1,
            sid=0,
            name="x",
            base_price=100,
            current_stock=1,
            total_trades=1,
            last_sold_price=100,
            last_sold_at=datetime(2024, 1, 1, tzinfo=UTC),
            max_enhance=0,
            price_min=1,
            price_max=10,
        )
        with pytest.raises(ValidationError):
            r.item_id = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


class TestItem:
    """Item model with defaults."""

    def test_defaults(self) -> None:
        item = Item(id=11608, name="Deboreka Ring")
        assert item.model_id == "accessory_v1"
        assert item.cron_table == "a"
        assert item.tracked is True
        assert item.category is None

    def test_full_data(self) -> None:
        item = Item(
            id=11608,
            name="Deboreka Ring",
            category="accessories",
            main_category="ring",
            sub_category="ring",
            tracked=False,
            model_id="cron_v1",
            cron_table="b",
        )
        assert item.tracked is False
        assert item.cron_table == "b"

    def test_frozen_rejects_mutation(self) -> None:
        item = Item(id=1, name="test")
        with pytest.raises(ValidationError):
            item.name = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ItemSid
# ---------------------------------------------------------------------------


class TestItemSid:
    """ItemSid model validation and roundtrip."""

    def test_valid_data_roundtrip(self) -> None:
        data = {
            "region": "tw",
            "item_id": 11608,
            "sid": 2,
            "max_enhance": 5,
            "price_min": 100_000_000,
            "price_max": 5_000_000_000,
        }
        obj = ItemSid(**data)
        assert obj.region == "tw"
        assert obj.item_id == 11608
        assert obj.sid == 2
        assert obj.max_enhance == 5

    def test_frozen_rejects_mutation(self) -> None:
        obj = ItemSid(region="tw", item_id=1, sid=0, max_enhance=5, price_min=100, price_max=1000)
        with pytest.raises(ValidationError):
            obj.region = "na"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SnapshotRow
# ---------------------------------------------------------------------------


class TestSnapshotRow:
    """SnapshotRow model validation."""

    def test_valid_data_roundtrip(self) -> None:
        ts = datetime(2024, 5, 30, 8, 0, 0, tzinfo=UTC)
        row = SnapshotRow(
            region="tw",
            snapshot_at=ts,
            item_id=11608,
            sid=0,
            base_price=448_000_000,
            current_stock=100,
            total_trades=5000,
            last_sold_price=445_000_000,
            last_sold_at=ts,
        )
        assert row.region == "tw"
        assert row.item_id == 11608

    def test_frozen_rejects_mutation(self) -> None:
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        row = SnapshotRow(
            region="tw",
            snapshot_at=ts,
            item_id=1,
            sid=0,
            base_price=100,
            current_stock=1,
            total_trades=1,
            last_sold_price=100,
            last_sold_at=ts,
        )
        with pytest.raises(ValidationError):
            row.region = "na"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# DailyRow
# ---------------------------------------------------------------------------


class TestDailyRow:
    """DailyRow model validation."""

    def test_valid_data_roundtrip(self) -> None:
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
        assert row.trade_date == date(2024, 5, 30)
        assert row.snapshot_count == 24

    def test_frozen_rejects_mutation(self) -> None:
        row = DailyRow(
            region="tw",
            trade_date=date(2024, 1, 1),
            item_id=1,
            sid=0,
            open_price=100,
            high_price=100,
            low_price=100,
            close_price=100,
            avg_price=100,
            total_trades_delta=0,
            avg_stock=0,
            snapshot_count=1,
        )
        with pytest.raises(ValidationError):
            row.region = "na"  # type: ignore[misc]
