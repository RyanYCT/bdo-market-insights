"""One-time privileged database bootstrap for an environment (no bastion).

Applies the bootstrap migrations (``0001``-``0003``: the schema and the cluster
roles ``lambda_rds_user`` / ``lambda_migrator`` / optional ``dba``) that the
IAM-authenticated migrator role cannot create itself, because creating roles and
transferring table ownership need the RDS master user (ADR-0025).

Rather than open a bastion tunnel, this reads the RDS-managed master credential
locally (the operator has Secrets Manager access) and invokes the in-VPC
migrator Lambda in *bootstrap* mode, passing the master username/password in the
one-time invocation payload. The migrator -- which runs inside the no-NAT VPC
and can reach RDS but not Secrets Manager -- uses them to connect as the master
and run ``alembic upgrade <target>`` (default ``0003``).

Run once per environment:

    make deploy STAGE=<env> AUTO_MIGRATE=false   # create infra incl. the migrator
    make db-bootstrap STAGE=<env>                # this script (roles + schema)
    make deploy STAGE=<env>                       # auto-migrate applies 0004+

From then on, routine migrations run automatically on deploy (ADR-0025); this
script is only needed again for a brand-new environment.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import boto3


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


def _master_credentials(sm: Any, secret_arn: str) -> tuple[str, str]:
    """Return ``(username, password)`` from the RDS-managed master secret."""
    secret = json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])
    return str(secret["username"]), str(secret["password"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time privileged DB bootstrap via the migrator Lambda"
    )
    parser.add_argument("--stage", default="dev", help="dev / prod (default: dev)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--target",
        default="0003",
        help="Alembic revision to bootstrap up to (default: 0003)",
    )
    parser.add_argument(
        "--dba-password",
        default=None,
        help="Optional: also create the human 'dba' login role with this password",
    )
    args = parser.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    sm = boto3.client("secretsmanager", region_name=args.region)
    lambda_client = boto3.client("lambda", region_name=args.region)

    username, password = _master_credentials(sm, _stack_output(cf, args.stage, "MasterSecretArn"))

    payload: dict[str, Any] = {
        "mode": "bootstrap",
        "master_username": username,
        "master_password": password,
        "target": args.target,
    }
    if args.dba_password:
        payload["dba_password"] = args.dba_password

    function_name = f"bdo-{args.stage}-migrator"
    print(f"Invoking {function_name} in bootstrap mode (target {args.target})...")
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )

    body = response["Payload"].read().decode("utf-8")
    if response.get("FunctionError"):
        raise SystemExit(f"bootstrap failed ({response['FunctionError']}): {body}")

    print(f"Bootstrap complete: {body}")
    print(f"Now run `make deploy STAGE={args.stage}` to apply routine migrations.")


if __name__ == "__main__":
    main()
