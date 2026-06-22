"""Bedrock Converse API request builder for market-insight narratives.

Constructs the system + user messages and inference configuration for the
Bedrock Converse API given a MarketDigest and model ID. The system prompt
instructs the model to produce *only* valid JSON matching the Narrative schema.
"""

from __future__ import annotations

from typing import Any

from bdo_common.insights.models import MarketDigest

_SYSTEM_PROMPT = """\
You are a sharp market analyst for an MMO trading platform. You turn a \
structured market digest into a short, useful briefing for traders.

The user message is a JSON digest. Its fields (use ONLY these, never anything else):
- entries[]: the day's notable items, each with:
  - item_name, category, sid (enhancement tier; 0 = base item)
  - pct_change: percent move of close_price vs prev_close_price
  - close_price / prev_close_price: latest and prior close, in silver
  - volume: that day's trade count
  - trend: "up" | "down" | "flat" (derived from pct_change)
  - volatility: coefficient of variation over the item's recent window
    (higher = choppier/less stable price; may be null)
  - liquidity: average daily trades over the window
    (higher = easier to buy/sell; may be null)
  - enhancement_cost_change: percent change in the expected silver to enhance
    an accessory to its tier (accessories only; null otherwise)
  - anomaly: true when this move is a statistical outlier vs the item's own
    history (a genuinely unusual move); may be null
- stats: a precomputed summary -- total, gainers, losers, flat, anomalies,
  and top_gainer / top_loser / most_volatile / most_traded (each {item_name,
  sid, value}). Use these to anchor the headline and overall.

How to write it:
- headline: lead with the single biggest story -- usually stats.top_gainer or
  top_loser, or a striking anomaly. Make it specific, not generic.
- For each category, write bullets that INTERPRET the numbers, don't just
  restate them. Combine signals into a takeaway, e.g.: tie an accessory's price
  move to its enhancement_cost_change; flag an anomaly as an unusual move worth
  attention; read high volatility as an unstable/risky price and thin liquidity
  as hard to fill; note when a big move comes on low volume (less conviction).
- overall: 1-2 sentences on breadth (how many up vs down, any anomalies) and the
  net sentiment.

Hard rules:
- NEVER invent, recompute, or hallucinate values. Use ONLY the figures in the
  digest. If a field is null, do not mention it. If entries is empty, say so
  plainly and do not fabricate items.
- Interpretation and framing are encouraged; new numbers are not.
- Output ONLY valid JSON (no markdown fences, no commentary) conforming exactly to:
  {
    "headline": "<one-line market headline>",
    "categories": [
      {"category": "<category name>", "bullets": ["<bullet>", ...]}
    ],
    "overall": "<1-2 sentence overall summary>"
  }
- Keep each bullet to one sentence.
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
            "maxTokens": 1024,
        },
    }
