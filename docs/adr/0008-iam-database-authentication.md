# ADR-0008: IAM Database Authentication

## Status

Accepted

## Context

Lambdas need to authenticate to RDS Postgres. Options considered were a static
password stored in environment variables, Secrets Manager with automatic
rotation, and IAM database authentication. Static passwords are a security risk
and rotation burden.

## Decision

Use IAM database authentication for all Lambda-to-RDS connections. Auth tokens
are generated per-invocation via `boto3` `generate_db_auth_token()` and used as
the password in the `psycopg` connection string.

## Consequences

- (+) No static passwords anywhere in the system.
- (+) Automatic credential rotation via IAM (no Secrets Manager cost).
- (+) Audit trail of database access in CloudTrail.
- (-) Tokens have a 15-minute lifetime (acceptable for Lambda execution).
- (-) Requires IAM policy and RDS instance configuration for IAM auth.
- (-) Slightly more complex connection setup compared to a static password.

## Notes

### Master role lockout during bootstrap

RDS Postgres has no superuser, and PostgreSQL 16 auto-grants the creating role
membership in every role it creates. Because the runtime role (`lambda_rds_user`)
and the migrator role (`lambda_migrator`) both hold `rds_iam`, that
auto-membership makes the master (`postgres`) a *transitive* member of `rds_iam`.
RDS then routes the master to PAM (IAM) auth and breaks master **password**
login with `FATAL: PAM authentication failed for user "postgres"`.

To avoid this, the role-bootstrap migrations (`0002`/`0003`) end with
`REVOKE … FROM CURRENT_USER`, so the master keeps password auth after creating
the IAM-auth roles. If a master is ever locked out this way it can still connect
with an IAM token (it now holds `rds_iam`) and revoke the memberships manually.
The operational steps live in the runbook's "First-time role bootstrap".
