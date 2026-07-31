# Deploy convergence — Implementation Tasks

Phased (ADR-0024). Each phase is independently deployable and leaves the
environment working; nothing ships half-built. Design only until scheduled.

## Phase 1 — Config in SSM + remove the bastion

- [ ] SSM parameter layout (`/bdo/shared/route53/hosted-zone-id`,
      `/bdo/<env>/{api-domain-name,icon-domain-name,enable-demo-key}`)
- [ ] Convert `HostedZoneId` / `ApiDomainName` / `IconDomainName` / demo-key
      template params to `AWS::SSM::Parameter::Value<String>`; `samconfig.toml`
      supplies the key paths per env; `none` sentinel + condition updates
- [ ] `make seed-config STAGE=<env>` (idempotent `put-parameter`; zone-id lookup
      via `list-hosted-zones-by-name`)
- [ ] Remove the bastion stack + `EnableBastion` parameter/condition everywhere
- [ ] admin-query Lambda (in-VPC, IAM auth) + `make db-admin SQL=…`
- [ ] Break-glass (ephemeral access) documented; no standing resource
- [ ] `cfn-lint` / `sam validate --lint` green

## Phase 2 — Auto-migrations

- [ ] Re-sequence role bootstrap to use the RDS master secret (drop the
      dba-secret-gated-on-bastion path); migrator IAM scoped to that secret
- [ ] MigrateCustomResource invokes the migrator on create/update; long runs via
      a migrate Step Functions the CR waits on
- [ ] Deploy fails on migration failure; rollback (forward-fix / downgrade)
      documented
- [ ] Tests for the migrator invocation path

## Phase 3 — Bootstrap orchestrator

- [ ] `bootstrap` Step Functions: catalogSync → seedTracked → iconSync (reuse
      existing functions/scripts as tasks)
- [ ] First-create guard (catalog-empty check) auto-runs it once; skip on
      routine deploys
- [ ] `make bootstrap STAGE=<env>` starts it on demand
- [ ] Tests for the guard + orchestration

## Phase 4 — Thin deploy wrapper + verify

- [ ] `make verify STAGE=<env>` smoke test (health / items>0 / market sample)
- [ ] `make deploy` = `build → sam deploy → verify` (migrate + first-create
      bootstrap happen inside sam deploy)
- [ ] Runbook rewrite around the one-command flow + the rare explicit commands

## Superseded / removed on completion

- ADR-0009 (EICE bastion), ADR-0020 (dba secret gated on bastion) — no standing
  bastion.
- The gitignored `deploy.<stage>.env` config approach — replaced by SSM.
