"""Unit tests for the insightsDiscord Lambda handler."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest


def _make_sns_event(message: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a realistic SNS event payload."""
    if message is None:
        message = {
            "region": "tw",
            "period": "daily",
            "target_date": "2026-06-14",
            "model_id": "us.amazon.nova-lite-v1:0",
            "status": "stored",
            "headline": "Market rallies on accessory demand",
        }
    return {
        "Records": [
            {
                "EventSource": "aws:sns",
                "EventVersion": "1.0",
                "Sns": {
                    "Type": "Notification",
                    "MessageId": "test-message-id",
                    "TopicArn": "arn:aws:sns:us-east-1:123456789012:bdo-dev-insights",
                    "Subject": None,
                    "Message": json.dumps(message),
                    "Timestamp": "2026-06-14T01:00:00.000Z",
                    "MessageAttributes": {},
                },
            }
        ]
    }


@pytest.fixture
def mod(
    load_handler: Callable[[str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("STAGE", "dev")
    with patch("aws_lambda_powertools.utilities.parameters.get_parameter") as mock_param:
        mock_param.return_value = "https://discord.com/api/webhooks/test/token"
        module = load_handler("insights_discord")
    return module


def test_successful_discord_post(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful POST to Discord returns ok status and uses headline from message."""
    mock_urlopen = MagicMock()
    monkeypatch.setattr(mod.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(
        mod.parameters,
        "get_parameter",
        lambda name, decrypt=False: "https://discord.com/api/webhooks/test/token",
    )

    result = mod.handler(_make_sns_event(), lambda_context)

    assert result == {"status": "ok"}
    mock_urlopen.assert_called_once()
    # Verify the request body contains the headline from the SNS message
    call_args = mock_urlopen.call_args
    request_obj = call_args[0][0]
    body = json.loads(request_obj.data.decode("utf-8"))
    assert body["embeds"][0]["title"] == "Market rallies on accessory demand"


def test_fallback_headline_when_missing(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When headline is missing from message, falls back to generic title."""
    mock_urlopen = MagicMock()
    monkeypatch.setattr(mod.urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(
        mod.parameters,
        "get_parameter",
        lambda name, decrypt=False: "https://discord.com/api/webhooks/test/token",
    )

    message_no_headline = {
        "region": "tw",
        "period": "daily",
        "target_date": "2026-06-14",
        "model_id": "us.amazon.nova-lite-v1:0",
        "status": "stored",
    }
    result = mod.handler(_make_sns_event(message_no_headline), lambda_context)

    assert result == {"status": "ok"}
    mock_urlopen.assert_called_once()
    call_args = mock_urlopen.call_args
    request_obj = call_args[0][0]
    body = json.loads(request_obj.data.decode("utf-8"))
    assert body["embeds"][0]["title"] == "New market insight available"


def test_post_failure_does_not_raise(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """URLError on POST does not raise; emits DiscordDeliveryFailures metric."""
    monkeypatch.setattr(
        mod.parameters,
        "get_parameter",
        lambda name, decrypt=False: "https://discord.com/api/webhooks/test/token",
    )
    monkeypatch.setattr(
        mod.urllib.request, "urlopen", MagicMock(side_effect=URLError("Connection refused"))
    )

    metric_calls: list[dict[str, Any]] = []

    def capture_metric(**kwargs: Any) -> None:
        metric_calls.append(kwargs)

    monkeypatch.setattr(mod.metrics, "add_metric", capture_metric)

    # Must NOT raise
    result = mod.handler(_make_sns_event(), lambda_context)

    assert result == {"status": "ok"}
    assert any(m["name"] == "DiscordDeliveryFailures" for m in metric_calls)


def test_ssm_failure_does_not_raise(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSM parameter retrieval failure does not raise."""
    monkeypatch.setattr(
        mod.parameters,
        "get_parameter",
        MagicMock(side_effect=Exception("SSM unavailable")),
    )

    metric_calls: list[dict[str, Any]] = []

    def capture_metric(**kwargs: Any) -> None:
        metric_calls.append(kwargs)

    monkeypatch.setattr(mod.metrics, "add_metric", capture_metric)

    # Must NOT raise
    result = mod.handler(_make_sns_event(), lambda_context)

    assert result == {"status": "ok"}
    assert any(m["name"] == "DiscordDeliveryFailures" for m in metric_calls)


def test_non_https_webhook_does_not_raise(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-https webhook URL is rejected before urlopen; no raise, metric emitted."""
    monkeypatch.setattr(
        mod.parameters,
        "get_parameter",
        lambda name, decrypt=False: "http://insecure.example/webhook",
    )
    mock_urlopen = MagicMock()
    monkeypatch.setattr(mod.urllib.request, "urlopen", mock_urlopen)

    metric_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(mod.metrics, "add_metric", lambda **kw: metric_calls.append(kw))

    result = mod.handler(_make_sns_event(), lambda_context)

    assert result == {"status": "ok"}
    # The scheme guard fires before any network call.
    mock_urlopen.assert_not_called()
    assert any(m["name"] == "DiscordDeliveryFailures" for m in metric_calls)


def test_malformed_sns_message_does_not_raise(
    mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed SNS message does not raise."""
    event: dict[str, Any] = {
        "Records": [
            {
                "Sns": {
                    "Message": "not valid json {{{}",
                }
            }
        ]
    }

    metric_calls: list[dict[str, Any]] = []

    def capture_metric(**kwargs: Any) -> None:
        metric_calls.append(kwargs)

    monkeypatch.setattr(mod.metrics, "add_metric", capture_metric)

    result = mod.handler(event, lambda_context)

    assert result == {"status": "ok"}
    assert any(m["name"] == "DiscordDeliveryFailures" for m in metric_calls)
