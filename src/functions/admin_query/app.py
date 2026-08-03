"""In-VPC admin-query Lambda -- ad-hoc SQL against RDS, read-only by default.

Replaces human pgAdmin-over-bastion access (ADR-0026). Invoke-only (no API
route) and IAM-gated. Connects as ``lambda_rds_user`` via IAM auth (ADR-0008),
reusing ``bdo_common.db``.

Read-only by default: the statement runs inside a Postgres ``READ ONLY``
transaction, so any write is rejected by the database, not merely by the code.
Pass ``{"write": true}`` to run in a normal (committing) transaction; because
``lambda_rds_user`` holds only DML (not DDL or ownership), write mode is limited
to data changes -- schema changes stay in migrations.

A large read is streamed through a server-side cursor so only ``max_rows`` (+1)
rows are pulled into the function's memory, and a per-statement server-side
timeout bounds runaway queries.

Payload::

    {"sql": "select ...", "params": [...], "write": false, "max_rows": 200}

Response::

    {"columns": [...], "rows": [[...]], "rowcount": n, "truncated": bool, "write": bool}
"""

from __future__ import annotations

import datetime
import decimal
import math
import os
import uuid
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()

_DEFAULT_MAX_ROWS = 200
_HARD_MAX_ROWS = 1000
_DEFAULT_STATEMENT_TIMEOUT_MS = 30_000

# Statements whose results can be streamed via a server-side cursor (DECLARE
# CURSOR accepts only these). Utility statements (SHOW / EXPLAIN) return small
# results and go through a client cursor instead.
_STREAMABLE_READ_PREFIXES = ("select", "with", "table", "values")


def _jsonable(value: Any) -> Any:
    """Reduce a DB value to a JSON-serialisable form for the Lambda response.

    The Lambda runtime JSON-encodes the return value, so ``Decimal`` /
    ``datetime`` / ``bytes`` / ``UUID`` must become primitives. ``jsonb`` and
    array columns arrive as ``dict`` / ``list`` and are recursed into (a typed
    array may still hold ``Decimal`` etc.).
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        # NaN / Infinity are not valid JSON; render them as strings.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, decimal.Decimal):
        # str keeps full precision (money / large numerics) without float rounding.
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _statement_timeout_ms() -> int:
    """Server-side per-statement timeout (ms); overridable via env, floored at 1s."""
    try:
        return max(
            1_000, int(os.environ.get("ADMIN_QUERY_TIMEOUT_MS", _DEFAULT_STATEMENT_TIMEOUT_MS))
        )
    except (TypeError, ValueError):
        return _DEFAULT_STATEMENT_TIMEOUT_MS


def _is_streamable_read(sql: str) -> bool:
    return sql.lstrip().lower().startswith(_STREAMABLE_READ_PREFIXES)


def _collect(cur: Any, max_rows: int) -> tuple[list[str], list[list[Any]], bool]:
    """Return (columns, rows, truncated) for a cursor, or empty when no result set.

    Fetches one row beyond ``max_rows`` to flag truncation. On a server-side
    cursor this is a bounded ``FETCH FORWARD``, so a large result never fully
    lands in the function's memory.
    """
    if cur.description is None:
        return [], [], False
    columns = [col.name for col in cur.description]
    fetched = cur.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    rows = [[_jsonable(v) for v in record] for record in fetched[:max_rows]]
    return columns, rows, truncated


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Run one ad-hoc statement; read-only unless ``write`` is set."""
    from bdo_common import db

    sql = event.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("event.sql is required and must be a non-empty string")

    write = bool(event.get("write", False))
    params = event.get("params")
    max_rows = max(1, min(int(event.get("max_rows", _DEFAULT_MAX_ROWS)), _HARD_MAX_ROWS))
    timeout_ms = _statement_timeout_ms()

    conn = db.get_connection()
    # The module-global connection may be mid-transaction from a warm reuse;
    # roll back so the read/write mode can be set on a fresh transaction.
    conn.rollback()
    conn.read_only = not write

    logger.info("admin-query", extra={"write": write, "max_rows": max_rows, "sql": sql})

    columns: list[str] = []
    rows: list[list[Any]] = []
    truncated = False
    rowcount = 0
    try:
        # Bound server-side execution time: the Lambda timeout would kill the
        # caller but not the running query. SET LOCAL is scoped to this
        # transaction (reverted on commit/rollback). The value is an int we
        # control, so inlining it is safe (SET takes no bind parameters).
        with conn.cursor() as setup:
            setup.execute(f"SET LOCAL statement_timeout = {timeout_ms}")

        if not write and _is_streamable_read(sql):
            # Server-side cursor: rows stream from the server so a large result
            # can't materialise the whole set into the function's memory.
            with conn.cursor(name="admin_query") as cur:
                cur.execute(sql, params)
                columns, rows, truncated = _collect(cur, max_rows)
            rowcount = len(rows)
        else:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns, rows, truncated = _collect(cur, max_rows)
                # For a result set, the client cursor is a utility statement
                # (small); for DML, rowcount is the affected-row count.
                rowcount = len(rows) if cur.description is not None else cur.rowcount

        if write:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        # Leave the warm connection defaulting to read-only for the next invoke.
        conn.read_only = True

    logger.info(
        "admin-query complete",
        extra={"rowcount": rowcount, "returned": len(rows), "truncated": truncated},
    )
    return {
        "columns": columns,
        "rows": rows,
        "rowcount": rowcount,
        "truncated": truncated,
        "write": write,
    }
