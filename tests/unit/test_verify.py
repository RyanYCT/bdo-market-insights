"""Unit tests for scripts/verify.py (post-deploy smoke test).

Covers the execution-aware data wait (the async-bootstrap tolerance) and the
liveness / RDS check parsers. AWS and HTTP are stubbed.
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

_VERIFY_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "verify.py"
_spec = importlib.util.spec_from_file_location("verify_script", _VERIFY_PATH)
assert _spec and _spec.loader
verify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify)


def _seq(*values: Any) -> Callable[[], Any]:
    """Return a zero-arg callable yielding the given values in order."""
    it = iter(values)
    return lambda: next(it)


# ---- data_check: execution-aware wait ---------------------------------------


def test_data_present_immediately() -> None:
    ok, msg = verify.data_check(
        lambda: True, lambda: None, wait=100, poll=0, sleep=lambda _s: None
    )
    assert ok is True
    assert "non-empty" in msg


def test_data_appears_after_bootstrap_running() -> None:
    items = _seq(False, False, True)
    ok, msg = verify.data_check(
        items,
        lambda: {"status": "RUNNING", "executionArn": "arn:x"},
        wait=100,
        poll=0,
        sleep=lambda _s: None,
        now=lambda: 0.0,
    )
    assert ok is True


def test_bootstrap_failed_fails_fast() -> None:
    ok, msg = verify.data_check(
        lambda: False,
        lambda: {"status": "FAILED", "executionArn": "arn:x"},
        wait=100,
        poll=0,
        sleep=lambda _s: None,
    )
    assert ok is False
    assert "FAILED" in msg and "arn:x" in msg


def test_bootstrap_succeeded_but_empty_fails() -> None:
    ok, msg = verify.data_check(
        lambda: False,
        lambda: {"status": "SUCCEEDED", "executionArn": "arn:x"},
        wait=100,
        poll=0,
        sleep=lambda _s: None,
    )
    assert ok is False
    assert "SUCCEEDED" in msg and "empty" in msg


def test_no_execution_past_grace_fails() -> None:
    ok, msg = verify.data_check(
        lambda: False, lambda: None, wait=100, poll=0, grace=0, sleep=lambda _s: None
    )
    assert ok is False
    assert "make bootstrap" in msg


def test_no_execution_within_grace_waits_then_fails() -> None:
    """No execution yet -> keep polling through the grace window, then fail."""
    ok, msg = verify.data_check(
        lambda: False,
        lambda: None,
        wait=100,
        poll=0,
        grace=10,
        sleep=lambda _s: None,
        now=_seq(0.0, 0.0, 5.0, 11.0),
    )
    assert ok is False
    assert "make bootstrap" in msg


def test_execution_appears_within_grace() -> None:
    """Execution lists as absent, then appears RUNNING, then items show up."""
    executions = _seq(None, {"status": "RUNNING", "executionArn": "arn:x"})
    items = _seq(False, False, True)
    ok, _ = verify.data_check(
        items,
        executions,
        wait=100,
        poll=0,
        grace=10,
        sleep=lambda _s: None,
        now=lambda: 0.0,
    )
    assert ok is True


def test_succeeded_recheck_items_passes() -> None:
    """SUCCEEDED with a lagging Scan: the re-check sees items and passes."""
    items = _seq(False, True)
    ok, _ = verify.data_check(
        items,
        lambda: {"status": "SUCCEEDED", "executionArn": "arn:x"},
        wait=100,
        poll=0,
        sleep=lambda _s: None,
    )
    assert ok is True


def test_pending_redrive_treated_as_running() -> None:
    items = _seq(False, True)
    ok, _ = verify.data_check(
        items,
        lambda: {"status": "PENDING_REDRIVE", "executionArn": "arn:x"},
        wait=100,
        poll=0,
        sleep=lambda _s: None,
        now=lambda: 0.0,
    )
    assert ok is True


def test_timeout_while_running() -> None:
    ok, msg = verify.data_check(
        lambda: False,
        lambda: {"status": "RUNNING", "executionArn": "arn:x"},
        wait=0,
        poll=0,
        sleep=lambda _s: None,
        now=_seq(0.0, 1.0, 2.0),
    )
    assert ok is False
    assert "timed out" in msg


# ---- check_liveness ---------------------------------------------------------


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_liveness_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(200))
    ok, msg = verify.check_liveness("https://api.example/dev")
    assert ok is True and "200" in msg


def test_liveness_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(503))
    ok, _ = verify.check_liveness("https://api.example/dev", sleep=lambda _s: None)
    assert ok is False


def test_liveness_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    ok, msg = verify.check_liveness("https://api.example/dev", sleep=lambda _s: None)
    assert ok is False and "failed" in msg


def test_liveness_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient non-200 on the first attempt should not fail liveness."""
    codes = iter([503, 200])
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(next(codes)))
    sleeps: list[float] = []
    ok, msg = verify.check_liveness("https://api.example/dev", sleep=sleeps.append)
    assert ok is True and "200" in msg
    assert len(sleeps) == 1  # backed off once between the two attempts


