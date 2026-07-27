"""Unit tests for the iconsBucketJanitor CloudFormation custom resource."""

from __future__ import annotations

import json
from collections.abc import Callable
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import boto3
import moto
import pytest


def _cfn_event(request_type: str, bucket: str = "bdo-dev-icons") -> dict[str, Any]:
    return {
        "RequestType": request_type,
        "ResponseURL": "https://cloudformation-custom-resource-response.s3.amazonaws.com/test",
        "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/test/abc",
        "RequestId": "req-1",
        "LogicalResourceId": "IconsBucketJanitor",
        "ResourceProperties": {"BucketName": bucket},
    }


@pytest.fixture
def mod(load_handler: Callable[[str], ModuleType], monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = load_handler("bucket_janitor")
    # Every test asserts on the janitor's own behaviour, not the response PUT.
    monkeypatch.setattr(module, "_send_response", MagicMock())
    return module


class TestEmptyBucket:
    def test_deletes_all_objects(self, mod: ModuleType) -> None:
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="bdo-dev-icons")
            for key in ("icons/1.png", "icons/2.png", "icons/3.png"):
                s3.put_object(Bucket="bdo-dev-icons", Key=key, Body=b"x")

            deleted = mod._empty_bucket("bdo-dev-icons", s3)

            assert deleted == 3
            listing = s3.list_objects_v2(Bucket="bdo-dev-icons")
            assert listing.get("KeyCount", 0) == 0

    def test_empty_bucket_is_noop(self, mod: ModuleType) -> None:
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="bdo-dev-icons")

            assert mod._empty_bucket("bdo-dev-icons", s3) == 0

    def test_missing_bucket_is_noop(self, mod: ModuleType) -> None:
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            # Bucket never created -- must not raise (idempotent Delete signal).
            assert mod._empty_bucket("bdo-dev-icons-does-not-exist", s3) == 0


class TestHandler:
    def test_delete_empties_bucket_and_reports_success(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="bdo-dev-icons")
            s3.put_object(Bucket="bdo-dev-icons", Key="icons/1.png", Body=b"x")
            monkeypatch.setattr(mod.boto3, "client", lambda service: s3)

            mod.handler(_cfn_event("Delete"), lambda_context)

            listing = s3.list_objects_v2(Bucket="bdo-dev-icons")
            assert listing.get("KeyCount", 0) == 0
            mod._send_response.assert_called_once()
            assert mod._send_response.call_args[0][2] == "SUCCESS"

    def test_create_is_a_noop(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = MagicMock()
        monkeypatch.setattr(mod, "_empty_bucket", called)

        mod.handler(_cfn_event("Create"), lambda_context)

        called.assert_not_called()
        mod._send_response.assert_called_once()
        assert mod._send_response.call_args[0][2] == "SUCCESS"

    def test_update_is_a_noop(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = MagicMock()
        monkeypatch.setattr(mod, "_empty_bucket", called)

        mod.handler(_cfn_event("Update"), lambda_context)

        called.assert_not_called()
        mod._send_response.assert_called_once()
        assert mod._send_response.call_args[0][2] == "SUCCESS"

    def test_empty_bucket_failure_still_reports_success(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The janitor never blocks a stack delete on its own failure."""
        monkeypatch.setattr(
            mod, "_empty_bucket", MagicMock(side_effect=RuntimeError("S3 unavailable"))
        )

        mod.handler(_cfn_event("Delete"), lambda_context)

        mod._send_response.assert_called_once()
        assert mod._send_response.call_args[0][2] == "SUCCESS"

    def test_delete_without_bucket_name_is_a_noop(
        self, mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = MagicMock()
        monkeypatch.setattr(mod, "_empty_bucket", called)

        mod.handler(_cfn_event("Delete", bucket=""), lambda_context)

        called.assert_not_called()
        mod._send_response.assert_called_once()
        assert mod._send_response.call_args[0][2] == "SUCCESS"


class TestSendResponse:
    """Exercise the CloudFormation response PUT itself (not mocked here).

    This is the path that hangs a whole stack operation if it is malformed,
    so it gets direct coverage: correct verb, the empty Content-Type the S3
    pre-signed URL requires, and the required response-body keys.
    """

    @pytest.fixture
    def raw_mod(self, load_handler: Callable[[str], ModuleType]) -> ModuleType:
        # NOTE: unlike the `mod` fixture, this does NOT patch _send_response.
        return load_handler("bucket_janitor")

    def test_puts_wellformed_success_response(
        self, raw_mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_urlopen = MagicMock()
        monkeypatch.setattr(raw_mod.urllib.request, "urlopen", mock_urlopen)

        event = _cfn_event("Delete")
        event["PhysicalResourceId"] = "IconsBucketJanitor"
        raw_mod._send_response(event, lambda_context, "SUCCESS")

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        # Verb + target must match the pre-signed URL contract.
        assert req.get_method() == "PUT"
        assert req.full_url == event["ResponseURL"]
        # Empty Content-Type: prevents urllib defaulting to
        # application/x-www-form-urlencoded, which would break the S3 signature.
        assert req.headers.get("Content-type") == ""
        body = json.loads(req.data.decode("utf-8"))
        assert body["Status"] == "SUCCESS"
        assert body["PhysicalResourceId"] == "IconsBucketJanitor"
        assert body["StackId"] == event["StackId"]
        assert body["RequestId"] == event["RequestId"]
        assert body["LogicalResourceId"] == event["LogicalResourceId"]

    def test_physical_id_falls_back_to_logical_id(
        self, raw_mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_urlopen = MagicMock()
        monkeypatch.setattr(raw_mod.urllib.request, "urlopen", mock_urlopen)

        # A Create event carries no PhysicalResourceId.
        event = _cfn_event("Create")
        raw_mod._send_response(event, lambda_context, "SUCCESS")

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["PhysicalResourceId"] == event["LogicalResourceId"]

    def test_never_raises_when_put_fails(
        self, raw_mod: ModuleType, lambda_context: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A failure to signal CloudFormation must be swallowed (logged), not
        # raised -- raising here would leave the stack op waiting on a response.
        monkeypatch.setattr(
            raw_mod.urllib.request,
            "urlopen",
            MagicMock(side_effect=OSError("network down")),
        )
        raw_mod._send_response(_cfn_event("Delete"), lambda_context, "SUCCESS")
