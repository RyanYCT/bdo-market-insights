"""Tests for the iconSync Lambda handler."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

from bdo_common.icons import IconSyncStats
from bdo_common.models import Item


class TestIconSyncHandler:
    def test_prefetches_all_tracked_and_returns(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ICONS_BUCKET", "bdo-dev-icons")
        mod = load_handler("icon_sync")

        items = [Item(id=1, name="A"), Item(id=2, name="B"), Item(id=3, name="C")]
        monkeypatch.setattr(mod.dynamo, "list_tracked_items", lambda: items)

        captured: dict[str, Any] = {}

        def fake_sync(tracked: Any, *, bucket: str, region: str, **_: Any) -> IconSyncStats:
            captured["ids"] = [item.id for item in tracked]
            captured["bucket"] = bucket
            captured["region"] = region
            return IconSyncStats(stored=3, missing=0, errors=0)

        monkeypatch.setattr(mod.icons, "sync_icons", fake_sync)

        result = mod.handler({}, lambda_context)

        assert captured["ids"] == [1, 2, 3]  # the whole tracked set is prefetched
        assert captured["bucket"] == "bdo-dev-icons"
        assert result == {"tracked": 3, "stored": 3, "missing": 0, "errors": 0}
