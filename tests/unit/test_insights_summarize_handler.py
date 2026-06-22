"""Unit tests for the insightsSummarize Lambda handler (hybrid narration)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from bdo_common.insights.models import DigestEntry, MarketDigest


def _make_digest() -> MarketDigest:
    """A digest WITH entries, so insights_summarize actually calls Bedrock."""
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 6, 13),
        top_n=5,
        entries=[
            DigestEntry(
                item_id=1,
                item_name="Ogre Ring",
                category="accessory",
                sid=3,
                close_price=1_000_000_000,
                prev_close_price=924_000_000,
                pct_change=8.2,
                volume=42,
                volatility=0.05,
                liquidity=42.0,
                enhancement_cost_change=3.1,
            )
        ],
        generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
    )


def _make_empty_digest() -> MarketDigest:
    """A digest with no entries (e.g. no tracked items / no movers)."""
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 6, 13),
        top_n=5,
        entries=[],
        generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
    )


_HEADLINE = "Market rallies on accessory demand"
_OVERALL = "Bullish day with accessory prices leading gains."


def _summary_text(headline: str = _HEADLINE, overall: str = _OVERALL) -> str:
    """The LLM now returns ONLY {headline, overall} (a NarrativeSummary)."""
    return json.dumps({"headline": headline, "overall": overall})


def _converse_response(text: str) -> dict[str, Any]:
    """Build a mock Bedrock Converse response wrapping ``text``."""
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }


def _make_event(digest: MarketDigest) -> dict[str, Any]:
    return {
        "region": "tw",
        "period": "daily",
        "target_date": "2026-06-13",
        "digest": digest.model_dump(mode="json"),
    }


@pytest.fixture
def mod(
    load_handler: Callable[[str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
    module = load_handler("insights_summarize")
    return module


def test_successful_response_uses_llm_header_and_deterministic_bullets(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LLM headline + overall are used verbatim; the per-item bullets come
    from the deterministic renderer (the digest), not the model."""
    digest = _make_digest()
    mock_client = MagicMock()
    mock_client.converse.return_value = _converse_response(_summary_text())
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    narrative = result["narrative"]
    assert narrative is not None
    assert narrative["headline"] == _HEADLINE
    assert narrative["overall"] == _OVERALL
    assert result["model_id"] == "us.amazon.nova-lite-v1:0"
    # Bullets are deterministic: derived from the digest, exact figures.
    assert narrative["categories"]
    rendered = json.dumps(narrative["categories"])
    assert "Ogre Ring" in rendered
    assert "+8.2%" in rendered
    # The LLM's response did not contain any bullets/categories.
    assert result["region"] == "tw"
    assert result["digest"] == digest.model_dump(mode="json")


def test_bedrock_client_error_returns_fallback(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bedrock client error returns narrative=null for fallback."""
    digest = _make_digest()
    mock_client = MagicMock()
    mock_client.converse.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "Converse",
    )
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is None
    assert result["model_id"] == "deterministic-v1"
    assert result["region"] == "tw"


def test_invalid_json_response_returns_fallback(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid JSON from Bedrock returns narrative=null for fallback."""
    digest = _make_digest()
    mock_client = MagicMock()
    mock_client.converse.return_value = _converse_response("This is not valid JSON at all")
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is None
    assert result["model_id"] == "deterministic-v1"


def test_empty_digest_skips_bedrock(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty digest must NOT call Bedrock (no facts to narrate -> hallucination
    risk). It returns narrative=null so insights_store renders the deterministic
    'no movements' summary."""
    digest = _make_empty_digest()
    mock_client = MagicMock()
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    mock_client.converse.assert_not_called()
    assert result["narrative"] is None
    assert result["model_id"] == "deterministic-v1"
    assert result["region"] == "tw"
    assert result["period"] == "daily"
    assert result["target_date"] == "2026-06-13"
    assert result["digest"] == digest.model_dump(mode="json")


def test_strips_markdown_fences_and_prose(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A response wrapping the JSON in ```json fences (and prose) still parses."""
    digest = _make_digest()
    fenced = f"Here is the summary:\n\n```json\n{_summary_text()}\n```\n"
    mock_client = MagicMock()
    mock_client.converse.return_value = _converse_response(fenced)
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is not None
    assert result["narrative"]["headline"] == _HEADLINE
    assert result["model_id"] == "us.amazon.nova-lite-v1:0"


def test_valid_json_but_invalid_schema_returns_fallback(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON missing a required field (overall) returns narrative=null."""
    digest = _make_digest()
    mock_client = MagicMock()
    mock_client.converse.return_value = _converse_response(json.dumps({"headline": "ok"}))
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is None
    assert result["model_id"] == "deterministic-v1"
