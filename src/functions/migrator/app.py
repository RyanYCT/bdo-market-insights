"""In-VPC migrator Lambda -- runs ``alembic upgrade head`` against RDS.

Triggered on deploy: CI invokes it after ``sam deploy`` (``make migrate-lambda``
for dev). It runs inside the VPC and authenticates to RDS with an IAM auth token
(ADR-0008), so it needs neither Secrets Manager nor NAT/internet egress
(ADR-0006). It connects as the ``lambda_migrator`` role (migration ``0003``),
which owns the schema objects and may run routine DDL.

The one-time cluster bootstrap (``0001`` schema + ``0002``/``0003`` roles) is
still applied by the operator through the bastion tunnel as the RDS master
user, because creating roles needs privileges the migrator role does not hold
(see docs/runbook.md). This function handles routine schema changes thereafter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()

# Bundled alongside the handler by the SAM makefile build (see ./Makefile).
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _iam_auth_token(host: str, port: int, user: str, region: str) -> str:
    """Generate an RDS IAM auth token (signed locally; makes no API call)."""
    import boto3

    client = boto3.client("rds", region_name=region)
    token: str = client.generate_db_auth_token(
        DBHostname=host, Port=port, DBUsername=user, Region=region
    )
    return token


def _database_url() -> str:
    """Build a psycopg-v3 SQLAlchemy URL with an IAM token as the password.

    ``URL.create`` percent-encodes the token (which contains URL-hostile
    characters), so it round-trips cleanly through Alembic's ``env.py``.
    """
    from sqlalchemy.engine import URL

    from bdo_common.config import get_settings

    settings = get_settings()
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    token = _iam_auth_token(settings.db_host, settings.db_port, settings.db_user, region)
    url = URL.create(
        "postgresql+psycopg",
        username=settings.db_user,
        password=token,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={"sslmode": "require"},
    )
    return url.render_as_string(hide_password=False)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Apply all pending migrations and return the resulting head revision."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    # The token is short-lived and sensitive: set it on the env that Alembic
    # reads and never log the resulting URL.
    os.environ["DATABASE_URL"] = _database_url()

    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

    logger.info("Applying database migrations (alembic upgrade head)")
    command.upgrade(cfg, "head")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    logger.info("Migrations applied", extra={"head_revision": head})

    return {"status": "ok", "head": head}
