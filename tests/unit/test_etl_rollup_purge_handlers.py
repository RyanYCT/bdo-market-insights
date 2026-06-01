"""Tests for the rollupDaily and purgeOldSnapshots ETL handlers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest


class TestRollupDaily:
    def test_rolls_up_previous_utc_day(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("rollup_daily")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)
        captured: dict[str, Any] = {}

        def fake_rollup(c: Any, *, region: str, trade_date: date) -> int:
            captured["region"] = region
            captured["trade_date"] = trade_date
            return 5

        monkeypatch.setattr(mod.DailyRepo, "rollup_day", staticmethod(fake_rollup))

        result = mod.handler(
            {"region": "tw", "snapshot_at": "2026-06-01T00:00:00+00:00"},
            lambda_context,
        )

        # snapshot_at is midnight of the new day -> roll up the prior day
        assert captured["trade_date"] == date(2026, 5, 31)
        assert result == {"region": "tw", "trade_date": "2026-05-31", "daily_rows": 5}
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_rollback_on_error(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("rollup_daily")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)

        def boom(c: Any, *, region: str, trade_date: date) -> int:
            raise RuntimeError("aggregate failed")

        monkeypatch.setattr(mod.DailyRepo, "rollup_day", staticmethod(boom))

        with pytest.raises(RuntimeError, match="aggregate failed"):
            mod.handler(
                {"region": "tw", "snapshot_at": "2026-06-01T00:00:00+00:00"}, lambda_context
            )

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()


class TestPurgeOldSnapshots:
    def test_purges_with_90_day_cutoff(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("purge_old_snapshots")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)
        captured: dict[str, Any] = {}

        def fake_purge(c: Any, cutoff: datetime) -> int:
            captured["cutoff"] = cutoff
            return 12

        monkeypatch.setattr(mod.SnapshotRepo, "purge_older_than", staticmethod(fake_purge))

        before = datetime.now(tz=UTC)
        result = mod.handler({}, lambda_context)
        after = datetime.now(tz=UTC)

        assert result["deleted"] == 12
        cutoff = captured["cutoff"]
        # cutoff is ~90 days before "now"
        assert before - timedelta(days=90) - timedelta(seconds=5) <= cutoff
        assert cutoff <= after - timedelta(days=90) + timedelta(seconds=5)
        conn.commit.assert_called_once()

    def test_rollback_on_error(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("purge_old_snapshots")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)

        def boom(c: Any, cutoff: datetime) -> int:
            raise RuntimeError("delete failed")

        monkeypatch.setattr(mod.SnapshotRepo, "purge_older_than", staticmethod(boom))

        with pytest.raises(RuntimeError, match="delete failed"):
            mod.handler({}, lambda_context)

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
