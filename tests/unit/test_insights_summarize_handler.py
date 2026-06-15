"""Unit tests for the insightsSummarize Lambda handler."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from bdo_common.insights.models import MarketDigest, Narrative, NarrativeCategory


def _make_digest() -> MarketDigest:
    return MarketDigest(
        region="tw",
        period="daily",
        summary_date=date(2026, 6, 13),
        top_n=5,
        entries=[],
        generated_at=datetime(2026, 6, 14, 1, 0, 0, tzinfo=UTC),
    )


def _make_narrative() -> Narrative:
    return Narrative(
        headline="Market rallies on accessory demand",
        categories=[NarrativeCategory(category="accessory", bullets=["Ogre Ring +8.2%"])],
        overall="Bullish day with accessory prices leading gains.",
    )


def _make_event(digest: MarketDigest) -> dict[str, Any]:
    return {
        "region": "tw",
        "period": "daily",
        "target_date": "2026-06-13",
        "digest": digest.model_dump(mode="json"),
    }


def _converse_response(narrative: Narrative) -> dict[str, Any]:
    """Build a mock Bedrock Converse response containing the narrative JSON."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": narrative.model_dump_json()}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }


@pytest.fixture
def mod(
    load_handler: Callable[[str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")
    module = load_handler("insights_summarize")
    return module


def test_successful_bedrock_response(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid Bedrock response returns parsed narrative and model_id."""
    digest = _make_digest()
    narrative = _make_narrative()
    mock_client = MagicMock()
    mock_client.converse.return_value = _converse_response(narrative)
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is not None
    assert result["narrative"]["headline"] == "Market rallies on accessory demand"
    assert result["model_id"] == "us.amazon.nova-lite-v1:0"
    assert result["region"] == "tw"
    assert result["period"] == "daily"
    assert result["target_date"] == "2026-06-13"
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
    mock_client.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "This is not valid JSON at all"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is None
    assert result["model_id"] == "deterministic-v1"


def test_valid_json_but_invalid_schema_returns_fallback(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JSON that doesn't match Narrative schema returns narrative=null."""
    digest = _make_digest()
    mock_client = MagicMock()
    # Valid JSON but missing required fields
    mock_client.converse.return_value = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": json.dumps({"headline": "ok"})}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 50},
    }
    monkeypatch.setattr(mod, "bedrock_client", mock_client)

    result = mod.handler(_make_event(digest), lambda_context)

    assert result["narrative"] is None
    assert result["model_id"] == "deterministic-v1"
