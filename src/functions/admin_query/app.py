"""In-VPC admin-query Lambda -- ad-hoc SQL against RDS, read-only by default.

Replaces human pgAdmin-over-bastion access (ADR-0026). Invoke-only (no API
route) and IAM-gated. Connects as ``lambda_rds_user`` via IAM auth (ADR-0008),
reusing ``bdo_common.db``.

Read-only by default: the statement runs inside a Postgres ``READ ONLY``
transaction, so any write is rejected by the database, not merely by the code.
Pass ``{"write": true}`` to run in a normal (committing) transaction; because
``lambda_rds_user`` holds only DML (not DDL or ownership), write mode is limited
to data changes -- schema changes stay in migrations.

Payload::

    {"sql": "select ...", "params": [...], "write": false, "max_rows": 200}

Response::

    {"columns": [...], "rows": [[...]], "rowcount": n, "truncated": bool, "write": bool}
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()

_DEFAULT_MAX_ROWS = 200
_HARD_MAX_ROWS = 1000


def _jsonable(value: Any) -> Any:
    """Convert a DB value to a JSON-serialisable form for the Lambda response.

    The Lambda runtime JSON-encodes the return value, so ``Decimal`` /
    ``datetime`` / ``bytes`` / ``UUID`` must be reduced to primitives first.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        # str keeps full precision (money / large numerics) without float rounding.
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


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

    conn = db.get_connection()
    # The module-global connection may be mid-transaction from a warm reuse;
    # roll back so the read/write mode can be set on a fresh transaction.
    conn.rollback()
    conn.read_only = not write

    logger.info("admin-query", extra={"write": write, "max_rows": max_rows, "sql": sql})

    columns: list[str] = []
    rows: list[list[Any]] = []
    truncated = False
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is not None:
                columns = [col.name for col in cur.description]
                # Fetch one extra to detect (and flag) truncation without
                # materialising an unbounded result set.
                fetched = cur.fetchmany(max_rows + 1)
                truncated = len(fetched) > max_rows
                rows = [[_jsonable(v) for v in record] for record in fetched[:max_rows]]
            rowcount = cur.rowcount
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
