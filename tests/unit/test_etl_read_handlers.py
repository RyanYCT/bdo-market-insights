"""Tests for the read-side ETL handlers: retrieveItems, fetchData, cleanData."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

from bdo_common.arsha_client import FetchOutcome
from bdo_common.models import Item


def _arsha_item(item_id: int, sid: int = 0) -> dict[str, Any]:
    """Minimal arsha.io v2 item object."""
    return {
        "id": item_id,
        "sid": sid,
        "name": "Test Item",
        "minEnhance": 0,
        "maxEnhance": 5,
        "basePrice": 1000,
        "currentStock": 10,
        "totalTrades": 100,
        "priceMin": 1,
        "priceMax": 9999,
        "lastSoldPrice": 950,
        "lastSoldTime": 1717027200,
    }


# ---------------------------------------------------------------------------
# retrieveItems
# ---------------------------------------------------------------------------


class TestRetrieveItems:
    def test_batches_metadata_and_snapshot(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("retrieve_items")
        items = [
            Item(id=1, name="A", category="ring", cron_profile="standard"),
            Item(id=2, name="B", category="ring", cron_profile="deboreka"),
            Item(id=3, name="C", category="necklace"),
        ]
        monkeypatch.setattr(mod.dynamo, "list_tracked_items", lambda: items)
        monkeypatch.setattr(mod, "BATCH_SIZE", 2)

        result = mod.handler(
            {"region": "tw", "execution_start_time": "2026-06-01T05:34:56.789Z"},
            lambda_context,
        )

        assert result["region"] == "tw"
        assert result["snapshot_at"] == "2026-06-01T05:00:00+00:00"
        assert result["is_day_first_run"] is False
        # 3 items, batch size 2 -> 2 batches
        assert len(result["batches"]) == 2
        first = result["batches"][0]
        assert first["region"] == "tw"
        assert first["snapshot_at"] == "2026-06-01T05:00:00+00:00"
        assert [i["id"] for i in first["items"]] == [1, 2]
        assert first["items"][1]["cron_profile"] == "deboreka"
        assert result["batches"][1]["items"][0]["id"] == 3

    def test_midnight_run_flags_day_first(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("retrieve_items")
        monkeypatch.setattr(mod.dynamo, "list_tracked_items", lambda: [])
        result = mod.handler(
            {"region": "tw", "execution_start_time": "2026-06-01T00:10:00Z"},
            lambda_context,
        )
        assert result["is_day_first_run"] is True
        assert result["snapshot_at"] == "2026-06-01T00:00:00+00:00"
        assert result["batches"] == []


# ---------------------------------------------------------------------------
# fetchData
# ---------------------------------------------------------------------------


class TestFetchData:
    def test_attaches_raw_and_passes_through(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("fetch_data")
        captured: dict[str, Any] = {}

        def fake_fetch(self: Any, item_ids: list[int]) -> FetchOutcome:
            captured["ids"] = item_ids
            return FetchOutcome([[_arsha_item(11608), _arsha_item(11629)]], [])

        monkeypatch.setattr(mod.ArshaClient, "fetch_raw_resilient", fake_fetch)

        batch = {
            "region": "tw",
            "snapshot_at": "2026-06-01T05:00:00+00:00",
            "items": [{"id": 11608, "name": "X"}, {"id": 11629, "name": "Y"}],
        }
        result = mod.handler(batch, lambda_context)

        assert captured["ids"] == [11608, 11629]
        assert result["region"] == "tw"
        assert result["snapshot_at"] == "2026-06-01T05:00:00+00:00"
        assert result["items"] == batch["items"]
        assert result["raw"] == [[_arsha_item(11608), _arsha_item(11629)]]

    def test_tolerates_sparse_failures(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A few unfetchable items are dropped; the run still returns the rest."""
        mod = load_handler("fetch_data")

        def fake_fetch(self: Any, item_ids: list[int]) -> FetchOutcome:
            # 10 items, 1 blocked (10%) -> below the 20% threshold
            return FetchOutcome([[_arsha_item(i)] for i in item_ids[:-1]], [item_ids[-1]])

        monkeypatch.setattr(mod.ArshaClient, "fetch_raw_resilient", fake_fetch)
        batch = {
            "region": "tw",
            "snapshot_at": "2026-06-01T05:00:00+00:00",
            "items": [{"id": 100 + i, "name": "X"} for i in range(10)],
        }
        result = mod.handler(batch, lambda_context)
        assert len(result["raw"]) == 9

    def test_fails_loud_when_too_many_unfetchable(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When arsha is broadly down (> threshold failed), the stage raises."""
        mod = load_handler("fetch_data")

        def fake_fetch(self: Any, item_ids: list[int]) -> FetchOutcome:
            return FetchOutcome([], list(item_ids))  # 100% failed

        monkeypatch.setattr(mod.ArshaClient, "fetch_raw_resilient", fake_fetch)
        batch = {
            "region": "tw",
            "snapshot_at": "2026-06-01T05:00:00+00:00",
            "items": [{"id": 100 + i, "name": "X"} for i in range(10)],
        }
        with pytest.raises(RuntimeError, match="unfetchable"):
            mod.handler(batch, lambda_context)


# ---------------------------------------------------------------------------
# cleanData
# ---------------------------------------------------------------------------


class TestCleanData:
    def test_normalizes_and_drops_raw(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
    ) -> None:
        mod = load_handler("clean_data")
        event = {
            "region": "tw",
            "snapshot_at": "2026-06-01T05:00:00+00:00",
            "items": [{"id": 11608, "name": "X"}],
            "raw": [[_arsha_item(11608, 0), _arsha_item(11608, 1)]],
        }
        result = mod.handler(event, lambda_context)

        assert "raw" not in result
        assert result["region"] == "tw"
        assert result["snapshot_at"] == "2026-06-01T05:00:00+00:00"
        assert result["items"] == event["items"]
        assert len(result["records"]) == 2
        rec = result["records"][0]
        assert rec["item_id"] == 11608
        assert rec["price_max"] == 9999
        # model_dump(mode="json") serializes datetime to an ISO string
        assert isinstance(rec["last_sold_at"], str)

    def test_empty_raw_yields_no_records(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
    ) -> None:
        mod = load_handler("clean_data")
        event: dict[str, Any] = {
            "region": "tw",
            "snapshot_at": "2026-06-01T05:00:00+00:00",
            "items": [],
            "records": None,
        }
        result = mod.handler(event, lambda_context)
        assert result["records"] == []
