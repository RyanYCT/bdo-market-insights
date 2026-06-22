"""Unit tests for bdo_common.insights.prompt — Converse request builder."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from bdo_common.insights.models import MarketDigest
from bdo_common.insights.prompt import build_converse_request


def _make_digest() -> MarketDigest:
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 6, 13),
        top_n=5,
        entries=[],
        generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
    )


def test_returns_correct_model_id() -> None:
    digest = _make_digest()
    result = build_converse_request(digest, "us.amazon.nova-lite-v1:0")
    assert result["modelId"] == "us.amazon.nova-lite-v1:0"


def test_has_system_message() -> None:
    digest = _make_digest()
    result = build_converse_request(digest, "us.amazon.nova-lite-v1:0")

    assert "system" in result
    assert len(result["system"]) == 1
    assert "text" in result["system"][0]
    # System prompt should mention JSON and never invent numbers
    system_text = result["system"][0]["text"]
    assert "JSON" in system_text
    assert "never" in system_text.lower() or "NEVER" in system_text


def test_has_user_message_with_digest_json() -> None:
    digest = _make_digest()
    result = build_converse_request(digest, "us.amazon.nova-lite-v1:0")

    assert "messages" in result
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert msg["role"] == "user"
    assert len(msg["content"]) == 1

    user_text = msg["content"][0]["text"]
    # Should be valid JSON matching the digest
    parsed = json.loads(user_text)
    assert parsed["region"] == "tw"
    assert parsed["period"] == "daily"


def test_inference_config_temperature_and_max_tokens() -> None:
    digest = _make_digest()
    result = build_converse_request(digest, "us.amazon.nova-lite-v1:0")

    assert "inferenceConfig" in result
    config = result["inferenceConfig"]
    assert config["temperature"] == 0.3
    assert config["maxTokens"] == 1024


def test_system_prompt_directs_interpretation_of_signals() -> None:
    """Option A: the prompt should reference the digest signals and stats so the
    model interprets them rather than restating raw numbers."""
    result = build_converse_request(_make_digest(), "us.amazon.nova-lite-v1:0")
    system_text = result["system"][0]["text"].lower()

    for token in ("anomaly", "stats", "volatility", "liquidity", "enhancement_cost_change"):
        assert token in system_text
    # Must still forbid inventing values.
    assert "only" in system_text


def test_system_prompt_polish_rules() -> None:
    """Narration-polish rules: compact silver, never claim 'no volatility', and
    state enhancement-cost moves from the authoritative precomputed list."""
    result = build_converse_request(_make_digest(), "us.amazon.nova-lite-v1:0")
    system_text = result["system"][0]["text"].lower()

    # Compact silver formatting is demonstrated (e.g. "675k").
    assert "675k" in system_text
    # Guard against the "no volatility" misread observed in evaluation.
    assert "no volatility" in system_text
    # Enhancement-cost moves are taken verbatim from the precomputed list, so a
    # small model can't mis-map a tier to the wrong %.
    assert "enhancement_cost_movers" in system_text
    assert "exactly" in system_text
