"""Pydantic v2 schemas for all I/O boundaries."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    """Normalized arsha.io GetWorldMarketSubList row.

    Maps arsha.io v2 JSON fields (camelCase) onto snake_case attributes.
    One Record corresponds to a single (item_id, sid) pair. The price_min,
    price_max and max_enhance fields feed the ``item_sid`` reference table;
    the remaining fields feed ``market_snapshot``.
    """

    model_config = ConfigDict(frozen=True)

    item_id: int  # arsha "id"
    sid: int  # arsha "sid" (enhancement level)
    name: str  # arsha "name"
    base_price: int  # arsha "basePrice"
    current_stock: int  # arsha "currentStock"
    total_trades: int  # arsha "totalTrades"
    last_sold_price: int  # arsha "lastSoldPrice"
    last_sold_at: datetime  # arsha "lastSoldTime" (unix seconds)
    max_enhance: int  # arsha "maxEnhance"
    price_min: int  # arsha "priceMin" (system bid floor)
    price_max: int  # arsha "priceMax" (system ask ceiling)


class CatalogEntry(BaseModel):
    """Normalized arsha.io ``util/db`` row: one item's catalog metadata.

    ``util/db?lang=<lang>`` returns every known BDO item as
    ``{id, name, grade}``. ``grade`` is language-independent; ``name`` is
    localized by ``lang``. Feeds the DynamoDB item catalog.
    """

    model_config = ConfigDict(frozen=True)

    item_id: int  # arsha "id"
    name: str  # arsha "name" (localized by lang)
    grade: int | None = None  # arsha "grade"; open-ended, may be absent


class Item(BaseModel):
    """DynamoDB item (bdo-<stage>-items table).

    Holds every known BDO item (the full catalog synced from arsha ``util/db``),
    not only the subset the ETL polls; ``tracked`` distinguishes the two. Name
    localization keeps English on ``name`` (the guaranteed baseline) and any
    other language in the ``names`` map.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    name: str  # canonical English name; guaranteed-present baseline
    # Localized names, e.g. {"tw": "..."}; English stays on ``name``.
    names: dict[str, str] = Field(default_factory=dict)
    # Raw BDO grade code (0=White, 1=Green, 2=Blue, 3=Gold, 4=Orange, 5=Violet, ...).
    # Open-ended: no closed enum, so future grades still store/read.
    grade: int | None = None
    category: str | None = None
    main_category: str | None = None
    sub_category: str | None = None
    tracked: bool = True
    model_id: str = "accessory_v1"
    cron_table: Literal["a", "b"] = "a"
    icon_status: Literal["unset", "stored", "missing"] = "unset"  # S3 icon materialization state
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def display_name(self, lang: str = "en") -> str:
        """Return the localized name for ``lang``, falling back to English.

        English always resolves to ``name``; any other language resolves to
        ``names[lang]`` when present, otherwise to the English ``name``.
        """
        if lang == "en":
            return self.name
        return self.names.get(lang, self.name)


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
