"""deliveryBucketJanitor: CloudFormation custom resource that empties the
``bdo-<stage>-icons`` delivery bucket before a non-prod stack delete.

``infra/cdn.yaml`` sets ``DeliveryBucket``'s ``DeletionPolicy``/
``UpdateReplacePolicy`` to ``Delete`` on dev (``Retain`` on prod, ADR-0019). S3
refuses to delete a non-empty bucket, so this custom resource -- wired only on
non-prod (``Condition: IsNotProd``) -- empties the bucket on the CloudFormation
``Delete`` event, right before CloudFormation attempts the bucket's own delete.
Prod never creates this resource, so prod's materialized assets are never
touched by a stack delete.

This is the classic Lambda-backed custom resource protocol: CloudFormation
invokes this function directly and expects the response as an HTTP ``PUT`` to
the pre-signed S3 URL in ``event["ResponseURL"]`` (not a normal return value).

Deliberately never reports ``FAILED``: a real failure to empty the bucket is
logged, but the response is always ``SUCCESS`` so the janitor never blocks a
stack delete. If objects genuinely remain, CloudFormation's own bucket delete
fails loudly with ``BucketNotEmpty`` -- a clearer signal than a stuck custom
resource retry loop.

Intentionally depends only on the Python standard library and boto3 (both in
the Lambda runtime), not on the shared bdo-common layer: as a custom resource
it must always send a response, and fewer imports means fewer init-time failure
modes. This also keeps the cdn stack (ADR-0032) free of a layer dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("bucket_janitor")
logger.setLevel(logging.INFO)


def _send_response(
    event: dict[str, Any],
    context: Any,
    status: str,
    *,
    reason: str = "",
    data: dict[str, Any] | None = None,
) -> None:
    """PUT the custom-resource result to the pre-signed CloudFormation URL.

    Never raises -- a failure here would otherwise leave the stack operation
    waiting on a response that never arrives.
    """
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch Logs: {getattr(context, 'log_stream_name', '')}",
            "PhysicalResourceId": event.get("PhysicalResourceId")
            or event.get("LogicalResourceId", "delivery-bucket-janitor"),
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "NoEcho": False,
            "Data": data or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"Content-Type": "", "Content-Length": str(len(body))},
    )
    try:
        # ResponseURL is an AWS-issued pre-signed S3 URL delivered in the CFN
        # event, not attacker-controlled.
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310  # nosec B310
            resp.read()
    except Exception:
        logger.exception("failed to send CloudFormation custom resource response")


def _empty_bucket(bucket: str, s3_client: Any) -> int:
    """Delete every object in ``bucket``; return the count deleted.

    A no-op (returns 0) if the bucket does not exist -- idempotent for a
    stack that never finished creating it, or a repeat Delete signal.
    """
    deleted = 0
    paginator = s3_client.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=bucket):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if not keys:
                continue
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchBucket":
            logger.info("bucket does not exist; nothing to empty: %s", bucket)
            return deleted
        raise
    return deleted


def handler(event: dict[str, Any], context: Any) -> None:
    """Empty the bucket on ``Delete``; no-op on ``Create``/``Update``."""
    request_type = event.get("RequestType", "")
    bucket = event.get("ResourceProperties", {}).get("BucketName", "")
    logger.info("deliveryBucketJanitor invoked: request_type=%s bucket=%s", request_type, bucket)

    if request_type == "Delete" and bucket:
        try:
            deleted = _empty_bucket(bucket, boto3.client("s3"))
            logger.info("emptied delivery bucket %s: deleted=%d", bucket, deleted)
        except Exception:
            # See module docstring: never block the stack delete on the
            # janitor itself.
            logger.exception("failed to empty bucket; continuing: %s", bucket)

    _send_response(event, context, "SUCCESS")
