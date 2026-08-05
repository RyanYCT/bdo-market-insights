"""bootstrapTrigger: CloudFormation custom resource that auto-runs the bootstrap
state machine once on a fresh environment (ADR-0028).

On stack **Create**, if the items table is empty (`dynamo.catalog_is_empty`), it
`StartExecution`s the bootstrap state machine (catalog sync -> tracked seed ->
icon sync) and signals CloudFormation success **immediately** -- fire-and-forget,
so the deploy never waits on data seeding. `Update` and `Delete` are no-ops.

It always signals CloudFormation (SUCCESS/FAILED) so the stack can't hang; a
genuine failure to start (e.g. IAM) surfaces as FAILED on the introducing deploy.
On-demand re-seeding is `make bootstrap` (starts the same state machine).
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()


def _send_cfn_response(
    event: dict[str, Any],
    context: LambdaContext,
    status: str,
    physical_id: str,
    *,
    data: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """PUT a custom-resource response to the CloudFormation-presigned S3 URL."""
    log_stream = getattr(context, "log_stream_name", "n/a")
    payload = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch log stream: {log_stream}",
            "PhysicalResourceId": physical_id,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "NoEcho": False,
            "Data": data or {},
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310  # nosec B310 - CloudFormation-issued HTTPS presigned S3 URL
        event["ResponseURL"], data=payload, method="PUT"
    )
    req.add_header("content-type", "")
    req.add_header("content-length", str(len(payload)))
    urllib.request.urlopen(req, timeout=30)  # noqa: S310  # nosec B310 - trusted CFN S3 URL, HTTPS
    logger.info("Signalled CloudFormation", extra={"status": status})


def _start_bootstrap() -> str:
    """Start the bootstrap state machine if the catalog is empty; return a note."""
    import boto3

    from bdo_common import dynamo

    if not dynamo.catalog_is_empty():
        logger.info("Catalog is not empty; skipping first-create bootstrap")
        return "skipped: catalog not empty"

    state_machine_arn = os.environ["BOOTSTRAP_STATE_MACHINE_ARN"]
    sfn = boto3.client("stepfunctions")
    response = sfn.start_execution(stateMachineArn=state_machine_arn)
    execution_arn = str(response["executionArn"])
    logger.info("Started bootstrap execution", extra={"execution_arn": execution_arn})
    return execution_arn


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Fire-and-forget bootstrap on first Create; always signal CloudFormation."""
    stage = os.environ.get("STAGE", "dev")
    physical_id = event.get("PhysicalResourceId") or f"bdo-{stage}-bootstrap-trigger"
    request_type = event.get("RequestType")

    try:
        if request_type == "Create":
            result = _start_bootstrap()
            _send_cfn_response(event, context, "SUCCESS", physical_id, data={"bootstrap": result})
        else:
            # Update/Delete: never re-seed or tear down data.
            logger.info("Non-create request; no-op", extra={"request_type": request_type})
            _send_cfn_response(event, context, "SUCCESS", physical_id, data={"bootstrap": "noop"})
    except Exception as exc:  # noqa: BLE001 - must always signal CFN, then swallow
        logger.exception("bootstrap trigger failed")
        try:
            _send_cfn_response(event, context, "FAILED", physical_id, reason=str(exc)[:1000])
        except Exception:  # noqa: BLE001 - nothing else we can do; stack will time out
            logger.exception("Failed to signal CloudFormation after error")

    return {"status": "cfn-handled", "request_type": request_type}
