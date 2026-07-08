"""Fixtures for the end-to-end ETL integration tests.

These tests exercise the real ETL handlers against a real Postgres database and
a moto-mocked DynamoDB. Only arsha.io HTTP calls are stubbed; everything else
runs for real -- the schema is built by the Alembic ``0001`` migration, and the
handlers use the layer's actual parameterized SQL, transactions, and idempotent
upserts.

A Postgres server is not available everywhere (the local sandbox has none), so
the whole suite skips unless ``TEST_DATABASE_URL`` points at a reachable
database. CI provides one through a ``postgres`` service container.
"""

from __future__ import annotations

import os
import pathlib
import urllib.parse
from collections.abc import Iterator
from typing import Any

import boto3
import moto
import psycopg
import pytest

# Dummy AWS credentials so boto3/moto can build clients without real ones.
# (tests/conftest.py already pins AWS_REGION/AWS_DEFAULT_REGION.)
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_TABLE_NAME = "bdo-dev-items"


def _to_psycopg_url(url: str) -> str:
    """Rewrite a libpq URL to SQLAlchemy's psycopg-v3 driver form.

    Alembic builds a SQLAlchemy engine from ``DATABASE_URL``; a plain
    ``postgresql://`` URL resolves to the psycopg2 dialect, but this project
    ships only psycopg v3, so the ``+psycopg`` driver must be explicit.
    """
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Return the test database URL, or skip the whole integration suite."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; integration tests require Postgres")
    return url


@pytest.fixture(scope="session")
def _db_env(pg_url: str) -> Iterator[None]:
    """Point the layer's connection settings at the test DB for the session.

    The handlers reach Postgres through ``bdo_common.db.get_connection`` (which
    reads ``DB_*`` env vars); IAM auth is off, so libpq picks up the password
    from ``PGPASSWORD``. ``DATABASE_URL`` is set for Alembic's ``env.py``.
    """
    parts = urllib.parse.urlsplit(pg_url)
    os.environ["DB_HOST"] = parts.hostname or "localhost"
    os.environ["DB_PORT"] = str(parts.port or 5432)
    os.environ["DB_NAME"] = parts.path.lstrip("/") or "bdo"
    os.environ["DB_USER"] = parts.username or "postgres"
    if parts.password:
        os.environ["PGPASSWORD"] = parts.password
    os.environ["DATABASE_URL"] = _to_psycopg_url(pg_url)
    os.environ["USE_IAM_AUTH"] = "false"

    from bdo_common import config, db

    config.get_settings.cache_clear()
    db.close_connection()
    yield
    db.close_connection()


@pytest.fixture(scope="session")
def _schema(_db_env: None) -> None:
    """Create the schema via Alembic migrations, including the real ``0004``.

    Runs ``0001`` (the four core tables), then stamps past ``0002``/``0003``
    (which bootstrap cluster roles -- ``rds_iam`` and friends -- that only exist
    on RDS, not on a vanilla Postgres) and runs the real ``0004`` migration. This
    exercises the actual ``market_summary`` migration rather than hand-written
    DDL, so a broken migration can never pass the suite.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    command.upgrade(cfg, "0001")
    # Mark 0002/0003 as applied without running them (they need RDS-only roles),
    # then run the real 0004 migration on top.
    command.stamp(cfg, "0003")
    command.upgrade(cfg, "0004")


@pytest.fixture
def db_conn(_schema: None, pg_url: str) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
    """Yield a clean autocommit connection; truncates all tables per test.

    Used by tests to seed/inspect rows out-of-band. The handler under test opens
    its own connection via ``bdo_common.db``; that module global is reset here so
    each test starts from a fresh connection too.
    """
    from bdo_common import db

    conn: psycopg.Connection[tuple[Any, ...]] = psycopg.connect(pg_url, autocommit=True)
    conn.execute(
        "TRUNCATE market_summary, market_daily, market_snapshot, item_sid, item"
        " RESTART IDENTITY CASCADE"
    )
    db.close_connection()
    try:
        yield conn
    finally:
        conn.close()
        db.close_connection()


@pytest.fixture
def dynamo_table(_db_env: None) -> Iterator[None]:
    """Provide a moto-mocked ``bdo-<stage>-items`` table, active for the test body."""
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name=os.environ["AWS_REGION"])
        client.create_table(
            TableName=_TABLE_NAME,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "id", "AttributeType": "N"},
                {"AttributeName": "category", "AttributeType": "S"},
                {"AttributeName": "tracked", "AttributeType": "S"},
                {"AttributeName": "t", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "category-tracked-index",
                    "KeySchema": [
                        {"AttributeName": "category", "KeyType": "HASH"},
                        {"AttributeName": "tracked", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "tracked-index",
                    "KeySchema": [{"AttributeName": "t", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield
