# Deploy convergence — Requirements

## Product

Turn environment bring-up from a manual dependency chain into a single
idempotent command. `make deploy STAGE=<env>` takes an environment from nothing
to serving, and re-running it safely converges to the same state. Decision and
rationale: **ADR-0024**.

## Functional Requirements

### One-command convergence
- **FR-1** `make deploy STAGE=<env>` SHALL build, deploy all stacks, run
  migrations, run first-create data bootstrap, and verify — with no manual
  intervening steps.
- **FR-2** Re-running `make deploy` on an already-provisioned environment SHALL
  be a safe converge (no destructive or duplicate effects).

### Config as data
- **FR-3** Custom domains (`API_DOMAIN_NAME`, `ICON_DOMAIN_NAME`), the shared
  Route 53 hosted zone id, and toggles (e.g. demo key) SHALL be sourced from SSM
  Parameter Store and resolved by CloudFormation (`AWS::SSM::Parameter::Value`).
- **FR-4** A full-state deploy SHALL NOT drop these values (no reliance on
  passing flags each time). `samconfig.toml` carries only SSM key paths, never
  account-specific hosts.
- **FR-5** `make seed-config STAGE=<env>` SHALL write the env's config to SSM
  (idempotent), including looking up and storing the hosted zone id.

### Migrations
- **FR-6** Schema migrations (`alembic upgrade head`) and the privileged role
  bootstrap SHALL run via the in-VPC migrator Lambda, invoked automatically by a
  CloudFormation custom resource on stack create/update.
- **FR-7** The role bootstrap SHALL use the RDS-managed master secret (no
  bastion, no human step); subsequent connections use IAM auth.
- **FR-8** A failed migration SHALL fail the deploy (surfaced, not silent).
- **FR-9** Long migrations SHALL run as a Step Functions execution the custom
  resource waits on, to avoid the custom-resource timeout.

### No standing bastion
- **FR-10** There SHALL be no bastion stack and no `EnableBastion` parameter.
- **FR-11** Ad-hoc SQL SHALL be available via an IAM-gated admin-query Lambda
  (invoke with a statement; returns rows/rowcount).
- **FR-12** True break-glass access SHALL be provisioned ephemerally on demand
  and documented; never a standing host.

### Data bootstrap
- **FR-13** A `bootstrap` Step Functions SHALL run catalog sync → tracked-set
  seed → icon sync, each idempotent (upserts).
- **FR-14** Bootstrap SHALL auto-run once on first create (guarded by a
  catalog-empty check) and be re-runnable via `make bootstrap STAGE=<env>`; it
  SHALL NOT re-backfill the full catalog on routine deploys.

### Verification
- **FR-15** `make verify STAGE=<env>` SHALL smoke-test the environment (health,
  `/v1/items` count > 0, one `/v1/market` sample) and run at the end of deploy.

## Non-Functional Requirements

- **NFR-1 (idempotency)** Every automated step (migrate, seed, config) is safe to
  re-apply; the deploy is a converge, not a one-shot script.
- **NFR-2 (parity)** dev and prod bring up with the same command and mechanism,
  differing only by SSM config namespace / `samconfig` env.
- **NFR-3 (no-CI dependency)** The full flow works from a workstation with the
  deploy role; CI (later) reuses the same `make deploy` and the same SSM config
  (no separate mechanism).
- **NFR-4 (least privilege)** The master secret is used only for the one-time
  role bootstrap; custom resources always signal CloudFormation.
- **NFR-5 (cost)** No standing bastion; no always-on components added.

## Out of scope

- Moving to Aurora Serverless v2 + RDS Data API (would remove VPC DB
  connectivity entirely) — noted as a future option, not this effort.
- CI/CD pipeline itself (the design must not depend on it; wiring CI is separate).
