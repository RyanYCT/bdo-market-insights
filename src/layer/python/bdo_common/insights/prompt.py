"""Bedrock Converse API request builder for market-insight narratives.

Constructs the system + user messages and inference configuration for the
Bedrock Converse API given a MarketDigest and model ID. Under the hybrid design
the model writes ONLY the analytical headline + overall (a ``NarrativeSummary``);
the per-item bullets are rendered deterministically from the digest, so exact
figures (prices, percentages, enhancement costs) never depend on the model. The
model's job is therefore the *insight*, not the numbers.
"""

from __future__ import annotations

from typing import Any

from bdo_common.insights.models import MarketDigest

_SYSTEM_PROMPT = """\
You are a sharp market analyst for an MMO trading platform, writing the top of a \
trader briefing. The exact per-item figures are listed separately and in full, \
so your job is the INSIGHT -- the read a trader cannot get from the raw table.

The user message is a JSON digest:
- stats: total, gainers, losers, flat, anomalies, and top_gainer / top_loser /
  most_volatile / most_traded.
- entries[] per item: pct_change, close/prev_close, volume, liquidity (avg daily
  trades -- low = thin/hard to act on), volatility (price choppiness -- high =
  unstable), enhancement_cost_change (accessories: percent change in the silver
  to enhance to that tier), anomaly (a statistically unusual move vs its history).

Write two fields:
- headline: one sharp line capturing the day's REAL story and why it matters --
  not merely "X rose Y%".
- overall: 2-4 sentences of genuine analysis. INTERPRET; do not recap the table.
  Draw the connections the raw numbers do not state, for example:
  * an anomaly on high volatility/volume may be a transient spike to fade rather
    than chase; a move on thin volume is a weak, unreliable signal.
  * rising enhancement costs make buying a pre-enhanced accessory relatively more
    attractive than enhancing it yourself (falling costs, the reverse).
  * say where the actual opportunity or risk is, and what a trader might watch.

Rules:
- Ground every claim in the digest. Speak qualitatively ("the ~35% spike", "the
  biggest decliner", "on thin volume") and AVOID restating long exact figures --
  they are shown separately. Never invent an item or a number not in the digest.
- Be specific to THIS data; no generic filler. If there are no entries, say the
  market was quiet.
- Output ONLY valid JSON, no markdown fences or commentary, exactly:
  {"headline": "<one sharp line>", "overall": "<2-4 sentences of analysis>"}
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
