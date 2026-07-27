"""Unit tests for the iconsBucketJanitor CloudFormation custom resource."""

from __future__ import annotations

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
