"""Unit tests for the admin-query Lambda handler.

The DB connection is stubbed; the tests assert the handler runs read-only by
default (rolls back, never commits), commits only with ``write=true``, caps and
flags truncated results, and reduces non-JSON values to primitives.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture
def admin_query(load_handler: Callable[[str], ModuleType]) -> ModuleType:
    return load_handler("admin_query")


class _FakeCursor:
    def __init__(self, description: Any, rows: list[tuple[Any, ...]], rowcount: int) -> None:
        self.description = description
        self._rows = rows
        self.rowcount = rowcount
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchmany(self, size: int) -> list[tuple[Any, ...]]:
        return self._rows[:size]

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.rollbacks = 0
        self.commits = 0
        self.read_only_sets: list[bool] = []
        self._read_only = False

    @property
    def read_only(self) -> bool:
        return self._read_only

    @read_only.setter
    def read_only(self, value: bool) -> None:
        self._read_only = value
        self.read_only_sets.append(value)

    def rollback(self) -> None:
        self.rollbacks += 1

    def commit(self) -> None:
        self.commits += 1

    def cursor(self) -> _FakeCursor:
        return self._cursor


def _cols(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(name=n) for n in names]


def _install_conn(
    admin_query: ModuleType, monkeypatch: pytest.MonkeyPatch, cursor: _FakeCursor
) -> _FakeConn:
    conn = _FakeConn(cursor)
    from bdo_common import db

    monkeypatch.setattr(db, "get_connection", lambda: conn)
    return conn


def test_read_only_select_returns_rows_and_never_commits(
    admin_query: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cur = _FakeCursor(_cols("id", "name"), [(1, "a"), (2, "b")], rowcount=2)
    conn = _install_conn(admin_query, monkeypatch, cur)

    result = admin_query.handler({"sql": "select id, name from item"}, lambda_context)

    assert result["columns"] == ["id", "name"]
    assert result["rows"] == [[1, "a"], [2, "b"]]
    assert result["rowcount"] == 2
    assert result["truncated"] is False
    assert result["write"] is False
    # Read-only transaction was requested and nothing was committed.
    assert True in [ro is True for ro in conn.read_only_sets]
    assert conn.commits == 0
    assert conn.rollbacks >= 1


def test_write_mode_commits_and_runs_read_write(
    admin_query: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cur = _FakeCursor(None, [], rowcount=1)  # DML: no result set
    conn = _install_conn(admin_query, monkeypatch, cur)

    result = admin_query.handler(
        {"sql": "delete from market_snapshot where id = 42", "write": True}, lambda_context
    )

    assert result["write"] is True
    assert result["columns"] == []
    assert result["rows"] == []
    assert result["rowcount"] == 1
    # read_only set False before the statement (write mode), committed once.
    assert conn.read_only_sets[0] is False
    assert conn.commits == 1


def test_result_is_capped_and_flagged_truncated(
    admin_query: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cur = _FakeCursor(_cols("id"), [(1,), (2,), (3,)], rowcount=3)
    _install_conn(admin_query, monkeypatch, cur)

    result = admin_query.handler({"sql": "select id from item", "max_rows": 2}, lambda_context)

    assert result["rows"] == [[1], [2]]
    assert result["truncated"] is True


def test_missing_sql_raises(
    admin_query: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_conn(admin_query, monkeypatch, _FakeCursor(None, [], 0))

    with pytest.raises(ValueError, match="sql"):
        admin_query.handler({}, lambda_context)
    with pytest.raises(ValueError, match="sql"):
        admin_query.handler({"sql": "   "}, lambda_context)


def test_non_json_values_are_serialised(admin_query: ModuleType) -> None:
    jsonable = admin_query._jsonable
    assert jsonable(decimal.Decimal("12.50")) == "12.50"
    assert jsonable(datetime.date(2026, 7, 5)) == "2026-07-05"
    assert jsonable(datetime.datetime(2026, 7, 5, 1, 2, 3)) == "2026-07-05T01:02:03"
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert jsonable(uid) == "12345678-1234-5678-1234-567812345678"
    assert jsonable(b"\x00\xff") == "00ff"
    assert jsonable(None) is None
    assert jsonable(7) == 7
