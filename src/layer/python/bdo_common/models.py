"""Pydantic v2 schemas for all I/O boundaries."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    """Normalized arsha.io response row."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    sid: int
    base_price: int
    current_stock: int
    total_trades: int
    last_sold_price: int
    last_sold_at: datetime


class Item(BaseModel):
    """DynamoDB item (bdo-v3-items table)."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    category: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    tracked: bool = True
    model_id: str = "accessory_v1"
    cron_table: Literal["a", "b"] = "a"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ItemSid(BaseModel):
    """Per-(region, item, sid) reference row."""

    model_config = ConfigDict(frozen=True)

    region: str
    item_id: int
    sid: int
    max_enhance: int
    price_min: int
    price_max: int
    updated_at: datetime | None = None


class SnapshotRow(BaseModel):
    """market_snapshot table row."""

    model_config = ConfigDict(frozen=True)

    region: str
    snapshot_at: datetime
    item_id: int
    sid: int
    base_price: int
    current_stock: int
    total_trades: int
    last_sold_price: int
    last_sold_at: datetime


class DailyRow(BaseModel):
    """market_daily table row."""

    model_config = ConfigDict(frozen=True)

    region: str
    trade_date: date
    item_id: int
    sid: int
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    avg_price: int
    total_trades_delta: int
    avg_stock: int
    snapshot_count: int
