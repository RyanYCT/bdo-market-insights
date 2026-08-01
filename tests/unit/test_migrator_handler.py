"""Unit tests for the in-VPC migrator Lambda handler.

Alembic, the RDS token call, the revision lookup, the advisory-lock engine, and
(for the custom-resource paths) the HTTP response are all stubbed. The tests
assert the handler runs the right ``upgrade`` target with a properly escaped
psycopg-v3 URL, serializes under a ``pg_advisory_lock``, and always signals
CloudFormation.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.engine import make_url


@pytest.fixture
def migrator(load_handler: Callable[[str], ModuleType]) -> ModuleType:
    return load_handler("migrator")


class _FakeConn:
    """Records the SQL executed on the advisory-lock connection."""

    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def execution_options(self, **_kwargs: Any) -> _FakeConn:
        return self

    def execute(self, stmt: Any, params: Any = None) -> None:
        self._calls.append((str(stmt), params))

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def connect(self) -> _FakeConn:
        return _FakeConn(self._calls)

    def dispose(self) -> None:
        pass


@pytest.fixture
def stubbed(migrator: ModuleType, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the DB-touching dependencies and capture what the handler does."""
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "bdo")
    monkeypatch.setenv("DB_USER", "lambda_migrator")
    monkeypatch.setenv("USE_IAM_AUTH", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("STAGE", "dev")

    from bdo_common.config import get_settings

    get_settings.cache_clear()

    captured: dict[str, Any] = {"token_kwargs": {}, "upgrade_calls": [], "sql": []}

    # A token with URL-hostile characters proves it is escaped, not interpolated.
    raw_token = "tok/with+special=chars&x"

    class _FakeRdsClient:
        def generate_db_auth_token(self, **kwargs: Any) -> str:
            captured["token_kwargs"] = kwargs
            return raw_token

    monkeypatch.setattr("boto3.client", lambda *a, **k: _FakeRdsClient())
    monkeypatch.setattr(
        "alembic.command.upgrade",
        lambda cfg, revision: captured["upgrade_calls"].append((cfg, revision)),
    )

    class _FakeScriptDir:
        def get_current_head(self) -> str:
            return "0004"

    monkeypatch.setattr(
        "alembic.script.ScriptDirectory.from_config",
        classmethod(lambda cls, cfg: _FakeScriptDir()),
    )
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **k: _FakeEngine(captured["sql"]))

    captured["raw_token"] = raw_token
    return captured


def _assert_advisory_lock_taken(sql_calls: list[tuple[str, Any]]) -> None:
    joined = " ".join(sql for sql, _ in sql_calls)
    assert "pg_advisory_lock" in joined
    assert "pg_advisory_unlock" in joined


