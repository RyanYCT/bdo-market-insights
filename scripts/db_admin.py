"""Run ad-hoc SQL against RDS through the in-VPC admin-query Lambda (ADR-0026).

Replaces human pgAdmin-over-bastion access: invokes ``bdo-<stage>-admin-query``,
which connects as ``lambda_rds_user`` via IAM auth and runs the statement in a
Postgres READ ONLY transaction by default. Pass ``--write`` to run DML in a
committing transaction (still no DDL -- that goes through migrations).

    make db-admin STAGE=dev SQL='select count(*) from item'
    make db-admin STAGE=dev SQL="delete from market_snapshot where id = 42" WRITE=1

Requires AWS credentials with ``lambda:InvokeFunction`` on the function.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import boto3


def _print_table(columns: list[str], rows: list[list[Any]]) -> None:
    """Render a simple fixed-width table (or a message when there are no rows)."""
    if not columns:
        print("(no result set)")
        return
    if not rows:
        print(" | ".join(columns))
        print("(0 rows)")
        return
    widths = [len(c) for c in columns]
    str_rows = [["" if v is None else str(v) for v in row] for row in rows]
    for row in str_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    line = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    print(line)
    print("-+-".join("-" * w for w in widths))
    for row in str_rows:
        print(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ad-hoc SQL via the admin-query Lambda")
    parser.add_argument("--stage", default="dev", help="dev / prod (default: dev)")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--sql", required=True, help="SQL statement to run")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Run in a committing (read-write) transaction; default is read-only",
    )
    parser.add_argument(
        "--max-rows", type=int, default=200, help="Max rows to return (default: 200, cap: 1000)"
    )
    args = parser.parse_args()

    payload: dict[str, Any] = {"sql": args.sql, "write": args.write, "max_rows": args.max_rows}

    lambda_client = boto3.client("lambda", region_name=args.region)
    function_name = f"bdo-{args.stage}-admin-query"
    mode = "read-write" if args.write else "read-only"
    print(f"Invoking {function_name} ({mode})...")
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )

    body = response["Payload"].read().decode("utf-8")
    if response.get("FunctionError"):
        raise SystemExit(f"admin-query failed ({response['FunctionError']}): {body}")

    result = json.loads(body)
    _print_table(result.get("columns", []), result.get("rows", []))
    footer = f"rowcount={result.get('rowcount')}"
    if result.get("truncated"):
        footer += f" (showing first {len(result.get('rows', []))} rows; more exist)"
    if result.get("write"):
        footer += " [committed]"
    print(footer)


if __name__ == "__main__":
    main()
