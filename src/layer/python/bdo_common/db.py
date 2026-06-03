"""psycopg3 module-global connection helper with IAM database authentication."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import psycopg

from bdo_common.config import get_settings

logger = logging.getLogger(__name__)

_connection: psycopg.Connection[tuple[Any, ...]] | None = None
_connection_created_at: float = 0.0

# IAM auth tokens expire after 15 minutes; reconnect proactively at 12 minutes.
_IAM_TOKEN_TTL_SECONDS: int = 12 * 60


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


def _is_connection_expired(use_iam: bool) -> bool:
    """Return True if the IAM token backing the connection may have expired."""
    if not use_iam:
        return False
    elapsed = time.monotonic() - _connection_created_at
    return elapsed >= _IAM_TOKEN_TTL_SECONDS


def get_connection() -> psycopg.Connection[tuple[Any, ...]]:
    """Return the module-global psycopg connection, creating it if needed.

    Reuses the same connection across Lambda invocations (warm start).
    Creates a new connection if the previous one is closed or if the IAM
    auth token has exceeded its TTL (12 minutes).
    Supports IAM database authentication when USE_IAM_AUTH=true.
    """
    global _connection, _connection_created_at  # noqa: PLW0603

    settings = get_settings()
    use_iam = settings.use_iam_auth

    if _connection is not None and not _connection.closed:
        if not _is_connection_expired(use_iam):
            return _connection
        # Token is about to expire; close and reconnect.
        logger.info("IAM token TTL exceeded, reconnecting")
        _connection.close()
        _connection = None

    host = settings.db_host
    port = settings.db_port
    dbname = settings.db_name
    user = settings.db_user

    password: str | None = None
    if use_iam:
        aws_region = os.environ.get("AWS_REGION", "us-east-1")
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
    _connection_created_at = time.monotonic()
    logger.info("Opened psycopg connection to %s:%d/%s", host, port, dbname)
    return _connection


def close_connection() -> None:
    """Close the module-global connection if open."""
    global _connection, _connection_created_at  # noqa: PLW0603
    if _connection is not None and not _connection.closed:
        _connection.close()
        logger.info("Closed psycopg connection")
    _connection = None
    _connection_created_at = 0.0
