"""Tests for bdo_common.icons (Pearl CDN fetch + S3 materialization)."""

from __future__ import annotations

import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import moto
import pytest
from botocore.exceptions import ClientError

from bdo_common import dynamo, icons
from bdo_common.models import Item


class TestBuildIconUrl:
    def test_upper_cases_region(self) -> None:
        assert (
            icons.build_icon_url(12094, region="tw")
            == "https://s1.pearlcdn.com/TW/TradeMarket/Common/img/BDO/item/12094.png"
        )

    def test_custom_base_trailing_slash_stripped(self) -> None:
        assert (
            icons.build_icon_url(1, region="na", base="https://cdn.example.com/")
            == "https://cdn.example.com/NA/TradeMarket/Common/img/BDO/item/1.png"
        )


class TestFetchIcon:
    @staticmethod
    def _resp(data: bytes) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = data
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_success_returns_bytes(self) -> None:
        with patch("bdo_common.icons.urllib.request.urlopen", return_value=self._resp(b"PNG")):
            assert icons.fetch_icon(12094, region="tw") == b"PNG"

    @pytest.mark.parametrize("code", [403, 404])
    def test_missing_codes_return_none(self, code: int) -> None:
        err = urllib.error.HTTPError("u", code, "missing", {}, None)  # type: ignore[arg-type]
        with patch("bdo_common.icons.urllib.request.urlopen", side_effect=err):
            assert icons.fetch_icon(1, region="tw") is None

    def test_server_error_raises(self) -> None:
        err = urllib.error.HTTPError("u", 503, "busy", {}, None)  # type: ignore[arg-type]
        with (
            patch("bdo_common.icons.urllib.request.urlopen", side_effect=err),
            pytest.raises(urllib.error.HTTPError),
        ):
            icons.fetch_icon(1, region="tw")

    def test_network_error_raises(self) -> None:
        with (
            patch(
                "bdo_common.icons.urllib.request.urlopen",
                side_effect=urllib.error.URLError("boom"),
            ),
            pytest.raises(urllib.error.URLError),
        ):
            icons.fetch_icon(1, region="tw")


class TestSyncIcons:
    def test_stored_missing_and_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="test-icons")

            def fake_fetch(
                item_id: int, *, region: str, base: str = icons.ICON_SOURCE_BASE
            ) -> Any:
                if item_id == 1:
                    return b"PNG1"
                if item_id == 2:
                    return None  # CDN has no icon -> missing
                raise RuntimeError("transient")  # -> error, left unset

            monkeypatch.setattr(icons, "fetch_icon", fake_fetch)

            statuses: dict[int, str] = {}
            monkeypatch.setattr(
                dynamo,
                "update_item",
                lambda item_id, updates: statuses.__setitem__(item_id, updates["icon_status"]),
            )

            items = [Item(id=1, name="A"), Item(id=2, name="B"), Item(id=3, name="C")]
            stats = icons.sync_icons(items, bucket="test-icons", region="tw", s3_client=s3)

            assert (stats.stored, stats.missing, stats.errors) == (1, 1, 1)
            # id 3 (transient error) is left unset -> no status write
            assert statuses == {1: "stored", 2: "missing"}

            assert s3.get_object(Bucket="test-icons", Key="icons/1.png")["Body"].read() == b"PNG1"
            with pytest.raises(ClientError):
                s3.get_object(Bucket="test-icons", Key="icons/2.png")
