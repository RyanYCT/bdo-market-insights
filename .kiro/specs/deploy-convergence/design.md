# Deploy convergence — Design

Design for the automated, idempotent bring-up in ADR-0024. No code yet; this
fixes the shape and sequence.

## Convergence DAG

```
make seed-config STAGE=<env>         (one-time / on change)
   └─ writes SSM: /bdo/<env>/api-domain-name, icon-domain-name, demo-key,
                  and /bdo/shared/route53/hosted-zone-id (looked up once)

make deploy STAGE=<env>
   1. sam build (+ verify-layer guard)
   2. sam deploy                      # CloudFormation orders the nested stacks;
      │                               # domain/zone/toggles resolved from SSM
      ├─ MigrateCustomResource ─────▶ migrator Lambda / Step Functions
      │        alembic upgrade head + role bootstrap (master secret → IAM)
      └─ BootstrapCustomResource ───▶ bootstrap Step Functions (first create only)
               catalog sync → tracked-set seed → icon sync   (idempotent)
   3. make verify                     # health / items>0 / market sample
```

Everything in step 2 is inside one `sam deploy`. Rare, explicit commands:
`make seed-config`, `make bootstrap` (force re-seed), `make verify`,
`make db-admin SQL='…'` (admin-query Lambda).

## 1. Config in SSM (P1)

Parameter layout:

```
/bdo/shared/route53/hosted-zone-id        # shared infra, looked up once
/bdo/<env>/api-domain-name                # '' sentinel handling below
/bdo/<env>/icon-domain-name
/bdo/<env>/enable-demo-key                # "true" / "false"
```

- Template parameters become `Type: AWS::SSM::Parameter::Value<String>` with the
  key path supplied per env in `samconfig.toml` (paths are not secrets).
  CloudFormation resolves the values at deploy.
- **Empty/opt-in:** SSM string values can't be empty and the param must exist.
  Use the sentinel `none` for "no custom domain"; the existing
  `HasCustomDomain`/`HasIconDomain` conditions test `!Equals [<value>, 'none']`.
  (Alternative considered: scope custom domains to prod only — rejected, keeps
  dev/prod parity.)
- `make seed-config` writes these with `aws ssm put-parameter --overwrite` and,
  for the zone id, resolves it via `aws route53 list-hosted-zones-by-name` then
  stores it (so it is looked up once, not on every deploy).

## 2. Bastion removal + admin access (P1)

- Delete the bastion stack and the `EnableBastion` parameter/condition
  everywhere they thread through `template.yaml` and the child stacks.
- **admin-query Lambda** (in-VPC, IAM auth): invoked with `{ "sql": "…" }`,
  returns rows/rowcount, read-only by default with an explicit write flag.
  Surfaced as `make db-admin STAGE=<env> SQL='select …'`. Covers routine "poke
  the DB" needs without a tunnel.
- **Break-glass** (rare): documented procedure to stand up ephemeral EICE/SSM
  access on demand and tear it down — not a committed standing resource.

## 3. Auto-migrations (P2)

- **Role bootstrap re-sequenced:** the migrator connects with the RDS-managed
  master secret (Secrets Manager) to create the app/migrator roles (today's
  `0002`/`0003`), removing the dba-secret-gated-on-bastion coupling; all later
  steps use IAM auth. Migrator IAM: `secretsmanager:GetSecretValue` on the
  master secret only, plus `rds-db:connect`.
- **MigrateCustomResource:** a Lambda-backed custom resource in the Data (or a
  small Migrate) stack invokes the migrator on create/update. For a bounded run
  it invokes synchronously; for a long run it starts a **migrate Step Functions**
  and the custom resource waits on completion (avoids the CR timeout).
- **Failure = deploy failure.** Rollback story: forward-fix or `alembic
  downgrade` via `make db-admin`/migrator; documented in the spec, not automated.

## 4. Bootstrap orchestrator (P3)

- **bootstrap Step Functions:** `catalogSync → seedTracked → iconSync`, reusing
  the existing functions/scripts as tasks; each is an idempotent upsert.
- **First-create guard:** a custom resource (or the state machine's first task)
  checks "is the catalog empty?" — runs the full sequence on first bring-up,
  and on later deploys skips the heavy catalog backfill (tracked/icon steps stay
  cheap/idempotent or are also guarded).
- **`make bootstrap STAGE=<env>`** starts the state machine on demand for a
  deliberate re-seed / catalog refresh.

## 5. Thin deploy wrapper + verify (P4)

- `make deploy` = `build → sam deploy → verify`. Migrate + first-create bootstrap
  happen inside `sam deploy` via the custom resources, so the wrapper stays thin.
- **`make verify`:** hits the deployed API (health, `/v1/items` count, one
  `/v1/market/items/<id>/analysis`) using the stage's API key; non-zero exit on
  failure so the deploy reports real health.

## Stack shape after this

- Removed: bastion stack + `EnableBastion` wiring.
- Added: admin-query Lambda; migrate custom resource (+ optional migrate SFN);
  bootstrap SFN + first-create guard.
- Changed: domain/zone/demo-key parameters become SSM-resolved; `samconfig.toml`
  holds SSM key paths; `Makefile` gains `seed-config`, `bootstrap`, `verify`,
  `db-admin`, and a thinner `deploy`.

## Risks / mitigations

- **CR hangs** → always signal CloudFormation (success/fail) and log; never let
  an exception escape without a response.
- **Long migration timeout** → async migrate SFN the CR waits on.
- **Accidental catalog re-backfill** → the catalog-empty guard + keeping heavy
  re-seeds behind `make bootstrap`.
- **SSM empty-value limitation** → `none` sentinel + condition update.
- **Master-secret blast radius** → migrator IAM scoped to that one secret.

## Rollout / sequencing

Phased so nothing ships half-built — see tasks.md (P1 config+bastion, P2
auto-migrate, P3 bootstrap, P4 wrapper+verify). Each phase is independently
deployable and leaves the environment working.
