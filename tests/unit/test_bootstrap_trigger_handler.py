"""Unit tests for the bootstrapTrigger custom-resource handler.

DynamoDB, Step Functions, and the CloudFormation response POST are stubbed. The
tests assert: Create + empty table starts the state machine and signals SUCCESS;
Create + non-empty skips StartExecution; Delete is a no-op; a start failure
signals FAILED.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def trigger(load_handler: Callable[[str], ModuleType]) -> ModuleType:
    return load_handler("bootstrap_trigger")


@pytest.fixture
def cfn_response(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the JSON body PUT to the CloudFormation ResponseURL."""
    sent: list[dict[str, Any]] = []

    def _fake_urlopen(req: Any, timeout: int = 30) -> Any:
        sent.append(json.loads(req.data.decode("utf-8")))
        return None

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return sent


class _FakeSfn:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self._calls = calls

    def start_execution(self, **kwargs: Any) -> dict[str, Any]:
        self._calls.append(kwargs)
        return {"executionArn": "arn:aws:states:us-east-1:123:execution:bdo-dev-bootstrap:x"}


def _event(request_type: str) -> dict[str, Any]:
    return {
        "RequestType": request_type,
        "ResponseURL": "https://cfn-response.example.s3.amazonaws.com/signed",
        "StackId": "arn:aws:cloudformation:us-east-1:123:stack/bdo/abc",
        "RequestId": "req-1",
        "LogicalResourceId": "BootstrapTrigger",
    }


def _wire(
    trigger: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    empty: bool,
    start_calls: list[dict[str, Any]],
) -> None:
    monkeypatch.setenv("STAGE", "dev")
    monkeypatch.setenv(
        "BOOTSTRAP_STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:123:stateMachine:bdo-dev-bootstrap",
    )
    from bdo_common import dynamo

    monkeypatch.setattr(dynamo, "catalog_is_empty", lambda: empty)
    monkeypatch.setattr("boto3.client", lambda *a, **k: _FakeSfn(start_calls))


def test_create_empty_starts_bootstrap_and_signals_success(
    trigger: ModuleType,
    lambda_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    cfn_response: list[dict[str, Any]],
) -> None:
    starts: list[dict[str, Any]] = []
    _wire(trigger, monkeypatch, empty=True, start_calls=starts)

    trigger.handler(_event("Create"), lambda_context)

    assert len(starts) == 1  # StartExecution called once
    assert len(cfn_response) == 1
    assert cfn_response[0]["Status"] == "SUCCESS"
    assert "execution" in cfn_response[0]["Data"]["bootstrap"]


def test_create_non_empty_skips_start(
    trigger: ModuleType,
    lambda_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    cfn_response: list[dict[str, Any]],
) -> None:
    starts: list[dict[str, Any]] = []
    _wire(trigger, monkeypatch, empty=False, start_calls=starts)

    trigger.handler(_event("Create"), lambda_context)

    assert starts == []  # guard skipped the start
    assert cfn_response[0]["Status"] == "SUCCESS"
    assert cfn_response[0]["Data"]["bootstrap"] == "skipped: catalog not empty"


def test_delete_is_noop_success(
    trigger: ModuleType,
    lambda_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    cfn_response: list[dict[str, Any]],
) -> None:
    starts: list[dict[str, Any]] = []
    _wire(trigger, monkeypatch, empty=True, start_calls=starts)

    trigger.handler(_event("Delete"), lambda_context)

    assert starts == []  # never seed/tear down on delete
    assert cfn_response[0]["Status"] == "SUCCESS"
    assert cfn_response[0]["Data"]["bootstrap"] == "noop"


def test_start_failure_signals_failed(
    trigger: ModuleType,
    lambda_context: Any,
    monkeypatch: pytest.MonkeyPatch,
    cfn_response: list[dict[str, Any]],
) -> None:
    monkeypatch.setenv("STAGE", "dev")
    monkeypatch.setenv(
        "BOOTSTRAP_STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:123:stateMachine:bdo-dev-bootstrap",
    )
    from bdo_common import dynamo

    monkeypatch.setattr(dynamo, "catalog_is_empty", lambda: True)

    class _BoomSfn:
        def start_execution(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("access denied")

    monkeypatch.setattr("boto3.client", lambda *a, **k: _BoomSfn())

    # Must not raise: always signals CloudFormation.
    trigger.handler(_event("Create"), lambda_context)

    assert cfn_response[0]["Status"] == "FAILED"
    assert "access denied" in cfn_response[0]["Reason"]
