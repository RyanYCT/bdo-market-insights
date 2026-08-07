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


def test_no_execution_fails() -> None:
    ok, msg = verify.data_check(
        lambda: False, lambda: None, wait=100, poll=0, sleep=lambda _s: None
    )
    assert ok is False
    assert "make bootstrap" in msg


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
    ok, _ = verify.check_liveness("https://api.example/dev")
    assert ok is False


def test_liveness_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    ok, msg = verify.check_liveness("https://api.example/dev")
    assert ok is False and "failed" in msg


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
