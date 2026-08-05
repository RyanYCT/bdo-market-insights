# ADR-0027: No standing bastion; on-demand break-glass access

## Status

Accepted (implements ADR-0024 item 3; supersedes ADR-0009 and ADR-0020)

## Context

The EICE + t4g.nano bastion (ADR-0009) existed so a human could reach the
private RDS instance (no public IP, no NAT) for two things: routine inspection /
data fixes via pgAdmin as the `dba` role (secret per ADR-0020), and rare
DDL / bulk / master-level work. It was a *standing* path — an always-present
host and SG surface — even though it was used rarely, and it anchored a chain of
manual steps (bring bastion up → sync dba secret → tunnel → …).

Two changes removed the need for a standing host:
- Routine ad-hoc SQL now goes through the **admin-query Lambda** (ADR-0026):
  in-VPC, IAM-authenticated, read-only by default.
- The one-time role bootstrap runs through the **migrator Lambda** via the
  RDS-managed master secret (ADR-0025), not a human tunnel.

What remains is genuinely rare break-glass: DDL outside migrations, bulk
backfills, or master-level intervention.

## Decision

Remove the standing bastion; provide break-glass **on demand**, never standing.

- **No bastion in the deployed stack.** The `EnableBastion` parameter, the
  `BastionStack`, and the `dba` Secrets Manager secret are gone. The root
  template no longer references any bastion resource.
- **On-demand break-glass stack.** `infra/break-glass.yaml` (the former
  bastion template — t4g.nano + EICE + instance role) is **not** wired into the
  root template. It is deployed only when needed with `make break-glass-up`
  (which then opens an IAM-authenticated SSH tunnel to RDS) and destroyed with
  `make break-glass-down`. Nothing is left running.
- **Break-glass authenticates as the RDS master** (from the RDS-managed master
  secret) over the ephemeral tunnel — there is no `dba` role/secret to maintain.
- **The break-glass security group is retained** in the network stack. A
  security group has no cost and is inert while no instance uses it; keeping it
  (and its RDS ingress rule) avoids mutating the RDS SG from an on-demand stack.

Routine reads/fixes: `make db-admin` (ADR-0026). Schema changes: migrations
(ADR-0025). Break-glass: `make break-glass-up` / `make break-glass-down`.

## Consequences

- (+) No standing bastion host or dba secret — smaller attack surface and one
  fewer always-present component; the up/sync/tunnel/down chain is gone from
  routine ops.
- (+) Break-glass is explicit and self-cleaning: an operator provisions it,
  uses it, and tears it down; forgetting to tear down is visible as a running
  stack rather than a silently-standing host.
- (+) Dev synthetic-data seeding (`scripts/seed_market_dev.py`) still works over
  the on-demand tunnel — no separate path needed.
- (−) Break-glass now costs a few minutes of setup/teardown (deploy + EICE
  readiness) versus a flag flip. Acceptable given how rarely it is needed.
- (−) An inert break-glass SG remains in the network stack (free, no instance).
- (−) An existing `dba` Postgres role (if a past bootstrap created one) is left
  in place but inert — nothing can reach it without a tunnel. Dropping it is an
  optional manual cleanup (needs master privileges).

## Notes

Supersedes ADR-0009 (EICE bastion) and ADR-0020 (dba secret gated on bastion).
Builds on ADR-0006 (no NAT), ADR-0026 (admin-query Lambda), and ADR-0025
(migrations own schema changes; master-secret bootstrap).
