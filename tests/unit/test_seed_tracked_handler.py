"""Unit tests for the seedTracked Lambda handler.

The bundled data files and the DynamoDB write are stubbed; the test asserts the
handler derives correct partial updates (tracked + category + cron_profile) from
the curated inputs and reports classified vs unclassified counts.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest


@pytest.fixture
def seed_tracked(load_handler: Callable[[str], ModuleType]) -> ModuleType:
    return load_handler("seed_tracked")


def test_handler_seeds_tracked_set_and_flags_unclassified(
    seed_tracked: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    data: dict[str, Any] = {
        "tracked_items.json": [{"id": 1, "name": "Ring"}, {"id": 2, "name": "Mystery"}],
        # id 1 -> (20,1) has a category label; id 2 -> (55,1) has none (unclassified).
        "full_items.json": [
            {"id": 1, "name": "Ring", "main": 20, "sub": 1},
            {"id": 2, "name": "Mystery", "main": 55, "sub": 1},
        ],
        "categories.json": {"_comment": "skip me", "20:1": {"category": "accessory"}},
        "track_sets.json": {},
    }
    monkeypatch.setattr(seed_tracked, "_load", lambda name: data[name])

    captured: dict[str, Any] = {}
    from bdo_common import dynamo

    def _fake_bulk(plan: list[tuple[int, dict[str, Any]]], **_kw: Any) -> int:
        captured["plan"] = dict(plan)
        return len(plan)

    monkeypatch.setattr(dynamo, "bulk_update_items", _fake_bulk)

    result = seed_tracked.handler({}, lambda_context)

    assert result["total"] == 2
    assert result["seeded"] == 2
    assert result["unclassified"] == [2]

    plan = captured["plan"]
    # id 1: fully classified accessory -> standard profile.
    assert plan[1]["tracked"] == "true"
    assert plan[1]["main_category"] == "20"
    assert plan[1]["category"] == "accessory"
    assert plan[1]["cron_profile"] == "standard"
    # id 2: tracked but ungrouped -> no category, default 'none' profile.
    assert plan[2]["tracked"] == "true"
    assert "category" not in plan[2]
    assert plan[2]["cron_profile"] == "none"
