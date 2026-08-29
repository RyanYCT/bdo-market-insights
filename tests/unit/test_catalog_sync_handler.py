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
            client: Any,
            langs: list[str],
            *,
            default_lang: str = "en",
            max_workers: int = 16,
            checksum_param: str | None = None,
        ) -> CatalogSyncStats:
            return CatalogSyncStats(
                total=68000,
                new=12,
                langs=langs,
                fetched={"en": 68000, "tw": 68000},
                written=12,
            )

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)

        result = mod.handler({}, lambda_context)
        # No CATALOG_ARTIFACT_BUCKET set -> the artifact publish is skipped.
        assert result == {
            "total": 68000,
            "written": 12,
            "new": 12,
            "langs": ["en", "tw"],
            "failed_langs": [],
            "skipped": False,
            "unchanged": False,
            "artifact_items": 0,
        }

    def test_reports_failed_langs(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mod = load_handler("catalog_sync")

        def fake_sync(
            client: Any,
            langs: list[str],
            *,
            default_lang: str = "en",
            max_workers: int = 16,
            checksum_param: str | None = None,
        ) -> CatalogSyncStats:
            return CatalogSyncStats(
                total=68000, new=0, langs=langs, fetched={"en": 68000, "tw": 0}
            )

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)

        result = mod.handler({}, lambda_context)
        assert result["failed_langs"] == ["tw"]
        assert result["skipped"] is False

    def test_publishes_artifact_when_bucket_configured(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CATALOG_ARTIFACT_BUCKET", "bdo-dev-icons")
        monkeypatch.setenv("ICON_BASE_URL", "https://cdn.example.com")
        mod = load_handler("catalog_sync")

        def fake_sync(client: Any, langs: list[str], **kwargs: Any) -> CatalogSyncStats:
            return CatalogSyncStats(total=68000, new=0, langs=langs, fetched={"en": 68000})

        captured: dict[str, Any] = {}

        def fake_publish(*, bucket: str, icon_base: str, **kwargs: Any) -> int:
            captured["bucket"] = bucket
            captured["icon_base"] = icon_base
            return 68000

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)
        monkeypatch.setattr(mod.catalog_artifact, "publish_catalog_artifact", fake_publish)

        result = mod.handler({}, lambda_context)
        assert result["artifact_items"] == 68000
        assert captured == {"bucket": "bdo-dev-icons", "icon_base": "https://cdn.example.com"}

    def test_skips_artifact_when_run_skipped(
        self,
        load_handler: Callable[[str], ModuleType],
        lambda_context: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A skipped sync (default-language fetch failed) must not republish."""
        monkeypatch.setenv("CATALOG_ARTIFACT_BUCKET", "bdo-dev-icons")
        mod = load_handler("catalog_sync")

        def fake_sync(client: Any, langs: list[str], **kwargs: Any) -> CatalogSyncStats:
            return CatalogSyncStats(total=0, new=0, langs=langs, fetched={"en": 0}, skipped=True)

        def fail_publish(**kwargs: Any) -> int:
            raise AssertionError("artifact must not be published on a skipped run")

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)
        monkeypatch.setattr(mod.catalog_artifact, "publish_catalog_artifact", fail_publish)

        result = mod.handler({}, lambda_context)
        assert result["skipped"] is True
        assert result["artifact_items"] == 0

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
            client: Any,
            langs: list[str],
            *,
            default_lang: str = "en",
            max_workers: int = 16,
            checksum_param: str | None = None,
        ) -> CatalogSyncStats:
            captured["langs"] = langs
            return CatalogSyncStats(total=1, new=0, langs=langs)

        monkeypatch.setattr(mod.catalog, "sync_catalog", fake_sync)

        mod.handler({}, lambda_context)
        assert captured["langs"] == ["en", "tw", "kr"]
