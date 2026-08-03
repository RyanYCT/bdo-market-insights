# ADR-0026: Admin-query Lambda for ad-hoc DB access (read-only by default)

## Status

Accepted (implements deploy-convergence; enables ADR-0024 item 3)

## Context

Ad-hoc database access — inspecting rows, checking a count, occasionally fixing a
bad row — went through pgAdmin over the EICE bastion tunnel (ADR-0009), as the
`dba` role backed by a bastion-gated secret (ADR-0020). That standing path is the
main thing keeping the bastion alive. ADR-0024 decided to remove the standing
bastion; something has to replace routine human DB access first, or removing the
bastion would leave no way to look at the data.

The database is in the no-NAT VPC (ADR-0006) and only reachable from inside it.
The runtime `lambda_rds_user` role already authenticates via IAM (ADR-0008) and
holds DML but no DDL/ownership.

## Decision

Add an in-VPC **admin-query Lambda** (`bdo-<stage>-admin-query`) for ad-hoc SQL,
replacing pgAdmin-over-bastion for routine work.

- **IAM auth as `lambda_rds_user`.** Reuses `LambdaRdsAuthRole` and
  `bdo_common.db` — no new role, no secret, no NAT.
- **Read-only by default, enforced by Postgres.** Each statement runs inside a
  `READ ONLY` transaction (`conn.read_only = True`), so a write is rejected by
  the database, not merely by application code, and the transaction is rolled
  back.
- **Explicit `{"write": true}` opt-in** runs the statement in a normal
  committing transaction. Because `lambda_rds_user` holds only DML, write mode is
  limited to data changes — **schema changes stay in migrations** (ADR-0025), and
  DDL / role administration is not reachable here.
- **Invoke-only.** No API Gateway route; access is gated by IAM
  (`lambda:InvokeFunction`). Driven by `make db-admin STAGE=<env> SQL='…'`
  (add `WRITE=1` for DML) via `scripts/db_admin.py`.
- **Bounded results.** Rows are capped (default 200, max 1000) with a
  `truncated` flag; values are reduced to JSON-safe primitives; the SQL text is
  logged to CloudWatch as an audit trail.
- No SQL allow-list: the `READ ONLY` transaction plus the role's privileges are
  the guardrails.

## Consequences

- (+) Routine DB inspection and data fixes without a standing bastion — the
  prerequisite for removing it.
- (+) Writes are impossible by default (DB-enforced), and even in write mode the
  blast radius is DML on existing tables — no schema or role changes.
- (+) Every access is IAM-authenticated and logged; no shared `dba` password.
- (−) Less power than pgAdmin-as-`dba`: no DDL, no interactive session, results
  are capped. DDL goes through migrations; genuine break-glass (DDL / roles /
  bulk work) is provisioned ephemerally on demand, never as a standing resource
  (follow-up: bastion removal).
- (−) One statement per invocation (the parameterised protocol runs a single
  command); no multi-statement scripts.

## Notes

Implements ADR-0024 item 3. Works with ADR-0006 (no NAT), ADR-0008 (IAM DB
auth), and ADR-0025 (migrations own schema changes). Supersession of ADR-0009
(bastion) and ADR-0020 (dba secret) lands with the bastion removal that this
Lambda unblocks.
