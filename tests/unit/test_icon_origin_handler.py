"""Unit tests for the icon_origin read-through CloudFront origin Lambda."""

from __future__ import annotations

import base64
import urllib.error
from collections.abc import Callable
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import boto3
import moto
import pytest


def _event(path: str) -> dict[str, Any]:
    return {"rawPath": path, "requestContext": {"http": {"path": path}}}


class _Resp:
    """Minimal context-manager stand-in for urllib.request.urlopen()."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def mod(load_handler: Callable[[str], ModuleType], monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("ICONS_BUCKET", "bdo-dev-cdn-test")
    monkeypatch.setenv("BDO_REGION", "tw")
    return load_handler("icon_origin")


class TestIconOrigin:
    def test_materializes_and_returns_bytes(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="bdo-dev-cdn-test")
            monkeypatch.setattr(mod.boto3, "client", lambda service: s3)
            monkeypatch.setattr(
                mod.urllib.request, "urlopen", lambda url, timeout=15: _Resp(b"PNGDATA")
            )

            resp = mod.handler(_event("/icons/12094.png"), lambda_context)

            assert resp["statusCode"] == 200
            assert resp["headers"]["content-type"] == "image/png"
            assert resp["headers"]["cache-control"] == "public, max-age=604800"
            assert resp["isBase64Encoded"] is True
            assert base64.b64decode(resp["body"]) == b"PNGDATA"
            # Stored to S3 so subsequent requests hit the primary origin directly.
            stored = s3.get_object(Bucket="bdo-dev-cdn-test", Key="icons/12094.png")
            assert stored["Body"].read() == b"PNGDATA"
            assert stored["ContentType"] == "image/png"

    def test_uses_region_in_upstream_url(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, str] = {}

        def fake_urlopen(url: str, timeout: int = 15) -> _Resp:
            seen["url"] = url
            return _Resp(b"x")

        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="bdo-dev-cdn-test")
            monkeypatch.setattr(mod.boto3, "client", lambda service: s3)
            monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
            mod.handler(_event("/icons/42.png"), lambda_context)

        # tw -> TW in the Pearl path; id echoed.
        assert seen["url"].endswith("/TW/TradeMarket/Common/img/BDO/item/42.png")

    def test_upstream_missing_returns_404(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_404(url: str, timeout: int = 15) -> _Resp:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(mod.urllib.request, "urlopen", raise_404)
        resp = mod.handler(_event("/icons/999999.png"), lambda_context)
        assert resp["statusCode"] == 404

    def test_upstream_server_error_returns_502(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_500(url: str, timeout: int = 15) -> _Resp:
            raise urllib.error.HTTPError(url, 500, "err", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(mod.urllib.request, "urlopen", raise_500)
        resp = mod.handler(_event("/icons/1.png"), lambda_context)
        assert resp["statusCode"] == 502

    def test_non_icon_path_returns_404(self, mod: ModuleType, lambda_context: Any) -> None:
        assert mod.handler(_event("/not-an-icon"), lambda_context)["statusCode"] == 404

    def test_store_failure_still_returns_bytes(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed S3 store is best-effort: the viewer still gets the icon."""
        failing = MagicMock()
        failing.put_object.side_effect = RuntimeError("s3 down")
        monkeypatch.setattr(mod.boto3, "client", lambda service: failing)
        monkeypatch.setattr(mod.urllib.request, "urlopen", lambda url, timeout=15: _Resp(b"X"))

        resp = mod.handler(_event("/icons/5.png"), lambda_context)
        assert resp["statusCode"] == 200
        assert base64.b64decode(resp["body"]) == b"X"
