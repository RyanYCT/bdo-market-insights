"""Unit tests for the in-VPC migrator Lambda handler.

Alembic, the RDS token call, and the revision lookup are all stubbed; the test
asserts the handler runs ``upgrade head`` and builds a correct, properly escaped
psycopg-v3 connection URL for the ``lambda_migrator`` role.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.engine import make_url


@pytest.fixture
def migrator(load_handler: Callable[[str], ModuleType]) -> ModuleType:
    return load_handler("migrator")


def test_handler_runs_upgrade_head_and_returns_revision(
    migrator: ModuleType,
    lambda_context: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_HOST", "db.internal")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "bdo")
    monkeypatch.setenv("DB_USER", "lambda_migrator")
    monkeypatch.setenv("USE_IAM_AUTH", "true")
    monkeypatch.setenv("AWS_REGION", "ap-northeast-1")

    from bdo_common.config import get_settings

    get_settings.cache_clear()

    # A token with URL-hostile characters proves it is escaped, not interpolated.
    raw_token = "tok/with+special=chars&x"
    captured: dict[str, Any] = {}

    class _FakeRdsClient:
        def generate_db_auth_token(self, **kwargs: Any) -> str:
            captured.update(kwargs)
            return raw_token

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: _FakeRdsClient())

    upgrade_calls: list[tuple[Any, str]] = []
    monkeypatch.setattr(
        "alembic.command.upgrade",
        lambda cfg, revision: upgrade_calls.append((cfg, revision)),
    )

    class _FakeScriptDir:
        def get_current_head(self) -> str:
            return "0003"

    monkeypatch.setattr(
        "alembic.script.ScriptDirectory.from_config",
        classmethod(lambda cls, cfg: _FakeScriptDir()),
    )

    result = migrator.handler({}, lambda_context)

    assert result == {"status": "ok", "head": "0003"}
    assert upgrade_calls and upgrade_calls[0][1] == "head"

    # IAM token requested for the privileged migrator role on the right host.
    assert captured["DBUsername"] == "lambda_migrator"
    assert captured["DBHostname"] == "db.internal"
    assert captured["Port"] == 5432

    # The connection URL uses psycopg v3, requires SSL, and round-trips the
    # token (i.e. it was percent-encoded, not naively interpolated).
    import os

    url = make_url(os.environ["DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    assert url.username == "lambda_migrator"
    assert url.password == raw_token
    assert url.host == "db.internal"
    assert url.database == "bdo"
    assert url.query.get("sslmode") == "require"
