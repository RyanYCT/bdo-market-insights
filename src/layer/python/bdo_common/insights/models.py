"""Pydantic v2 models for market-insights digest and narrative."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

#: Per-entry price direction, derived from ``pct_change``.
Trend = Literal["up", "down", "flat"]

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
    #: True when the latest close is a statistical outlier vs the item's own
    #: trailing window (|z-score| > analytics.ANOMALY_Z); None when there were
    #: too few daily points to judge. Computed deterministically (ADR-0016).
    anomaly: bool | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def trend(self) -> Trend:
        """Price direction, derived from ``pct_change`` (output-only field)."""
        if self.pct_change > 0:
            return "up"
        if self.pct_change < 0:
            return "down"
        return "flat"


class MoverRef(BaseModel):
    """A reference to one notable item within a digest, used by ``DigestStats``.

    ``value`` is the metric that earned the superlative: ``pct_change`` for the
    top gainer/loser, the coefficient of variation for ``most_volatile``, and the
    daily trade count for ``most_traded``.
    """

    model_config = ConfigDict(frozen=True)

    item_name: str
    sid: int
    value: float


class DigestStats(BaseModel):
    """Deterministic cross-entry summary of a digest (breadth + superlatives).

    Pre-computed in ``build_digest`` so the LLM narrator (and the fallback
    renderer) get explicit headline material and never have to derive or invent
    it. ``None`` on the digest when there are no entries.
    """

    model_config = ConfigDict(frozen=True)

    total: int
    gainers: int
    losers: int
    flat: int
    anomalies: int
    top_gainer: MoverRef | None = None
    top_loser: MoverRef | None = None
    most_volatile: MoverRef | None = None
    most_traded: MoverRef | None = None


class MarketDigest(BaseModel):
    """Deterministic structured digest of market movements."""

    model_config = ConfigDict(frozen=True)

    region: str
    period: Period
    summary_date: date
    top_n: int
    entries: list[DigestEntry]
    #: Cross-entry summary (breadth + superlatives). Optional/defaulted so digests
    #: persisted before this field (e.g. empty "no movement" days) still validate.
    stats: DigestStats | None = None
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


class NarrativeSummary(BaseModel):
    """The LLM's qualitative contribution: headline + overall only.

    Hybrid narration (ADR-0016): the LLM writes the headline and overall summary,
    while the per-item ``categories`` bullets are rendered deterministically from
    the digest -- so exact figures (prices, percentages, enhancement costs) never
    depend on the model. ``insights_summarize`` merges this with the deterministic
    bullets into a full :class:`Narrative`.
    """

    model_config = ConfigDict(frozen=True)

    headline: str
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
