# ADR-0025: Auto-migrations on deploy, one-time bootstrap, and expand/contract

## Status

Accepted (refines ADR-0024 item 2)

## Context

ADR-0024 decided that migrations should run themselves on deploy instead of
through a human DB tunnel. This ADR records *how* that is implemented and the
schema-change discipline it requires.

Two facts constrain the implementation:

- The migrator Lambda lives in the no-NAT VPC (ADR-0006). It reaches RDS but not
  the internet or Secrets Manager. A CloudFormation custom resource must POST its
  result to a presigned S3 URL, and the migrator can only do that through a VPC
  endpoint.
- The privileged bootstrap (create the `lambda_rds_user` / `lambda_migrator`
  roles, transfer table ownership) needs the RDS **master** user, which
  authenticates by password and is deliberately kept off `rds_iam` (migration
  0003). The IAM-authenticated `lambda_migrator` role cannot perform it.

Running the full bootstrap as the master on *every* deploy would put a
master-privileged step on the recurring deploy path forever, for something that
is genuinely one-time per environment. That erodes the least-privilege role model
the schema was built around.

## Decision

**1. Routine migrations auto-run on deploy as `lambda_migrator` (IAM).** A
`AWS::CloudFormation::CustomResource` (`SchemaMigration`) in the ETL stack invokes
the migrator, which runs `alembic upgrade head` as the IAM-authenticated
`lambda_migrator` role. The migrator always signals CloudFormation
(SUCCESS/FAILED), so a failed migration fails the deploy instead of hanging it,
and `Delete` is a no-op success (schema is data-bearing).

**2. The custom-resource response reaches S3 via a gateway endpoint.** An S3
gateway VPC endpoint (no hourly cost) plus a scoped 443 egress rule let the
in-VPC migrator POST to the CloudFormation-presigned S3 URL without NAT.

**3. Re-run is fingerprint-driven.** `make deploy` passes `MigrationsFingerprint`
(a content hash of `migrations/versions`). CloudFormation re-invokes the migrator
only when that property changes, so no-op deploys don't touch the DB. An
`AutoMigrate` toggle (default `true`) can disable the custom resource for a new
environment's very first deploy.

**4. The privileged bootstrap is one-time, via `make db-bootstrap` (no bastion).**
It reads the RDS-managed master secret locally and invokes the migrator in
*bootstrap* mode, passing the master credentials in the one-time invocation
payload. The migrator connects as the master and applies `0001`-`0003`. The
master credential never appears in a committed file and the migrator needs no
Secrets Manager access from inside the VPC. Bootstrap also **idempotently
re-grants `rds_iam`** to the login roles as a self-healing invariant, so an
environment whose role lost (or never had) its IAM enrollment is repaired even
when Alembic is already past the bootstrap boundary — otherwise the routine
migrator connection fails with "password authentication failed". (Considered and rejected: running the
full bootstrap on every deploy — keeps master on the hot path; a standing Secrets
Manager interface endpoint — ~$8/month for a once-per-environment need.)

**5. Every upgrade is serialized by a Postgres advisory lock.** The migrator
takes a session-level `pg_advisory_lock` on a dedicated connection for the whole
upgrade, so two overlapping runs (a retried deploy racing a manual invoke) can
never apply DDL concurrently.

**6. Schema changes follow expand/contract.** Because migrations now run as part
of deploy — potentially alongside the code that uses the schema — every change
must be backward compatible with the currently-running code:

- **Expand:** add columns/tables/indexes as nullable or with defaults; never
  rename or drop in the same deploy that ships code depending on the new shape.
- **Migrate/backfill:** deploy the code that writes both shapes; backfill data.
- **Contract:** only in a *later* deploy, once no running code references the old
  shape, drop/rename it.

A destructive change and the code that stops using the old shape must land in
separate deploys. Migrations keep working `downgrade()` paths for forward-fix or
rollback.

## Consequences

- (+) `make deploy` applies pending routine migrations with no manual step;
  bootstrap is a single documented one-time command per environment.
- (+) The recurring deploy path stays least-privilege (IAM `lambda_migrator`
  only); the master is used exactly once per environment.
- (+) No new standing cost (gateway endpoint is free; no interface endpoint).
- (−) The master password transits a one-time Lambda invocation payload during
  bootstrap (over TLS, not logged). Accepted for a rare, operator-initiated op.
- (−) Expand/contract is a discipline, not enforced by tooling — reviewers must
  check that a migration is compatible with the code deploying alongside it.
- (−) A 443 egress rule on the Lambda SG uses a wide CIDR; it is safe only
  because the private route table has no default route (traffic can reach only
  the gateway-endpoint-backed services).
- (−) Introducing the custom resource must be a **two-phase deploy** on an
  existing environment (see below), or a rollback of the introducing deploy can
  hang.

## Introducing the custom resource to an existing environment

Deploy in two phases, never in one shot:

1. `make deploy STAGE=<env> AUTO_MIGRATE=false` — lands the Delete-capable
   migrator code and the S3 endpoint without creating the custom resource. Then
   `make db-bootstrap STAGE=<env>` to ensure the roles are IAM-enrolled.
2. `make deploy STAGE=<env>` — adds the custom resource (auto-migrate on).

Rationale: a Lambda-backed custom resource must answer its `Delete` request or
CloudFormation waits out the resource timeout (~1h) and retries indefinitely. If
the resource's *first* deploy both introduces the resource and updates the
function's code, and that deploy fails and rolls back, CloudFormation reverts the
function to the prior code — which has no `Delete` handler — and the rollback
hangs. Phase 1 makes the Delete-capable code the rollback target before the
resource exists, so any later rollback reverts to code that can signal. Once the
custom resource is part of the environment's baseline, ordinary deploys need no
special handling.

## Notes

Refines ADR-0024 item 2 (migrations run themselves): the custom resource runs the
**routine** upgrade only; the privileged bootstrap is a separate one-time
`make db-bootstrap` rather than part of every deploy, and the long-run Step
Functions signal is deferred (migrations are small and fast). Builds on ADR-0006
(no NAT), ADR-0008 (IAM DB auth), and the two-phase role model in migrations
0002/0003.
