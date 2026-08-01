"""In-VPC migrator Lambda -- applies Alembic migrations against RDS.

Three entry paths share one handler:

1. **Auto-migrate on deploy (CloudFormation custom resource).** A
   ``Custom::`` resource in the ETL stack invokes this function with a
   ``RequestType``/``ResponseURL`` event on every stack update whose migration
   fingerprint changed. It runs the *routine* ``alembic upgrade head`` as the
   privileged ``lambda_migrator`` role via IAM auth (ADR-0008) and always signals
   CloudFormation (SUCCESS/FAILED) so the stack can never hang. ``Delete`` is a
   no-op success -- migrations are data-bearing and must not be dropped when the
   custom resource is removed.
2. **Manual routine invoke.** ``make migrate-lambda`` invokes with ``{}`` (or
   ``{"mode": "routine"}``); same routine ``upgrade head`` as (1), returning the
   resulting head revision as JSON.
3. **One-time role bootstrap (``{"mode": "bootstrap"}``).** ``make db-bootstrap``
   fetches the RDS-managed master credential locally and invokes with those
   credentials in the payload. The function connects as the master user (password
   auth -- the master is deliberately kept off ``rds_iam``; see migration 0003)
   and applies ``0001``-``0003`` (schema + cluster roles), the one privileged
   step the IAM-authenticated ``lambda_migrator`` role cannot perform itself. Run
   once per environment before the first auto-migrate.

Every upgrade is serialized by a Postgres session-level **advisory lock**, so two
overlapping runs (e.g. a retried deploy racing a manual invoke) can never apply
DDL concurrently.

The function lives in the no-NAT VPC (ADR-0006): routine runs need only RDS (IAM
tokens are signed locally, no API call); the custom-resource response reaches the
CloudFormation S3 bucket through the VPC's S3 gateway endpoint.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path
from typing import Any

from aws_lambda_powertools import Logger, Tracer
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()
tracer = Tracer()

# Bundled alongside the handler by the SAM makefile build (see ./Makefile).
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Fixed 64-bit key namespacing the migration advisory lock. Derived from a
# stable string so it is deterministic across invocations and self-documenting;
# any process holding this lock blocks others until the upgrade completes.
_MIGRATION_LOCK_KEY = zlib.crc32(b"bdo-market-insights:migrations")

# Revision boundary between the privileged bootstrap (run once as the master)
# and the routine migrations the migrator role applies itself. Migrations
# 0001-0003 create the schema and cluster roles; from 0004 on the migrator role
# owns and evolves the schema.
_BOOTSTRAP_TARGET = "0003"

# Idempotent RDS IAM-auth enrollment for the login roles, run as the master
# during bootstrap. Migrations 0002/0003 grant `rds_iam` when they first create
# the roles, but an environment bootstrapped before those grants existed (or
# whose grant was lost) has roles that RDS then treats as password-auth,
# rejecting the IAM token with "password authentication failed". Re-applying the
# grant here every bootstrap makes enrollment a self-healing invariant,
# independent of the Alembic version pointer. Guarded so it is a no-op on a fresh
# database where the roles do not exist yet (the migrations create them). A plain
# GRANT of `rds_iam` TO the role does not make the master a member of anything,
# so it does not re-introduce the PG16 transitive-membership issue that 0002/0003
# guard against.
_ENSURE_IAM_ENROLLMENT_SQL = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'lambda_rds_user') THEN
        EXECUTE 'GRANT rds_iam TO lambda_rds_user';
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'lambda_migrator') THEN
        EXECUTE 'GRANT rds_iam TO lambda_migrator';
    END IF;
END
$$;
"""


def _iam_auth_token(host: str, port: int, user: str, region: str) -> str:
    """Generate an RDS IAM auth token (signed locally; makes no API call)."""
    import boto3

    client = boto3.client("rds", region_name=region)
    token: str = client.generate_db_auth_token(
        DBHostname=host, Port=port, DBUsername=user, Region=region
    )
    return token


def _build_url(username: str, password: str, host: str, port: int, database: str) -> str:
    """Build a psycopg-v3 SQLAlchemy URL, percent-encoding the password.

    ``URL.create`` percent-encodes the password (an RDS IAM token contains
    URL-hostile characters), so it round-trips cleanly through Alembic's
    ``env.py`` without being mistaken for ConfigParser interpolation.
    """
    from sqlalchemy.engine import URL

    url = URL.create(
        "postgresql+psycopg",
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
        query={"sslmode": "require"},
    )
    return url.render_as_string(hide_password=False)


def _iam_database_url() -> str:
    """Routine-migration URL: connect as ``lambda_migrator`` via an IAM token."""
    from bdo_common.config import get_settings

    settings = get_settings()
    region = os.environ.get("AWS_REGION", "us-east-1")
    token = _iam_auth_token(settings.db_host, settings.db_port, settings.db_user, region)
    return _build_url(
        settings.db_user, token, settings.db_host, settings.db_port, settings.db_name
    )


def _master_database_url(username: str, password: str) -> str:
    """Bootstrap URL: connect as the RDS master with the supplied password."""
    from bdo_common.config import get_settings

    settings = get_settings()
    return _build_url(username, password, settings.db_host, settings.db_port, settings.db_name)


