"""Bedrock Converse API request builder for market-insight narratives.

Constructs the system + user messages and inference configuration for the
Bedrock Converse API given a MarketDigest and model ID. Under the hybrid design
the model writes ONLY the qualitative headline + overall (a ``NarrativeSummary``);
the per-item bullets are rendered deterministically from the digest, so exact
figures (prices, percentages, enhancement costs) never depend on the model.
"""

from __future__ import annotations

from typing import Any

from bdo_common.insights.models import MarketDigest

_SYSTEM_PROMPT = """\
You are a market analyst for an MMO trading platform. From a structured market \
digest, write a short, punchy briefing header for traders.

The user message is a JSON digest containing `stats` (precomputed: total,
gainers, losers, flat, anomalies, top_gainer, top_loser, most_volatile,
most_traded) and `entries` (per-item moves). The per-item detail is rendered
separately, so your job is ONLY two fields:
- headline: one line leading with the single biggest story -- usually
  stats.top_gainer, stats.top_loser, or a striking anomaly. Specific, not generic.
- overall: 1-2 sentences on breadth (how many up vs down, any anomalies) and the
  net market sentiment.

Rules:
- Use ONLY figures present in the digest/stats; never invent or recompute a
  number. Take any figure you cite verbatim from stats and round it naturally
  (e.g. "35%"). If there are no entries, say the market was quiet -- do not
  fabricate items.
- Do NOT produce a per-item or per-tier breakdown -- just the two fields.
- Output ONLY valid JSON, with no markdown fences or commentary, exactly:
  {"headline": "<one line>", "overall": "<1-2 sentences>"}
"""


def build_converse_request(digest: MarketDigest, model_id: str) -> dict[str, Any]:
    """Build kwargs for ``bedrock_client.converse(**kwargs)``.

    Parameters
    ----------
    digest:
        The structured market digest to narrate.
    model_id:
        Bedrock model identifier (e.g. ``us.amazon.nova-lite-v1:0``).

    Returns
    -------
    dict
        Keyword arguments ready to spread into ``client.converse()``.
    """
    user_content = digest.model_dump_json()

    return {
        "modelId": model_id,
        "system": [{"text": _SYSTEM_PROMPT}],
        "messages": [
            {
                "role": "user",
                "content": [{"text": user_content}],
            },
        ],
        "inferenceConfig": {
            "temperature": 0.3,
            "maxTokens": 512,
        },
    }
