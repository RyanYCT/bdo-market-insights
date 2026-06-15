"""Bedrock Converse API request builder for market-insight narratives.

Constructs the system + user messages and inference configuration for the
Bedrock Converse API given a MarketDigest and model ID. The system prompt
instructs the model to produce *only* valid JSON matching the Narrative schema.
"""

from __future__ import annotations

from typing import Any

from bdo_common.insights.models import MarketDigest

_SYSTEM_PROMPT = """\
You are a concise market analyst for an MMO trading platform. \
Your job is to narrate structured market data for traders.

Rules:
- Write in a direct, trader-friendly style.
- NEVER invent, recompute, or hallucinate numbers. Use ONLY the figures provided in the user data.
- Output ONLY valid JSON (no markdown fences, no commentary).
- The JSON must conform to this schema exactly:
  {
    "headline": "<one-line market headline>",
    "categories": [
      {
        "category": "<category name>",
        "bullets": ["<bullet 1>", "<bullet 2>", ...]
      }
    ],
    "overall": "<1-2 sentence overall summary>"
  }
- Keep each bullet to one sentence.
- The overall summary should capture the day's market sentiment in plain English.
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
