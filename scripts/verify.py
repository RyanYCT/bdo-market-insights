"""Post-deploy smoke test for an environment (ADR-0029): `make verify`.

Confirms a deploy produced a *serving* stack, key-free and uniform across dev/prod:

1. **Liveness** -- `GET {ApiUrl}/v1/openapi.json` returns 200. That route is public
   (no `x-api-key`), so it needs no key; a 200 proves API Gateway + a Lambda serve.
2. **RDS-backed serving** -- invoke the admin-query Lambda (ADR-0026) with
   ``select 1``; success proves the in-VPC Postgres path (IAM auth) works.
3. **Data present** -- the items table is non-empty. Because the bootstrap runs
   asynchronously (ADR-0028) and a fresh catalog is tens of thousands of items,
   this is **execution-aware**: it waits while the bootstrap state machine is
   RUNNING, passes as soon as items appear, and fails fast if the execution
   FAILED -- bounded by ``--wait`` (env ``VERIFY_WAIT``).

Market *data* is intentionally not asserted: RDS market rows come from the hourly
ETL, not the bootstrap, so they are absent on a fresh environment (check (2)
proves the path serves). Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import boto3

#: Step Functions execution states that mean the bootstrap will not populate data.
_EXEC_FAILED = frozenset({"FAILED", "TIMED_OUT", "ABORTED"})


def _stack_output(cf: Any, stage: str, key: str) -> str:
    """Resolve an Output value by key across the ``bdo-market-<stage>`` stacks."""
    prefix = f"bdo-market-{stage}"
    for page in cf.get_paginator("describe_stacks").paginate():
        for stack in page["Stacks"]:
            if not stack["StackName"].startswith(prefix):
                continue
            for output in stack.get("Outputs", []):
                if output["OutputKey"] == key:
                    return str(output["OutputValue"])
    raise SystemExit(f"error: output {key} not found on {prefix}* stacks. Is the stack deployed?")


def check_liveness(
    api_url: str,
    *,
    attempts: int = 3,
    backoff: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[bool, str]:
    """GET the public OpenAPI document; a 200 proves the API serves.

    Retries a few times: right after a deploy, API Gateway / Lambda may need a
    moment to warm, so a single attempt can spuriously fail an otherwise-healthy
    stack. Backs off linearly between attempts.
    """
    url = f"{api_url}/v1/openapi.json"
    last = f"liveness: GET {url} produced no result"
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, method="GET")  # noqa: S310 - fixed https API URL
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                code = resp.status
            if code == 200:
                return True, f"liveness: GET /v1/openapi.json -> {code}"
            last = f"liveness: GET /v1/openapi.json -> {code}"
        except Exception as exc:  # noqa: BLE001 - any error is a liveness failure
            last = f"liveness: GET {url} failed: {exc}"
        if attempt < attempts:
            sleep(backoff * attempt)
    return False, last


def check_rds(lambda_client: Any, stage: str) -> tuple[bool, str]:
    """Invoke the admin-query Lambda with ``select 1`` to prove the RDS path."""
    fn = f"bdo-{stage}-admin-query"
    payload = json.dumps({"sql": "select 1 as ok"}).encode("utf-8")
    try:
        resp = lambda_client.invoke(
            FunctionName=fn, InvocationType="RequestResponse", Payload=payload
        )
        body = resp["Payload"].read().decode("utf-8")
        if resp.get("FunctionError"):
            return False, f"rds: admin-query failed ({resp['FunctionError']}): {body[:200]}"
        rows = json.loads(body).get("rows") or []
    except Exception as exc:  # noqa: BLE001 - any error is an RDS-path failure
        return False, f"rds: admin-query invoke failed: {exc}"
    ok = bool(rows)
    return ok, f"rds: admin-query select 1 -> {'ok' if ok else 'no rows'}"


def data_check(
    items_present: Callable[[], bool],
    latest_execution: Callable[[], dict[str, str] | None],
    *,
    wait: float,
    poll: float,
    grace: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """Wait (execution-aware) until the items table is non-empty.

    Returns as soon as items appear; fails fast on a failed bootstrap; otherwise
    waits while the bootstrap execution is RUNNING, bounded by ``wait``.

    When no execution is listed yet, it keeps waiting up to ``grace`` seconds
    before concluding the bootstrap was never triggered: right after a converged
    deploy ``ListExecutions`` is eventually consistent, so a just-started
    execution can briefly list as absent.
    """
    start = now()
    deadline = start + wait
    grace_deadline = min(start + grace, deadline)
    while True:
        if items_present():
            return True, "data: items table is non-empty"
        execution = latest_execution()
        if execution is None:
            # No execution listed yet: tolerate the ListExecutions consistency
            # window before declaring the bootstrap was never triggered.
            if now() >= grace_deadline:
                return (
                    False,
                    "data: items table empty and no bootstrap execution found; "
                    "run `make bootstrap`",
                )
            sleep(poll)
            continue
        status = execution["status"]
        arn = execution.get("executionArn", "?")
        if status in _EXEC_FAILED:
            return False, f"data: bootstrap execution {status}: {arn}"
        if status == "SUCCEEDED":
            # The items Scan is eventually consistent; re-check once so a read
            # lag at the SUCCEEDED transition does not false-fail.
            if items_present():
                return True, "data: items table is non-empty"
            return False, f"data: bootstrap SUCCEEDED but items table is empty: {arn}"
        # RUNNING / PENDING_REDRIVE: still populating.
        if now() >= deadline:
            return (
                False,
                f"data: timed out after {wait:.0f}s; bootstrap still running ({arn}). "
                "Re-run `make verify` or raise VERIFY_WAIT.",
            )
        sleep(poll)


def _items_present(ddb: Any, table: str) -> bool:
    """True if the items table has at least one row (cheap consistent Scan Limit=1)."""
    resp = ddb.scan(TableName=table, Limit=1, ProjectionExpression="id", ConsistentRead=True)
    return bool(resp.get("Items"))


def _latest_bootstrap_execution(sfn: Any, state_machine_arn: str) -> dict[str, str] | None:
    """Return the most recent bootstrap execution ``{status, executionArn}`` or None."""
    executions = sfn.list_executions(stateMachineArn=state_machine_arn, maxResults=1).get(
        "executions", []
    )
    if not executions:
        return None
    return {"status": executions[0]["status"], "executionArn": executions[0]["executionArn"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-deploy smoke test for an environment")
    parser.add_argument("--stage", default="dev", help="dev / prod (default: dev)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--wait",
        type=float,
        default=1200.0,
        help="Max seconds to wait for the async bootstrap to populate data (default: 1200)",
    )
    parser.add_argument(
        "--poll", type=float, default=15.0, help="Poll interval seconds (default: 15)"
    )
    args = parser.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    lambda_client = boto3.client("lambda", region_name=args.region)
    ddb = boto3.client("dynamodb", region_name=args.region)
    sfn = boto3.client("stepfunctions", region_name=args.region)

    api_url = _stack_output(cf, args.stage, "ApiUrl")
    bootstrap_arn = _stack_output(cf, args.stage, "BootstrapStateMachineArn")
    table = f"bdo-{args.stage}-items"

    print(f"Verifying stage {args.stage} ({args.region})...")
    results = [
        check_liveness(api_url),
        check_rds(lambda_client, args.stage),
        data_check(
            lambda: _items_present(ddb, table),
            lambda: _latest_bootstrap_execution(sfn, bootstrap_arn),
            wait=args.wait,
            poll=args.poll,
            grace=max(4 * args.poll, 60.0),
        ),
    ]

    ok = True
    for passed, message in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {message}")
        ok = ok and passed

    if not ok:
        raise SystemExit(f"verify: stage {args.stage} FAILED")
    print(f"verify: stage {args.stage} OK")


if __name__ == "__main__":
    main()
