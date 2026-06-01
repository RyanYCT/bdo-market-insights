"""Tests for the storeData ETL handler (transaction orchestration)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from bdo_common.models import Record


def _record_dict(item_id: int, sid: int = 0) -> dict[str, Any]:
    """A JSON-serialized Record as cleanData would emit."""
    return Record(
        item_id=item_id,
        sid=sid,
        name="Test Item",
        base_price=1000,
        current_stock=10,
        total_trades=100,
        last_sold_price=950,
        last_sold_at=datetime(2026, 6, 1, 5, 0, tzinfo=UTC),
        max_enhance=5,
        price_min=1,
        price_max=9999,
    ).model_dump(mode="json")


def _event() -> dict[str, Any]:
    return {
        "region": "tw",
        "snapshot_at": "2026-06-01T05:00:00+00:00",
        "items": [
            {
                "id": 11608,
                "name": "Deboreka Necklace",
                "category": "necklace",
                "main_category": None,
                "sub_category": None,
            }
        ],
        "records": [_record_dict(11608, 0), _record_dict(11608, 1)],
    }


class TestStoreData:
    def test_commits_one_transaction(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("store_data")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)

        item_calls: list[dict[str, Any]] = []
        sid_calls: list[dict[str, Any]] = []
        bulk_rows: list[Any] = []

        monkeypatch.setattr(
            mod.ItemRepo, "upsert", staticmethod(lambda c, **kw: item_calls.append(kw))
        )
        monkeypatch.setattr(
            mod.ItemSidRepo, "upsert", staticmethod(lambda c, **kw: sid_calls.append(kw))
        )

        def fake_bulk(c: Any, rows: list[Any]) -> int:
            bulk_rows.extend(rows)
            return len(rows)

        monkeypatch.setattr(mod.SnapshotRepo, "bulk_insert", staticmethod(fake_bulk))

        result = mod.handler(_event(), lambda_context)

        assert len(item_calls) == 1
        assert item_calls[0]["item_id"] == 11608
        assert item_calls[0]["category"] == "necklace"
        assert len(sid_calls) == 2
        assert sid_calls[0]["price_max"] == 9999
        assert len(bulk_rows) == 2
        assert bulk_rows[0].region == "tw"
        assert bulk_rows[0].snapshot_at == datetime(2026, 6, 1, 5, 0, tzinfo=UTC)
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
        assert result == {
            "region": "tw",
            "item_count": 1,
            "sid_count": 2,
            "snapshot_count": 2,
        }

    def test_skips_records_for_unknown_items(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("store_data")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)
        monkeypatch.setattr(mod.ItemRepo, "upsert", staticmethod(lambda c, **kw: None))
        sid_ids: list[int] = []
        monkeypatch.setattr(
            mod.ItemSidRepo,
            "upsert",
            staticmethod(lambda c, **kw: sid_ids.append(kw["item_id"])),
        )
        monkeypatch.setattr(
            mod.SnapshotRepo, "bulk_insert", staticmethod(lambda c, rows: len(rows))
        )

        event = _event()
        event["records"].append(_record_dict(99999, 0))  # not in items metadata
        result = mod.handler(event, lambda_context)

        assert sid_ids == [11608, 11608]  # the stray 99999 record was skipped
        assert result["sid_count"] == 2

    def test_rolls_back_and_reraises_on_error(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("store_data")
        conn = MagicMock()
        monkeypatch.setattr(mod.db, "get_connection", lambda: conn)
        monkeypatch.setattr(mod.ItemRepo, "upsert", staticmethod(lambda c, **kw: None))
        monkeypatch.setattr(mod.ItemSidRepo, "upsert", staticmethod(lambda c, **kw: None))

        def boom(c: Any, rows: list[Any]) -> int:
            raise RuntimeError("insert failed")

        monkeypatch.setattr(mod.SnapshotRepo, "bulk_insert", staticmethod(boom))

        with pytest.raises(RuntimeError, match="insert failed"):
            mod.handler(_event(), lambda_context)

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()
