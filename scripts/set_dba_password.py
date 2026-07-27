"""Re-sync the dba Postgres role password to the current dba secret (per-session).

The dba secret is created only while the bastion is up (ADR-0020, gated on
``EnableBastion``) and gets a fresh random password each time it is created. The
``dba`` role in Postgres persists across bastion sessions, so from the second
session on its stored password drifts from the secret. Run this once each time
you bring the bastion up (after ``make db-tunnel-up``) so pgAdmin can log in as
``dba``.

Requires an open bastion tunnel (``localhost:5432`` -> RDS) and AWS credentials.
Connects as the RDS master and ``ALTER``s the ``dba`` role password to the value
in the dba secret; both secrets are resolved from the DataStack outputs
(``MasterSecretArn`` / ``DbaSecretArn``).

    make db-tunnel-up STAGE=prod       # terminal 1 (leave running)
    make dba-password STAGE=prod       # terminal 2
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import boto3
import psycopg
from psycopg import sql


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
    raise SystemExit(
        f"error: output {key} not found on {prefix}* stacks. "
        "Is the stack deployed with ENABLE_BASTION=true (dba secret exists)?"
    )


def _secret_password(sm: Any, secret_arn: str) -> str:
    """Return the ``password`` field from a JSON Secrets Manager secret."""
    secret_string = sm.get_secret_value(SecretId=secret_arn)["SecretString"]
    return str(json.loads(secret_string)["password"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-sync the dba Postgres role password to the current dba secret"
    )
    parser.add_argument("--stage", default="dev", help="dev / prod (default: dev)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--host", default="localhost", help="tunnelled host (default: localhost)")
    parser.add_argument("--port", type=int, default=5432, help="local tunnel port (default: 5432)")
    args = parser.parse_args()

    cf = boto3.client("cloudformation", region_name=args.region)
    sm = boto3.client("secretsmanager", region_name=args.region)

    master_pw = _secret_password(sm, _stack_output(cf, args.stage, "MasterSecretArn"))
    dba_pw = _secret_password(sm, _stack_output(cf, args.stage, "DbaSecretArn"))

    print(f"Connecting as master to {args.host}:{args.port} (stage {args.stage})...")
    with psycopg.connect(
        host=args.host,
        port=args.port,
        dbname="bdo",
        user="postgres",
        password=master_pw,
        sslmode="require",
        autocommit=True,
    ) as conn:
        # sql.Literal safely quotes the password (ALTER ROLE takes a string literal).
        conn.execute(sql.SQL("ALTER ROLE dba WITH PASSWORD {}").format(sql.Literal(dba_pw)))
    print("Synced: the dba role password now matches the current dba secret.")


if __name__ == "__main__":
    main()
