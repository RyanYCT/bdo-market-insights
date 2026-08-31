"""Tests for bdo_common.catalog_artifact (the static catalog CDN artifact)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bdo_common import catalog_artifact
from bdo_common.models import Item


def _item(item_id: int, **kw: Any) -> Item:
    return Item(id=item_id, name=kw.pop("name", f"Item {item_id}"), **kw)


class TestBuildCatalogArtifact:
    def test_projects_public_shape_sorted_by_id(self) -> None:
        items = [
            _item(
                12094,
                name="Deboreka Ring",
                names={"tw": "\u5fb7\u6ce2\u96f7\u5361\u6212\u6307"},
                grade=4,
                category="accessories",
                main_category="20",
                sub_category="2",
                icon_status="stored",
                # Internal fields must not leak into the artifact.
                model_id="accessory_v1",
                cron_profile="deboreka",
                tracked=True,
            ),
            _item(11, name="Cheap Thing", icon_status="unset"),
        ]

        out = catalog_artifact.build_catalog_artifact(items, icon_base="https://cdn.example.com")

        # Sorted by id.
        assert [e["id"] for e in out] == [11, 12094]
        deb = out[1]
        assert deb == {
            "id": 12094,
            "name": "Deboreka Ring",
            "names": {"tw": "\u5fb7\u6ce2\u96f7\u5361\u6212\u6307"},
            "grade": 4,
            "category": "accessories",
            "main_category": "20",
            "sub_category": "2",
            "icon_url": "https://cdn.example.com/icons/12094.png",
        }
        # Internal fields are absent.
        assert "model_id" not in deb
        assert "cron_profile" not in deb
        assert "tracked" not in deb

    def test_icon_url_universal_regardless_of_status(self) -> None:
        # Read-through (ADR-0033): every item gets a URL when a base is set,
        # regardless of icon_status (the icon materializes on first request).
        out = catalog_artifact.build_catalog_artifact(
            [_item(1, icon_status="unset")], icon_base="https://cdn.example.com"
        )
        assert out[0]["icon_url"] == "https://cdn.example.com/icons/1.png"

    def test_icon_url_none_when_no_base(self) -> None:
        out = catalog_artifact.build_catalog_artifact(
            [_item(1, icon_status="stored")], icon_base=""
        )
        assert out[0]["icon_url"] is None


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> None:
        self.puts.append(kwargs)


class TestPublishCatalogArtifact:
    def test_scans_builds_and_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        items = [
            _item(2, name="B", icon_status="stored"),
            _item(1, name="A", icon_status="unset"),
        ]
        monkeypatch.setattr("bdo_common.dynamo.scan_catalog_items", lambda: items)
        s3 = _FakeS3()

        count = catalog_artifact.publish_catalog_artifact(
            bucket="bdo-dev-icons", icon_base="https://cdn.example.com", s3_client=s3
        )

        assert count == 2
        assert len(s3.puts) == 1
        put = s3.puts[0]
        assert put["Bucket"] == "bdo-dev-icons"
        assert put["Key"] == "catalog/catalog.json"
        assert put["ContentType"] == "application/json; charset=utf-8"
        assert put["CacheControl"] == "public, max-age=3600"

        payload = json.loads(put["Body"].decode("utf-8"))
        # Sorted by id, public shape, universal icon_url (read-through, ADR-0033).
        assert [e["id"] for e in payload] == [1, 2]
        assert payload[0]["icon_url"] == "https://cdn.example.com/icons/1.png"
        assert payload[1]["icon_url"] == "https://cdn.example.com/icons/2.png"
