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