# ---- check_rds --------------------------------------------------------------


class _Payload:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class _FakeLambda:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def invoke(self, **_kwargs: Any) -> dict[str, Any]:
        return self._response


def test_rds_ok() -> None:
    client = _FakeLambda({"Payload": _Payload(b'{"rows": [[1]], "columns": ["ok"]}')})
    ok, msg = verify.check_rds(client, "dev")
    assert ok is True and "ok" in msg


def test_rds_function_error() -> None:
    client = _FakeLambda(
        {"FunctionError": "Unhandled", "Payload": _Payload(b'{"errorMessage":"x"}')}
    )
    ok, _ = verify.check_rds(client, "dev")
    assert ok is False


def test_rds_no_rows() -> None:
    client = _FakeLambda({"Payload": _Payload(b'{"rows": []}')})
    ok, _ = verify.check_rds(client, "dev")
    assert ok is False



class _RaisingLambda:
    def invoke(self, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("no such function")


def test_rds_invoke_raises_is_reported() -> None:
    ok, msg = verify.check_rds(_RaisingLambda(), "dev")
    assert ok is False and "invoke failed" in msg


def test_rds_bad_json_is_reported() -> None:
    client = _FakeLambda({"Payload": _Payload(b"not-json")})
    ok, msg = verify.check_rds(client, "dev")
    assert ok is False and "invoke failed" in msg


# ---- _items_present ---------------------------------------------------------


class _FakeDdb:
    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self.last_kwargs: dict[str, Any] = {}

    def scan(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        return {"Items": self._items}


def test_items_present_true_uses_consistent_read() -> None:
    ddb = _FakeDdb([{"id": {"S": "x"}}])
    assert verify._items_present(ddb, "bdo-dev-items") is True
    assert ddb.last_kwargs.get("ConsistentRead") is True
    assert ddb.last_kwargs.get("Limit") == 1


def test_items_present_false_when_empty() -> None:
    assert verify._items_present(_FakeDdb([]), "bdo-dev-items") is False


# ---- _latest_bootstrap_execution --------------------------------------------


class _FakeSfn:
    def __init__(self, executions: list[dict[str, str]]) -> None:
        self._executions = executions
        self.last_arn: str | None = None

    def list_executions(self, *, stateMachineArn: str, maxResults: int) -> dict[str, Any]:
        self.last_arn = stateMachineArn
        return {"executions": self._executions}


def test_latest_execution_returns_most_recent() -> None:
    sfn = _FakeSfn([{"status": "RUNNING", "executionArn": "arn:e1"}])
    result = verify._latest_bootstrap_execution(sfn, "arn:sm")
    assert result == {"status": "RUNNING", "executionArn": "arn:e1"}
    assert sfn.last_arn == "arn:sm"


def test_latest_execution_none_when_no_executions() -> None:
    assert verify._latest_bootstrap_execution(_FakeSfn([]), "arn:sm") is None


# ---- _stack_output ----------------------------------------------------------


class _FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def paginate(self) -> list[dict[str, Any]]:
        return self._pages


class _FakeCf:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def get_paginator(self, _name: str) -> _FakePaginator:
        return _FakePaginator(self._pages)


def _stacks_page(name: str, outputs: dict[str, str]) -> dict[str, Any]:
    return {
        "Stacks": [
            {
                "StackName": name,
                "Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in outputs.items()],
            }
        ]
    }


def test_stack_output_found_on_nested_stack() -> None:
    cf = _FakeCf(
        [_stacks_page("bdo-market-dev-BootstrapStack-ABC", {"BootstrapStateMachineArn": "arn:sm"})]
    )
    assert verify._stack_output(cf, "dev", "BootstrapStateMachineArn") == "arn:sm"


def test_stack_output_missing_raises_systemexit() -> None:
    cf = _FakeCf([_stacks_page("bdo-market-dev", {"ApiUrl": "https://api/dev"})])
    with pytest.raises(SystemExit):
        verify._stack_output(cf, "dev", "BootstrapStateMachineArn")


# ---- main -------------------------------------------------------------------


def _patch_main(monkeypatch: pytest.MonkeyPatch, *, data_ok: bool) -> None:
    monkeypatch.setattr(verify.boto3, "client", lambda *_a, **_k: object())
    monkeypatch.setattr(verify, "_stack_output", lambda _cf, _stage, key: f"val:{key}")
    monkeypatch.setattr(verify, "check_liveness", lambda *_a, **_k: (True, "live"))
    monkeypatch.setattr(verify, "check_rds", lambda *_a, **_k: (True, "rds"))
    monkeypatch.setattr(verify, "data_check", lambda *_a, **_k: (data_ok, "data"))
    monkeypatch.setattr("sys.argv", ["verify.py", "--stage", "dev"])


def test_main_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_main(monkeypatch, data_ok=True)
    verify.main()  # should not raise


def test_main_fails_when_a_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_main(monkeypatch, data_ok=False)
    with pytest.raises(SystemExit):
        verify.main()
