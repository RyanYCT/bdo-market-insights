"""Tests for the catalogSync Lambda handler."""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

from bdo_common.catalog import CatalogSyncStats


class TestCatalogSyncHandler:
    def test_returns_stats(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("catalog_sync")

        def fake_sync(
            client: Any, langs: list[str], *, default_lang: str = "en", max_workers: int = 16
        ) -> CatalogSyncStats:
            return CatalogSyncStats(total=68000, new=12, langs=langs)

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)

        result = mod.handler({}, lambda_context)
        assert result == {"total": 68000, "new": 12, "langs": ["en", "tw"]}

    def test_respects_catalog_langs_env(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CATALOG_LANGS", "en, tw, kr")
        mod = load_handler("catalog_sync")
        captured: dict[str, Any] = {}

        def fake_sync(
            client: Any, langs: list[str], *, default_lang: str = "en", max_workers: int = 16
        ) -> CatalogSyncStats:
            captured["langs"] = langs
            return CatalogSyncStats(total=1, new=0, langs=langs)

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)

        mod.handler({}, lambda_context)
        assert captured["langs"] == ["en", "tw", "kr"]
