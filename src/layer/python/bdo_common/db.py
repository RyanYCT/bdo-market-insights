"""psycopg3 module-global connection helper with IAM database authentication."""

from __future__ import annotations

import logging
import os
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

_connection: psycopg.Connection[tuple[Any, ...]] | None = None


def _generate_iam_token(host: str, port: int, user: str, region: str) -> str:
    """Generate an IAM auth token for RDS using boto3."""
    import boto3  # imported here to avoid cold-start cost when IAM auth is off

    client = boto3.client("rds", region_name=region)
    token: str = client.generate_db_auth_token(
        DBHostname=host,
        Port=port,
        DBUsername=user,
        Region=region,
    )
    return token


def get_connection() -> psycopg.Connection[tuple[Any, ...]]:
    """Return the module-global psycopg connection, creating it if needed.

    Reuses the same connection across Lambda invocations (warm start).
    Creates a new connection if the previous one is closed.
    Supports IAM database authentication when USE_IAM_AUTH=true.
    """
    global _connection  # noqa: PLW0603

    if _connection is not None and not _connection.closed:
        return _connection

    host = os.environ.get("DB_HOST", "localhost")
    port = int(os.environ.get("DB_PORT", "5432"))
    dbname = os.environ.get("DB_NAME", "bdo")
    user = os.environ.get("DB_USER", "lambda_rds_user")
    use_iam = os.environ.get("USE_IAM_AUTH", "false").lower() in ("1", "true", "yes")

    password: str | None = None
    if use_iam:
        aws_region = os.environ.get("AWS_REGION", "ap-northeast-1")
        password = _generate_iam_token(host, port, user, aws_region)
        logger.info("Generated IAM auth token for %s@%s:%d", user, host, port)

    conninfo = psycopg.conninfo.make_conninfo(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode="require" if use_iam else "prefer",
    )

    _connection = psycopg.connect(conninfo, autocommit=False)
    logger.info("Opened psycopg connection to %s:%d/%s", host, port, dbname)
    return _connection


def close_connection() -> None:
    """Close the module-global connection if open."""
    global _connection  # noqa: PLW0603
    if _connection is not None and not _connection.closed:
        _connection.close()
        logger.info("Closed psycopg connection")
    _connection = None