def _ensure_iam_enrollment(master_url: str) -> None:
    """Idempotently (re)grant ``rds_iam`` to the login roles, as the master.

    Self-heals an environment whose ``lambda_migrator`` / ``lambda_rds_user`` role
    is not enrolled in RDS IAM auth (so IAM-token connections fail with "password
    authentication failed"). Safe to run every bootstrap and a no-op on a fresh
    database (the roles are created by the migrations).
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    engine = create_engine(master_url, poolclass=NullPool)
    try:
        with engine.connect() as raw_conn:
            conn = raw_conn.execution_options(isolation_level="AUTOCOMMIT")
            logger.info("Ensuring RDS IAM enrollment for login roles")
            conn.execute(text(_ENSURE_IAM_ENROLLMENT_SQL))
    finally:
        engine.dispose()


def _run_upgrade(database_url: str, target: str) -> str | None:
    """Apply migrations up to ``target`` under a serializing advisory lock.

    The lock is held on its own AUTOCOMMIT connection for the whole upgrade
    (which runs on a separate Alembic-managed connection), so concurrent migrator
    runs serialize rather than racing DDL. The token/password in ``database_url``
    is sensitive: it is set on the env Alembic reads and never logged.
    """
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    os.environ["DATABASE_URL"] = database_url

    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))

    lock_engine = create_engine(database_url, poolclass=NullPool)
    try:
        with lock_engine.connect() as raw_conn:
            lock_conn = raw_conn.execution_options(isolation_level="AUTOCOMMIT")
            logger.info("Acquiring migration advisory lock")
            lock_conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": _MIGRATION_LOCK_KEY})
            try:
                logger.info("Applying database migrations", extra={"target": target})
                command.upgrade(cfg, target)
                head = ScriptDirectory.from_config(cfg).get_current_head()
                logger.info("Migrations applied", extra={"head_revision": head})
            finally:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _MIGRATION_LOCK_KEY}
                )
    finally:
        lock_engine.dispose()
    return head


def _run_routine() -> dict[str, Any]:
    """Routine path: ``upgrade head`` as ``lambda_migrator`` via IAM auth."""
    head = _run_upgrade(_iam_database_url(), "head")
    return {"status": "ok", "head": head}


def _run_bootstrap(event: dict[str, Any]) -> dict[str, Any]:
    """One-time path: apply the privileged bootstrap as the RDS master user.

    Credentials come from the invocation payload (``make db-bootstrap`` reads
    them from the RDS-managed master secret), so the function needs no Secrets
    Manager access from inside the no-NAT VPC. An optional ``dba_password``
    provisions the human ``dba`` login role (migration 0002); omit it to skip.
    """
    username = event["master_username"]
    password = event["master_password"]
    target = event.get("target", _BOOTSTRAP_TARGET)

    dba_password = event.get("dba_password")
    if dba_password:
        os.environ["DBA_PASSWORD"] = dba_password

    master_url = _master_database_url(username, password)
    # Self-heal IAM-auth enrollment before/regardless of the schema upgrade, so a
    # pre-existing environment whose role lost its rds_iam grant is repaired even
    # when Alembic is already past the bootstrap boundary.
    _ensure_iam_enrollment(master_url)
    head = _run_upgrade(master_url, target)
    return {"status": "ok", "mode": "bootstrap", "head": head}


def _send_cfn_response(
    event: dict[str, Any],
    context: LambdaContext,
    status: str,
    physical_id: str,
    *,
    data: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """PUT a custom-resource response to the CloudFormation-presigned S3 URL.

    Reaches S3 through the VPC's S3 gateway endpoint (ADR-0006, no NAT). Any
    failure here would hang the stack, so callers invoke this on every path.
    """
    import json
    import urllib.request

    log_stream = getattr(context, "log_stream_name", "n/a")
    payload = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch log stream: {log_stream}",
            "PhysicalResourceId": physical_id,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "NoEcho": False,
            "Data": data or {},
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310  # nosec B310 - CloudFormation-issued HTTPS presigned S3 URL
        event["ResponseURL"], data=payload, method="PUT"
    )
    req.add_header("content-type", "")
    req.add_header("content-length", str(len(payload)))
    urllib.request.urlopen(req, timeout=30)  # noqa: S310  # nosec B310 - trusted CFN S3 URL, HTTPS
    logger.info("Signalled CloudFormation", extra={"status": status})


def _handle_custom_resource(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Run routine migrations for a CloudFormation custom resource, always signalling.

    A stable ``PhysicalResourceId`` keeps CloudFormation from treating an update
    as a replace+delete. ``Delete`` is a deliberate no-op success: removing the
    custom resource must never drop the schema.
    """
    stage = os.environ.get("STAGE", "dev")
    physical_id = event.get("PhysicalResourceId") or f"bdo-{stage}-schema-migration"
    request_type = event.get("RequestType")

    try:
        if request_type == "Delete":
            logger.info("Custom-resource Delete: skipping migrations (schema is data-bearing)")
            _send_cfn_response(
                event, context, "SUCCESS", physical_id, data={"head": "skipped-on-delete"}
            )
        else:
            head = _run_upgrade(_iam_database_url(), "head")
            _send_cfn_response(
                event, context, "SUCCESS", physical_id, data={"head": head or "none"}
            )
    except Exception as exc:  # noqa: BLE001 - must always signal CFN, then swallow
        logger.exception("Auto-migration failed")
        try:
            _send_cfn_response(event, context, "FAILED", physical_id, reason=str(exc)[:1000])
        except Exception:  # noqa: BLE001 - nothing else we can do; stack will time out
            logger.exception("Failed to signal CloudFormation after migration error")

    return {"status": "cfn-handled", "request_type": request_type}


@logger.inject_lambda_context
@tracer.capture_lambda_handler
def handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    """Dispatch to the custom-resource, bootstrap, or routine path.

    A CloudFormation event (``RequestType`` + ``ResponseURL``) is handled with
    guaranteed signalling; ``{"mode": "bootstrap"}`` runs the one-time master
    bootstrap; anything else runs the routine ``upgrade head``.
    """
    if event.get("RequestType") and event.get("ResponseURL"):
        return _handle_custom_resource(event, context)

    if event.get("mode") == "bootstrap":
        return _run_bootstrap(event)

    return _run_routine()
