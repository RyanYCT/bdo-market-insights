"""Bootstrap the lambda_migrator role for the in-VPC migrator Lambda.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01 00:00:00.000000

Creates ``lambda_migrator``: an IAM-authenticated, passwordless role that the
in-VPC migrator Lambda uses to run routine schema migrations (``alembic
upgrade head``) without Secrets Manager or NAT access (ADR-0006, ADR-0008).

Like ``0002_bootstrap_roles``, this is a cluster-level change that needs
CREATEROLE / ownership-transfer privileges, so it is part of the one-time
bootstrap the operator runs through the bastion tunnel as the RDS master user
(see docs/runbook.md). From the next revision on, the migrator role applies
migrations itself.

``lambda_migrator`` is made the OWNER of the four application tables so it can
ALTER/DROP them in future migrations, and a DEFAULT PRIVILEGES rule keeps the
runtime ``lambda_rds_user`` role's DML grants flowing to any tables the
migrator creates later.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_TABLES = ("item", "item_sid", "market_snapshot", "market_daily")


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
                EXECUTE 'REASSIGN OWNED BY {name} TO CURRENT_USER';
                EXECUTE 'DROP OWNED BY {name}';
                EXECUTE 'DROP ROLE {name}';
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # IAM-authenticated, passwordless migration role.
    _create_role_if_absent("lambda_migrator", "LOGIN")
    op.execute("GRANT rds_iam TO lambda_migrator;")
    op.execute("GRANT USAGE, CREATE ON SCHEMA public TO lambda_migrator;")

    # Own the application tables so routine DDL (ALTER/DROP) is permitted.
    for table in _APP_TABLES:
        op.execute(f"ALTER TABLE {table} OWNER TO lambda_migrator;")

    # Tables the migrator creates in later revisions keep granting DML to the
    # runtime role automatically (mirrors the grants in 0002).
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE lambda_migrator IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lambda_rds_user;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES FOR ROLE lambda_migrator IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM lambda_rds_user;
        """
    )
    # Return table ownership to the role running the downgrade (the master).
    for table in _APP_TABLES:
        op.execute(f"ALTER TABLE {table} OWNER TO CURRENT_USER;")
    op.execute("REVOKE ALL ON SCHEMA public FROM lambda_migrator;")
    _drop_role_if_present("lambda_migrator")
