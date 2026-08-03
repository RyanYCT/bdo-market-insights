# Deploy convergence — Implementation Tasks

Phased (ADR-0024). Each phase is independently deployable and leaves the
environment working; nothing ships half-built. Design only until scheduled.

> Implemented as re-sequenced slices: ① SSM config, ② auto-migrations,
> ③ admin-query Lambda, ④ remove bastion, ⑤ bootstrap orchestrator,
> ⑥ verify + thin deploy. SSM names are repo-scoped
> (`/bdo-market-insights/<env>/<category>/<key>`), not the `/bdo/…` sketch below.

## Phase 1 — Config in SSM + remove the bastion

- [x] SSM parameter layout (repo-scoped `/bdo-market-insights/<env>/<category>/<key>`)
- [x] Convert `HostedZoneId` / `ApiDomainName` / `IconDomainName` / demo-key
      template params to `AWS::SSM::Parameter::Value<String>`; `samconfig.toml`
      supplies the key paths per env; `none` sentinel + condition updates
- [x] `make seed-config STAGE=<env>` (idempotent `put-parameter`; zone-id lookup
      via `list-hosted-zones-by-name`)
- [x] Remove the bastion stack + `EnableBastion` parameter/condition everywhere
      (retained as an on-demand break-glass stack — ADR-0027)
- [x] admin-query Lambda (in-VPC, IAM auth) + `make db-admin SQL=…` (ADR-0026)
- [x] Break-glass (ephemeral access) documented; no standing resource (ADR-0027)
- [x] `cfn-lint` / `sam validate --lint` green

## Phase 2 — Auto-migrations

- [x] Re-sequence role bootstrap to use the RDS master secret (drop the
      dba-secret-gated-on-bastion path); `make db-bootstrap` (ADR-0025)
- [x] Auto-migrate custom resource invokes the migrator on create/update
      (long-run Step Functions deferred — migrations are small/fast)
- [x] Deploy fails on migration failure; rollback (forward-fix / downgrade)
      documented (ADR-0025)
- [x] Tests for the migrator invocation path

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
