"""Bootstrap database roles: lambda_rds_user (IAM auth) and dba (login).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-30 00:00:00.000000

This migration creates cluster-level roles, not schema objects. It must
run as a role with CREATEROLE (the RDS master user) through the bastion
tunnel -- never from CI, which cannot reach the private RDS instance
(see docs/runbook.md).

- ``lambda_rds_user``: passwordless; authenticates via IAM
  (``GRANT rds_iam``). Used by every DB-touching Lambda (ADR-0008,
  NFR-9).
- ``dba``: LOGIN role for human pgAdmin access via the bastion
  (NFR-19). Its password is read from the ``DBA_PASSWORD`` env var,
  which the operator sources from the ``bdo-<stage>-dba-credentials``
  Secrets Manager secret. If ``DBA_PASSWORD`` is unset the dba role is
  skipped; lambda_rds_user is always created.
"""

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_GRANTS = """
GRANT USAGE ON SCHEMA public TO {role};
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role};
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role};
"""


def _create_role_if_absent(name: str, options: str = "") -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{name}') THEN
                CREATE ROLE {name} {options};
            END IF;
        END
        $$;
        """
    )


def _drop_role_if_present(name: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{name}') THEN
                EXECUTE 'DROP OWNED BY {name}';
                EXECUTE 'DROP ROLE {name}';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # lambda_rds_user: IAM-authenticated, no password.
    _create_role_if_absent("lambda_rds_user", "LOGIN")
    op.execute("GRANT rds_iam TO lambda_rds_user;")
    op.execute(_GRANTS.format(role="lambda_rds_user"))

    # dba: human login role; created only when a password is supplied.
    dba_password = os.environ.get("DBA_PASSWORD")
    if dba_password:
        escaped = dba_password.replace("'", "''")
        _create_role_if_absent("dba", f"LOGIN PASSWORD '{escaped}'")
        op.execute(_GRANTS.format(role="dba"))


def downgrade() -> None:
    _drop_role_if_present("lambda_rds_user")
    _drop_role_if_present("dba")
