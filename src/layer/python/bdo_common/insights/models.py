"""Pydantic v2 models for market-insights digest and narrative."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

#: Summary cadence. Reserved for extension (the API and repo validate it too).
Period = Literal["daily", "weekly"]


class DigestEntry(BaseModel):
    """A single item's movement data within a digest."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    item_name: str
    category: str
    sid: int
    close_price: int
    prev_close_price: int
    pct_change: float
    volume: int
    volatility: float | None
    liquidity: float | None
    enhancement_cost_change: float | None


class MarketDigest(BaseModel):
    """Deterministic structured digest of market movements."""

    model_config = ConfigDict(frozen=True)

    region: str
    period: Period
    summary_date: date
    top_n: int
    entries: list[DigestEntry]
    generated_at: datetime


class NarrativeCategory(BaseModel):
    """One category section within the narrative."""

    model_config = ConfigDict(frozen=True)

    category: str
    bullets: list[str]


class Narrative(BaseModel):
    """LLM-generated (or fallback) narrative for a market summary."""

    model_config = ConfigDict(frozen=True)

    headline: str
    categories: list[NarrativeCategory]
    overall: str


class MarketSummary(BaseModel):
    """Full market summary record as stored in the database."""

    model_config = ConfigDict(frozen=True)

    region: str
    period: Period
    summary_date: date
    lang: str
    model_id: str
    digest: MarketDigest
    narrative: Narrative
    created_at: datetime