def test_routine_invoke_runs_upgrade_head_under_lock(
    migrator: ModuleType, lambda_context: Any, stubbed: dict[str, Any]
) -> None:
    result = migrator.handler({}, lambda_context)

    assert result == {"status": "ok", "head": "0004"}
    assert stubbed["upgrade_calls"] and stubbed["upgrade_calls"][0][1] == "head"
    _assert_advisory_lock_taken(stubbed["sql"])

    # IAM token requested for the privileged migrator role on the right host.
    assert stubbed["token_kwargs"]["DBUsername"] == "lambda_migrator"
    assert stubbed["token_kwargs"]["DBHostname"] == "db.internal"
    assert stubbed["token_kwargs"]["Port"] == 5432

    # The connection URL uses psycopg v3, requires SSL, and round-trips the
    # token (i.e. it was percent-encoded, not naively interpolated).
    url = make_url(os.environ["DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "lambda_migrator"
    assert url.password == stubbed["raw_token"]
    assert url.host == "db.internal"
    assert url.database == "bdo"
    assert url.query.get("sslmode") == "require"


def _cfn_event(request_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "RequestType": request_type,
        "ResponseURL": "https://cfn-response.example.s3.amazonaws.com/signed",
        "StackId": "arn:aws:cloudformation:us-east-1:123:stack/bdo/abc",
        "RequestId": "req-1",
        "LogicalResourceId": "SchemaMigration",
        "ResourceProperties": {"MigrationsFingerprint": "abc123"},
        **extra,
    }


@pytest.fixture
def cfn_response(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the JSON body PUT to the CloudFormation ResponseURL."""
    sent: list[dict[str, Any]] = []

    def _fake_urlopen(req: Any, timeout: int = 30) -> Any:
        sent.append(json.loads(req.data.decode("utf-8")))

        class _Resp:
            def __enter__(self) -> _Resp:
                return self

            def __exit__(self, *_exc: Any) -> None:
                return None

        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return sent


def test_custom_resource_create_runs_upgrade_and_signals_success(
    migrator: ModuleType,
    lambda_context: Any,
    stubbed: dict[str, Any],
    cfn_response: list[dict[str, Any]],
) -> None:
    migrator.handler(_cfn_event("Create"), lambda_context)

    assert stubbed["upgrade_calls"] and stubbed["upgrade_calls"][0][1] == "head"
    _assert_advisory_lock_taken(stubbed["sql"])
    assert len(cfn_response) == 1
    body = cfn_response[0]
    assert body["Status"] == "SUCCESS"
    assert body["Data"]["head"] == "0004"
    assert body["LogicalResourceId"] == "SchemaMigration"
    # Stable physical id so an update is not treated as replace+delete.
    assert body["PhysicalResourceId"] == "bdo-dev-schema-migration"


def test_custom_resource_delete_is_noop_success(
    migrator: ModuleType,
    lambda_context: Any,
    stubbed: dict[str, Any],
    cfn_response: list[dict[str, Any]],
) -> None:
    migrator.handler(_cfn_event("Delete"), lambda_context)

    # Delete must never drop the schema.
    assert stubbed["upgrade_calls"] == []
    assert len(cfn_response) == 1
    assert cfn_response[0]["Status"] == "SUCCESS"
    assert cfn_response[0]["Data"]["head"] == "skipped-on-delete"


def test_custom_resource_failure_signals_failed(
    migrator: ModuleType,
    lambda_context: Any,
    stubbed: dict[str, Any],
    cfn_response: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(cfg: Any, revision: str) -> None:
        raise RuntimeError("relation already exists")

    monkeypatch.setattr("alembic.command.upgrade", _boom)

    # Must not raise: a failed migration signals FAILED so the stack doesn't hang.
    migrator.handler(_cfn_event("Update"), lambda_context)

    assert len(cfn_response) == 1
    assert cfn_response[0]["Status"] == "FAILED"
    assert "relation already exists" in cfn_response[0]["Reason"]


def test_bootstrap_mode_uses_master_credentials(
    migrator: ModuleType, lambda_context: Any, stubbed: dict[str, Any]
) -> None:
    event = {
        "mode": "bootstrap",
        "master_username": "postgres",
        "master_password": "s3cr3t/pw+x",
        "target": "0003",
    }
    result = migrator.handler(event, lambda_context)

    assert result["status"] == "ok"
    assert result["mode"] == "bootstrap"
    # Bootstrap stops at the role/schema boundary, not head.
    assert stubbed["upgrade_calls"] and stubbed["upgrade_calls"][0][1] == "0003"
    _assert_advisory_lock_taken(stubbed["sql"])
    # Self-heals RDS IAM enrollment (idempotent re-grant) as part of bootstrap.
    joined_sql = " ".join(sql for sql, _ in stubbed["sql"])
    assert "rds_iam" in joined_sql and "lambda_migrator" in joined_sql
    # No IAM token requested; connects as the master with the supplied password.
    assert stubbed["token_kwargs"] == {}
    url = make_url(os.environ["DATABASE_URL"])
    assert url.username == "postgres"
    assert url.password == "s3cr3t/pw+x"
