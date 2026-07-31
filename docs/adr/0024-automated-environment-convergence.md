# ADR-0024: Automated, idempotent environment convergence (one-command deploy)

## Status

Accepted (implementation phased in `.kiro/specs/deploy-convergence/`)

## Context

Bringing an environment up was a manual dependency chain a human had to walk:

```
sam deploy → (bastion up → set dba secret → bootstrap roles) → migrate
          → backfill catalog → seed tracked set → sync icons → bastion down
```

plus full-state parameter juggling on every `sam deploy` (each invocation must
re-declare the custom domain, demo key, and bastion toggle or they revert to
template defaults). The result: many ordered manual steps, and fragmentation
around the bastion (up/do-one-thing/down) and per-deploy flags. It is easy to
get wrong and slow to reproduce.

The steps are not really independent — they are couplings that were left for a
person to execute: role bootstrap needed a dba secret that only existed while
the bastion was up; migrations needed the roles; seeds needed migrations + the
catalog. The fix is to let the deploy **own** that chain and run it
automatically and idempotently.

## Decision

Make **`make deploy STAGE=<env>` converge an environment from nothing to
running**, idempotently, with no human checklist. Concretely:

1. **Config is data.** Custom domains, the shared Route 53 hosted zone id, and
   toggles (e.g. demo key) live in **SSM Parameter Store** and are resolved by
   CloudFormation via `AWS::SSM::Parameter::Value<String>` parameter types.
   `samconfig.toml` (committed) carries only the SSM key *paths*, not values, so
   a full-state deploy can never drop the domain and no account-specific host is
   committed. One source of truth for local and CI. **Supersedes** the
   gitignored `deploy.<stage>.env` mechanism.
2. **Migrations run themselves.** The in-VPC migrator Lambda runs
   `alembic upgrade head` and the privileged role bootstrap, invoked
   automatically by a CloudFormation **custom resource** on every stack update
   (a long run is delegated to a Step Functions execution the custom resource
   waits on). The migrator uses the **RDS-managed master secret** for the
   one-time role bootstrap, then IAM auth. This removes the human DB tunnel from
   the deploy path.
3. **No standing bastion.** There is no bastion stack and no `EnableBastion`
   parameter. Ad-hoc SQL goes through an IAM-gated **admin-query Lambda**;
   genuine break-glass provisions **ephemeral** access on demand (temporary
   EICE/SSM), never a standing host. **Supersedes ADR-0009** (EICE bastion) and
   **ADR-0020** (dba secret gated on bastion).
4. **Data bootstrap is one idempotent orchestrator.** A **`bootstrap` Step
   Functions** runs catalog sync → tracked-set seed → icon sync (all upserts).
   It auto-runs **once on first create** (guarded by a "catalog empty?" check)
   and is re-runnable on demand via `make bootstrap`; it does not re-backfill on
   routine deploys.
5. **Deploy verifies itself.** The converge ends with a smoke test
   (`make verify`): health, `/v1/items` count > 0, one `/v1/market` sample — so
   "deploy succeeded" means "actually serving."

`make deploy` becomes a thin wrapper: `build → sam deploy → (custom resources
run migrate + first-create bootstrap) → verify`. The only separate, rare
commands are `make seed-config` (one-time SSM seed, incl. the zone-id lookup),
`make bootstrap` (force re-seed), and `make verify`.

## Consequences

- (+) One command brings up dev or prod identically and reproducibly; no ordered
  manual checklist; the bastion up/down fragmentation and per-deploy flag
  juggling are gone.
- (+) The DB is reached only by the app and the migrator/admin Lambdas — smaller
  attack surface, no standing bastion cost.
- (+) Config has a single source of truth (SSM) for local and CI.
- (−) More infrastructure: custom resources, a bootstrap state machine, and an
  admin Lambda. Custom resources must always signal CloudFormation or the stack
  hangs (mitigated by always responding + logging).
- (−) A failed migration now **fails the deploy** (desirable, but needs a
  rollback story: Alembic downgrade or forward-fix; long migrations use the
  async Step Functions signal to avoid the custom-resource timeout).
- (−) The master secret is used for the one-time role bootstrap — scope its IAM
  to exactly that.
- (−) SSM string values can't be empty and the parameter must exist, so the
  "no custom domain" case uses a sentinel or is scoped to prod (see the spec).

## Notes

Phased plan: `.kiro/specs/deploy-convergence/`. Supersedes ADR-0009 and
ADR-0020; drops the gitignored `deploy.<stage>.env` approach. Builds on
ADR-0013 (API domain), ADR-0023 (icons CDN), ADR-0010 (DynamoDB item registry),
and the ETL/migrations already in the v3 design.
