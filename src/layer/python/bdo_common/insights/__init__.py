"""LLM market-insights subpackage."""

from bdo_common.insights.digest import build_digest
from bdo_common.insights.models import (
    DigestEntry,
    MarketDigest,
    MarketSummary,
    Narrative,
    NarrativeCategory,
)
from bdo_common.insights.narrative import render_narrative
from bdo_common.insights.prompt import build_converse_request

__all__ = [
    "DigestEntry",
    "MarketDigest",
    "MarketSummary",
    "Narrative",
    "NarrativeCategory",
    "build_converse_request",
    "build_digest",
    "render_narrative",
]
