# Runbook

Operational guide for the live v3 stacks (`bdo-market-dev` / `bdo-market-prod`):
first-time bring-up, daily operations, deployment, feature toggles, database
access, insights evaluation, recovery/teardown, and troubleshooting.

## Contents

- [Decision flow](#decision-flow)
- [First-time bring-up](#first-time-bring-up)
- [Daily operations](#daily-operations)
- [Deployment](#deployment)
  - [Quick reference](#quick-reference)
  - [Deployment notes](#deployment-notes)
  - [Dev deployment (manual)](#dev-deployment-manual)
  - [Running migrations](#running-migrations)
  - [Prod deployment (CI/CD)](#prod-deployment-cicd)
  - [Rollback](#rollback)
  - [Breaking changes](#breaking-changes)
- [Feature toggles](#feature-toggles)
  - [Custom API domain](#custom-api-domain)
  - [Public demo API key](#public-demo-api-key)
- [Database access](#database-access)
- [Market Insights: dev evaluation](#market-insights-dev-evaluation)
- [Recovery & teardown](#recovery--teardown)
  - [Cleanup and teardown](#cleanup-and-teardown)
  - [Recreating a stack from scratch](#recreating-a-stack-from-scratch)
- [Troubleshooting](#troubleshooting)

## Decision flow

Start here: pick what you are doing and follow the arrows to the section that
covers it. Every box names a section in this runbook (see [Contents](#contents)).

```mermaid
flowchart TD
    q(["What do you need to do?"])

    q --> shipping{"Ship a change?"}
    q --> standup["Stand up a fresh stack<br/>→ First-time bring-up"]
    q --> reset{"Remove or reset a stack?"}
    q --> monitor["Check ETL / API health<br/>→ Daily operations"]
    q --> dbwork["Connect to Postgres (ad-hoc / recovery)<br/>→ Database access"]
    q --> feature["Toggle custom domain / demo key<br/>→ Feature toggles"]
    q --> reviewi["Review insights on dev<br/>→ Market Insights: dev evaluation"]
    q --> broken["Something is broken<br/>→ Troubleshooting"]

    shipping -->|"prod"| pr["Prod deployment (CI/CD)<br/>→ Post-deploy verification (prod)"]
    shipping -->|"dev · stack exists"| dv["Dev deployment (manual)<br/>+ migrations if schema changed<br/>→ Post-deploy verification (dev)"]
    shipping -->|"dev · no stack yet"| standup

    reset -->|"just delete it"| cl["Cleanup and teardown"]
    reset -->|"stuck in ROLLBACK_COMPLETE<br/>or 'already exists' on create"| rc
    reset -->|"rebuild dev clean"| rc

    rc["Recreating a stack from scratch<br/>clear orphans → then First-time bring-up"]
```

## First-time bring-up

Standing up a stack from empty (a new account, or after a full teardown — see
[Recreating a stack from scratch](#recreating-a-stack-from-scratch) for the
orphan-clearing that precedes this). Do these in order; each is a self-contained
subsection below, so the sequence reads top to bottom without jumping out:

1. **Seed deploy config (once):** `make seed-config STAGE=<env>` — writes the
   SSM parameters the deploy resolves (custom domain / demo key / hosted-zone;
   dev defaults to none). See [Deployment notes](#deployment-notes).
2. **Bootstrap the DB roles (once):** introducing the auto-migrate custom
   resource is two-phase, so deploy with it off, create the roles, then deploy
   normally (see [First-time role bootstrap](#first-time-role-bootstrap)):

   ```sh
   make deploy STAGE=<env> AUTO_MIGRATE=false
   make db-bootstrap STAGE=<env>
   ```
3. **Converge + verify:** `make deploy STAGE=<env>`. One command: it applies
   routine migrations (auto-migrate custom resource, ADR-0025), starts the data
   bootstrap on first create (catalog → tracked set → icons, ADR-0028), and ends
   with `make verify` (liveness + RDS path + waits on the async bootstrap to
   populate data, ADR-0029).

That is the whole bring-up — the catalog backfill, tracked-set seed, and icon
materialization run **automatically** inside the bootstrap orchestrator, so
there are no manual seed steps. Re-run the seeding anytime with
`make bootstrap STAGE=<env>` (idempotent); the offline `make seed*` scripts
below remain as local alternatives. The first `make deploy` may wait several
minutes on the initial ~tens-of-thousands-item catalog sync — raise
`VERIFY_WAIT` or pass `VERIFY=false` and run `make verify` separately if you'd
rather not block on it.

For **prod**, also run the one-time
[CI/CD deploy role bootstrap](#cicd-deploy-role-github-oidc-bootstrap) so tagged
releases can deploy.

### First-time role bootstrap

(Run once per environment, no bastion.) The Postgres roles are cluster-level
objects created by migrations `0002`/`0003` and need privileges the
`lambda_migrator` role does not hold:

- `0002_bootstrap_roles` — `lambda_rds_user` (runtime, IAM auth).
- `0003_migrator_role` — `lambda_migrator` (IAM auth) used by the migrator
  Lambda (see [Running migrations](#running-migrations)); also grants it DML on
  `alembic_version`.

`make db-bootstrap` applies `0001`–`0003` as the RDS **master** user by invoking
the migrator Lambda in bootstrap mode — it reads the RDS-managed master secret
locally and passes it in a one-time invocation, so there is no tunnel or bastion
(ADR-0025). Because the migrator Lambda must exist first, and introducing the
auto-migrate custom resource must be two-phase (ADR-0025), deploy with
auto-migrate off, bootstrap, then deploy normally:

```sh
make deploy STAGE=dev AUTO_MIGRATE=false   # creates infra incl. the migrator, no auto-migrate CR
make db-bootstrap STAGE=dev                # applies 0001-0003 as master (roles + schema)
make deploy STAGE=dev                      # auto-migrate CR applies routine migrations (0004+)
```

After this one-time bootstrap, all later schema changes run automatically on
deploy via the auto-migrate custom resource (ADR-0025) — no tunnel required.

> Migrations `0002`/`0003` end with `REVOKE … FROM CURRENT_USER` so the master
> keeps password login; without it the master becomes a transitive `rds_iam`
> member and RDS routes it to PAM auth (`FATAL: PAM authentication failed for
> user "postgres"`). See [ADR-0008](adr/0008-iam-database-authentication.md) for
> the mechanism. If you are ever locked out this way, connect once with an IAM
> token (the master now holds `rds_iam`, so IAM auth works) and run
> `REVOKE lambda_rds_user FROM postgres; REVOKE lambda_migrator FROM postgres;`.

### Backfill the item catalog (one-time)

> The bootstrap orchestrator (ADR-0028) runs the catalog sync automatically on
> a fresh environment's first deploy and on every `make bootstrap`. The manual
> backfill below is the offline/local alternative or for a targeted re-run.

The full BDO item catalog (~tens of thousands of items) is synced from arsha.io
`util/db` by the weekly `catalogSync` Lambda. For the initial load, run the
backfill once (idempotent; safe to re-run). It partial-upserts every item, so it
never clobbers tracked items' ETL-owned fields:

```bash
uv run python scripts/seed_catalog.py --target-table bdo-dev-items --dry-run
uv run python scripts/seed_catalog.py --target-table bdo-dev-items
```

Thereafter the weekly Lambda keeps the catalog current (default Thu 08:00 UTC /
16:00 UTC+8, a buffer after the Thu 03:00-07:00 UTC+8 maintenance window; adjust
via the `CatalogSyncSchedule` parameter). The Lambda stores a content checksum
of the catalog in SSM (`/bdo-market-insights/<stage>/catalog/checksum`) and skips all writes on
weeks where `util/db` is unchanged; when it changes, only new/changed items are
written. The parameter is created automatically on the first run.

You can also invoke the Lambda for the initial load instead of the script, but
the first run writes the whole catalog and takes a few minutes — longer than the
AWS CLI's 60s read timeout. Invoke it **asynchronously** (`--invocation-type
Event`) and read the result from the logs; a synchronous `aws lambda invoke`
would time out on the client while the function keeps running:

```bash
aws lambda invoke --function-name bdo-dev-catalog-sync \
  --invocation-type Event --payload '{}' /tmp/catalog-sync.json   # returns 202; payload is empty
aws logs tail /aws/lambda/bdo-dev-catalog-sync --since 10m --follow
# look for "catalogSync complete" with total / written / new
```

### Seed the tracked set (one-time)

The offline pipeline that decides **which items are tracked**. Only the
occasional snapshot build (`make market-catalog`) touches arsha; selection and
seeding are fully offline:

```mermaid
flowchart LR
    arsha([arsha.io<br/>GetWorldMarketList + util/db grade]) -->|make market-catalog<br/>occasional| snap[(full_items.json<br/>id, name, main, sub, grade)]
    presets[(presets.json)] --> toggle
    sets[(track_sets.json)] --> toggle
    snap --> toggle{{select_tracked.py<br/>preset / main+sub / set}}
    toggle --> tracked[(tracked_items.json<br/>id + name)]
    snap --> seed[seed_items.py]
    cats[(categories.json)] --> seed
    tracked --> seed
    sets -.->|cron_profile by series| seed
    seed -->|tracked + category + cron_profile| ddb[(DynamoDB<br/>items table)]

    subgraph offline [Fully offline, no arsha]
        toggle
        tracked
        seed
    end
```

> The bootstrap orchestrator (ADR-0028) applies the committed tracked set
> automatically (the `seedTracked` Lambda) on first deploy and on
> `make bootstrap`. Use the offline flow below to **change** what's tracked,
> then `make bootstrap STAGE=<env>` (or a redeploy) applies the new set.

The ETL polls only *tracked* items. A **curated default tracked set ships** in
`scripts/data/tracked_items.json` — you can seed it as-is, or adjust what's
tracked first with the toggle below. Everything here is offline; only
`make market-catalog` (regenerating the snapshot) touches arsha.

**1. (Optional) change what's tracked.** Use the preset toggle rather than
hand-editing the list. It **adds** the selection to the current tracked set by
default (pass `--replace` to overwrite instead). Pick several presets at once —
comma-separated numbers in `make track` (e.g. `9,10`), or `--preset a,b` — and
their selections are unioned:

```bash
make track                                   # interactive menu (accepts e.g. 9,10)
# ...or scripted (adds by default; --replace to overwrite, --force for broad sets):
uv run python scripts/select_tracked.py --preset deboreka,buffs --out scripts/data/tracked_items.json
uv run python scripts/select_tracked.py --preset ring --out scripts/data/tracked_items.json
# grade filter: keep only high-value items (grade codes 0 White..3 Gold,4 Orange,5 Violet)
uv run python scripts/select_tracked.py --main 20 --min-grade 3 --out scripts/data/tracked_items.json
```

Presets (`scripts/data/presets.json` + `scripts/data/track_sets.json`): `all`
(guarded), `high-value` (guarded; every item grade ≥ 3), `accessories`, `ring`,
`necklace`, `earring`, `belt`, `pearl`, `functional`, `deboreka`, `buffs`.

**Grade filter.** The snapshot carries each item's BDO `grade` (0 White, 1
Green, 2 Blue, 3 Gold, 4 Orange, 5 Violet), so a selection can be narrowed to a
grade band with `--min-grade` / `--max-grade`. The accessory presets
(`accessories`, `ring`, `necklace`, `earring`, `belt`) and `high-value` default
to grade ≥ 3 (this project targets valuable items); the CLI flags override that
default — pass `--min-grade 0` to re-include every grade. Items whose grade is
unknown in the snapshot are dropped whenever a grade bound applies.

**2. Seed it.** Write the tracked markers to DynamoDB. Category comes from the
snapshot + `categories.json`; `cron_profile` from series membership — no arsha:

```bash
uv run python scripts/seed_items.py --target-table bdo-dev-items --dry-run
uv run python scripts/seed_items.py --target-table bdo-dev-items
# ...or run the catalog backfill + tracked seed together, in the correct order:
make seed-data STAGE=dev
```

It partial-upserts `tracked=true` + the sparse tracked-index marker +
`cron_profile`/`category`/`main_category`/`sub_category`, preserving the
catalog-owned `name`/`grade`/`names` (run after the catalog backfill so names are
present). Because it stamps the marker, no separate tracked-index backfill is
needed for seeded items. Seeding is **additive** by default; add `--reconcile`
(or `RECONCILE=1` with make) to also untrack items no longer in the list. An
item whose `(main:sub)` is not in `categories.json` is still tracked but left
ungrouped — extend the map to classify it.

### Item icons

> The bootstrap orchestrator (ADR-0028) runs the icon sync automatically as its
> last step on a fresh deploy and on `make bootstrap`. The manual invoke below
> is for materializing immediately outside that flow.

Icons are self-hosted in the `bdo-<stage>-icons` bucket and materialized from the
Pearl Abyss CDN by the daily `iconSync` Lambda, which processes tracked items
with `icon_status=unset` (marking each `stored`, or `missing` when the CDN has no
icon). No manual step is required — new tracked items get an icon by the next
daily run, and the job is a no-op once every tracked icon is `stored`/`missing`.
To materialize immediately (e.g. right after registering items), invoke it
**asynchronously** and read the result from the logs:

```bash
aws lambda invoke --function-name bdo-dev-icon-sync \
  --invocation-type Event --payload '{}' /tmp/icon-sync.json   # returns 202; payload is empty
aws logs tail /aws/lambda/bdo-dev-icon-sync --since 5m --follow
# look for "iconSync complete" with stored / missing / errors
```

> The icon materializer fetches from the Pearl Abyss CDN, not arsha, so it is
> unaffected by arsha outages.

### CI/CD deploy role (GitHub OIDC) bootstrap

Prod deploys assume an IAM role via GitHub OIDC (no long-lived keys in CI). The
CI `deploy` job reads the role ARN from the `AWS_DEPLOY_ROLE_ARN` repo secret;
until that role and secret exist, the job fails at `configure-aws-credentials`
with *"Could not load credentials from any providers"*. Set it up once:

```sh
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# 1. GitHub OIDC identity provider (skip if it already exists in the account).
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 1b511abead59c6ce207077c0bf0e0043b1382612   # no longer validated, but the CLI requires a value

# 2. Trust policy: only this repo's v* tags may assume the role
#    (the deploy job runs only on tag pushes).
cat > trust.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike":   {"token.actions.githubusercontent.com:sub": "repo:RyanYCT/bdo-market-insights:ref:refs/tags/v*"}
    }
  }]
}
JSON
aws iam create-role --role-name bdo-github-deploy \
  --assume-role-policy-document file://trust.json

# 3. Permissions. `sam deploy` IS the CloudFormation actor (no separate service
#    role), so this role provisions every resource the stacks manage. Pragmatic
#    baseline: PowerUserAccess (covers CloudFormation, S3, Lambda, API Gateway,
#    Step Functions, EventBridge, RDS, DynamoDB, EC2/VPC, Secrets Manager, KMS,
#    SNS, Logs, CloudWatch, ACM, Route 53) + an inline policy for the IAM writes
#    PowerUser omits, scoped to bdo-* so it can't mint arbitrary privileged roles.
aws iam attach-role-policy --role-name bdo-github-deploy \
  --policy-arn arn:aws:iam::aws:policy/PowerUserAccess

cat > deploy-iam.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageStackRoles",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:TagRole", "iam:UntagRole",
        "iam:AttachRolePolicy", "iam:DetachRolePolicy",
        "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy",
        "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:UpdateAssumeRolePolicy",
        "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile",
        "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile"
      ],
      "Resource": [
        "arn:aws:iam::${ACCOUNT_ID}:role/bdo-*",
        "arn:aws:iam::${ACCOUNT_ID}:instance-profile/bdo-*"
      ]
    },
    {
      "Sid": "PassRoleToServices",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/bdo-*",
      "Condition": {"StringEquals": {"iam:PassedToService": [
        "lambda.amazonaws.com", "states.amazonaws.com",
        "events.amazonaws.com", "ec2.amazonaws.com"
      ]}}
    }
  ]
}
JSON
aws iam put-role-policy --role-name bdo-github-deploy \
  --policy-name bdo-deploy-iam --policy-document file://deploy-iam.json

# 4. Tell GitHub the role ARN (or set it in Settings -> Secrets and variables
#    -> Actions -> New repository secret).
gh secret set AWS_DEPLOY_ROLE_ARN \
  --body "arn:aws:iam::${ACCOUNT_ID}:role/bdo-github-deploy"

# 5. Optional: if prod serves a custom domain, set its repository *secrets*
#    (kept out of git, and masked in the public Actions logs the rendered
#    deploy command would otherwise expose). The CI deploy passes them so every
#    tagged release preserves the domain. Skip if not using a domain; leaving
#    them unset deploys prod with the domain disabled.
gh secret set PROD_API_DOMAIN_NAME --body "api.example.com"
gh secret set PROD_HOSTED_ZONE_ID --body "ZXXXXXXXXXXXXX"
```

> CloudFormation-generated resource roles are prefixed with the stack name
> (`bdo-market-<stage>-…`), so they match the `bdo-*` scope above. If a deploy
> ever hits `AccessDenied` on `iam:CreateRole` for a non-`bdo-*` name, widen that
> one Resource rather than opening IAM up. PowerUser is a pragmatic start for a
> single-account project; the OIDC trust already limits *who* can assume the
> role, and you can tighten to least-privilege later by replaying CloudTrail.

> First prod deploy only: the RDS Postgres roles must be bootstrapped
> (migrations `0001`–`0003` as the master user via `make db-bootstrap` — see
> [First-time role bootstrap](#first-time-role-bootstrap)) before the auto-migrate
> custom resource can run as `lambda_migrator`.

## Daily operations

ETL runs hourly via EventBridge (one execution per active region).
Monitor health from the CloudWatch dashboard:

- **BdoMarket/EtlSuccessfulItems** - items processed without error
- **BdoMarket/EtlFailedItems** - items that failed in the current run

Step Functions console shows full execution history, per-state
input/output, and retry behaviour.

## Deployment

### Quick reference

| Environment | Method |
|-------------|--------|
| Dev | `make deploy STAGE=dev` (manual; CI deploy is tag-only) |
| Prod | Push a `v*` tag to trigger CI deploy |
| Rollback | Deploy the previous tag |

`make deploy` converges the whole environment: `build → sam deploy` (which runs
migrations and starts the first-create data bootstrap) `→ verify`. Other
one-command operations:

- `make verify STAGE=<env>` — post-deploy smoke test (ADR-0029); also run
  automatically at the end of `make deploy` (`VERIFY=false` to skip).
- `make bootstrap STAGE=<env>` — re-run the data seeding (catalog → tracked set
  → icons); idempotent.
- `make db-admin STAGE=<env> SQL='…'` — ad-hoc read-only SQL (ADR-0026).

### Deployment notes

Two things apply to every `make deploy` below:

- **Build on a native Linux filesystem, not a Windows-mounted `/mnt/*` path.**
  `make deploy` runs `make build` (including the verify-layer guard) first, so a
  deploy can never republish a source-only `CommonLayer` — which would otherwise
  break every function at init with `No module named 'aws_lambda_powertools'`.
  On a `/mnt/*` path `pip --target` can silently vendor nothing.
- **Deploy config (domains, hosted zone, demo key) lives in SSM** (ADR-0024).
  Seed it once per stage before the first deploy — `make seed-config STAGE=<env>`
  (see below) — and `make deploy` resolves it automatically; a full-state deploy
  can no longer drop the custom domain by forgetting a flag. The older
  `API_DOMAIN_NAME`/`HOSTED_ZONE_ID` make-vars and the `PROD_*` GitHub secrets
  described later in this doc are superseded and will be removed when the runbook
  is rewritten around the one-command flow.

  ```bash
  # no custom domain (dev):
  make seed-config STAGE=dev
  # with custom domains + zone lookup (prod):
  make seed-config STAGE=prod API_DOMAIN_NAME=api.example.com \
      ICON_DOMAIN_NAME=icons.example.com PARENT_DOMAIN=example.com ENABLE_DEMO_KEY=true
  ```
- **`make deploy` re-declares the full stack state.** Non-SSM parameters not
  passed revert to their template default; `DEPLOY_PARAMS` in the Makefile
  assembles the complete set.

### Dev deployment (manual)

**Use this workflow to test changes on the dev stack before promoting to prod.**
(Setting up a stack from empty? See [First-time bring-up](#first-time-bring-up).)

#### Pre-deploy checklist

- [ ] Code review complete (PR merged to `main`)
- [ ] All CI checks passed (lint, typecheck, tests, audit, scan, OpenAPI drift)
- [ ] Schema changes? Ensure migrations are in `migrations/versions/` with sequential numbers
- [ ] Local `make test` passes (including integration if `TEST_DATABASE_URL` is set)

#### Deploy dev

```bash
# Build and deploy the dev stack (prompts for changeset confirmation).
# See "Deployment notes" above re: the build guard and full-state flags.
# make deploy streams stack events and blocks until the deploy settles.
make deploy STAGE=dev
```

Once it settles on `CREATE_COMPLETE` / `UPDATE_COMPLETE`, verify (below).

#### Apply migrations (if applicable)

If your changes include schema migrations (`migrations/versions/*`), apply them
after the deploy through the in-VPC migrator Lambda — no bastion or tunnel
needed:

```bash
make migrate-lambda STAGE=dev
```

See [Running migrations](#running-migrations) for how this works.

> **First time on a fresh database?** The `lambda_migrator` role does not exist
> yet, so the migrator Lambda cannot run. Do the one-time
> [First-time role bootstrap](#first-time-role-bootstrap) instead — `make
> db-bootstrap` applies `0001`–`0003` (schema + roles) as the master, no bastion.
> Every later migration then runs automatically on deploy (or `make migrate-lambda`).

#### Post-deploy verification (dev)

The one-command check is **`make verify STAGE=<env>`** (ADR-0029) — it runs
automatically at the end of `make deploy` and asserts liveness
(`/v1/openapi.json` → 200), the RDS path (admin-query `select 1`), and that the
items table is populated (waiting on the async bootstrap up to `VERIFY_WAIT`):

```sh
make verify STAGE=dev
```

The manual commands below are a deeper, key-authenticated spot-check (resolve a
private API key and hit `/v1/items` directly) — useful to exercise the key-gated
read path or inspect responses by hand. This block is parametrized on `STAGE`;
the [prod section](#post-deploy-verification-prod) reuses it with `STAGE=prod`.

```bash
STAGE=dev

# Resolve the API base URL from the nested API stack. The root stack
# (bdo-market-${STAGE}) exposes no outputs of its own, so query across all stacks
# whose name starts with the stack prefix and pick the nested API stack's output.
API_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-${STAGE}')].Outputs[] | [?OutputKey=='ApiUrl'].OutputValue | [0]" \
  --output text)

# The API key is an API Gateway key created by the usage plan (NOT Secrets
# Manager). Resolve it via the REST API id so the dev/prod keys in the same
# account are never confused.
API_ID=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-${STAGE}')].Outputs[] | [?OutputKey=='ApiId'].OutputValue | [0]" \
  --output text)
# Exclude the read-only demo plan (if enabled) so this resolves the PRIVATE key.
USAGE_PLAN_ID=$(aws apigateway get-usage-plans \
  --query "items[?apiStages[?apiId=='${API_ID}'] && name!='bdo-${STAGE}-demo-plan'].id | [0]" --output text)
API_KEY_ID=$(aws apigateway get-usage-plan-keys --usage-plan-id "${USAGE_PLAN_ID}" \
  --query 'items[0].id' --output text)
API_KEY=$(aws apigateway get-api-key --api-key "${API_KEY_ID}" --include-value \
  --query 'value' --output text)

# Swagger UI + spec are key-less; use their dedicated output
DOCS_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-${STAGE}')].Outputs[] | [?OutputKey=='DocsUrl'].OutputValue | [0]" \
  --output text)

# Test the key-required API (ApiUrl already includes the stage path)
curl -H "x-api-key: ${API_KEY}" "${API_URL}/v1/items" | head -20
curl -s "${DOCS_URL}" | grep -q "swagger-ui" && echo "Swagger UI OK"

# Check CloudWatch Logs for errors (last 10 minutes)
aws logs tail /aws/lambda/bdo-${STAGE}-market-query --since 10m --follow
```

Optional (dev / fresh-table only): smoke-test the tracked-index Query path. Skip
on prod — this registers a real tracked item.

```bash
# Registering an item validates the id against arsha.io and writes it via
# put_item, which stamps the sparse marker (t="1") because it is tracked -- so
# it must appear in the tracked-index the ETL's retrieveItems now queries.
curl -s -X POST "${API_URL}/v1/items" -H "x-api-key: ${API_KEY}" \
  -H "Content-Type: application/json" -d '{"id": 12094}' | head -20
# Confirm the item is present in the sparse tracked-index (Count >= 1)
aws dynamodb query --table-name bdo-${STAGE}-items --index-name tracked-index \
  --key-condition-expression "t = :t" \
  --expression-attribute-values '{":t": {"S": "1"}}' \
  --query 'Count'
```

### Running migrations

Routine schema migrations run **from inside the VPC** — no bastion or tunnel
needed. The CI deploy job invokes the migrator Lambda (`bdo-<stage>-migrator`)
after `sam deploy`; the function connects to RDS as `lambda_migrator` via IAM
auth and runs `alembic upgrade head`. A GitHub runner cannot reach the private
RDS directly, so it drives the migration through this Lambda (control-plane
invoke). Trigger it by hand for dev:

```sh
make migrate-lambda STAGE=dev
```

(The very first migration on a fresh database is different — the roles don't
exist yet; see [First-time role bootstrap](#first-time-role-bootstrap).)

### Prod deployment (CI/CD)

**Use this workflow to release changes to production. All CI checks run automatically before merge.**

#### Pre-release checklist (before pushing tag)

- [ ] Changes are merged to `main` and all CI checks passed
- [ ] Schema migrations are sequenced correctly and tested on dev
- [ ] No breaking API changes (or clearly communicated if intentional)
- [ ] Updated ADRs or architecture docs if architectural changes were made
- [ ] Reviewed the diff one final time: `git diff main~1 main`

#### Release to prod

```bash
# Create and push a version tag (semver format: v1.2.3)
# This automatically triggers the CI deploy job
git tag v1.2.0
git push origin v1.2.0

# Monitor the deployment in GitHub Actions
# The deploy job runs after all CI checks pass (lint, test, audit, etc.)
# It then invokes the migrator Lambda to run pending migrations

# Watch the workflow (open in a browser):
# https://github.com/RyanYCT/bdo-market-insights/actions

# Or via CLI:
gh run list --workflow ci.yml --branch main --limit 1
gh run view <RUN_ID> --log
```

#### Post-deploy verification (prod)

Once the workflow completes (indicated by a ✓ or ✗ badge), resolve
`API_URL` / `API_KEY` / `DOCS_URL` and run the API + Swagger checks exactly as in
[Post-deploy verification (dev)](#post-deploy-verification-dev) with `STAGE=prod`
(skip the item-registration smoke test). Then run the two prod-only checks: the
custom domain and live ETL history.

```bash
STAGE=prod
# (resolve API_URL / API_KEY / DOCS_URL and test the API per the dev block)

# If the custom domain is enabled (ADR-0013), it should serve too:
CUSTOM_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-${STAGE}')].Outputs[] | [?OutputKey=='CustomApiUrl'].OutputValue | [0]" \
  --output text)
[ -n "$CUSTOM_URL" ] && curl -H "x-api-key: ${API_KEY}" "${CUSTOM_URL}/v1/items?limit=1" | head -20

# Verify recent ETL runs succeeded (state machine ARN is exported by the ETL stack)
ETL_ARN=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-${STAGE}')].Outputs[] | [?OutputKey=='EtlStateMachineArn'].OutputValue | [0]" \
  --output text)
aws stepfunctions list-executions --state-machine-arn "${ETL_ARN}" \
  --query 'executions[:3].[name, status, stopDate]' --output table
```

#### Schema migration verification

After the CI deploy completes, verify migrations ran successfully:

```bash
# Check the migrator Lambda logs
aws logs tail /aws/lambda/bdo-prod-migrator --since 5m --follow

# Or manually invoke the migrator to check status
aws lambda invoke --function-name bdo-prod-migrator \
  --cli-binary-format raw-in-base64-out --payload '{}' /tmp/migrate.json
cat /tmp/migrate.json
```

### Rollback

If the prod deploy introduces a critical issue:

```bash
# Identify the previous stable tag
git tag --list 'v*' | sort -V | tail -5

# Deploy the previous version
git tag v1.1.9  # (example of a previous stable version)
git push origin v1.1.9

# Watch the rollback deploy in Actions
gh run list --workflow ci.yml --branch main --limit 1

# After the rollback completes, run post-deploy verification again
# (see "Post-deploy verification (prod)" section above)
```

**Note:** Rollbacks are safe for data — ETL writes are idempotent on `(region, item_id, sid, snapshot_at)`. If you rolled back past a schema migration, you may need to manually run `REVOKE` commands on your RDS roles; see [First-time role bootstrap](#first-time-role-bootstrap) for details.

### Breaking changes

If you make a breaking change (new required field, schema incompatibility, etc.):

1. **Create a feature branch** and test on dev first
2. **Communicate the change** in the PR and release notes
3. **Deploy with a minor/major version bump** (e.g., `v2.0.0` if breaking)
4. **Update API consumers** before removing old behavior
5. **Consider a canary approach** if possible — deploy to dev/staging first, then prod after a soak period

## Feature toggles

Optional, opt-in capabilities that are off by default. Each is a plain
`make deploy` flag; remember the full-state rule in
[Deployment notes](#deployment-notes).

### Custom API domain

The API custom domain is opt-in and off by default (ADR-0013). The hostname and
hosted zone are **not** stored in committed config (account-specific). They are
supplied two ways:

- **CI (prod):** repository **secrets** `PROD_API_DOMAIN_NAME` /
  `PROD_HOSTED_ZONE_ID` (Settings → Secrets and variables → Actions →
  Secrets — secrets, not variables, so the values are masked in the public
  workflow logs). The tag-gated deploy passes them, so every release keeps the
  domain; if unset, CI deploys with the domain disabled.
- **Manual:** the `make deploy` variables `API_DOMAIN_NAME` / `HOSTED_ZONE_ID`.

Use `{service}.{env}.example.com`: `api.example.com` for prod,
`api.dev.example.com` for dev.

#### Prerequisites

- The parent domain's hosted zone exists in Route 53 (shared infra; **not**
  created by this stack). Get its ID:

  ```sh
  aws route53 list-hosted-zones-by-name --dns-name example.com \
    --query 'HostedZones[0].Id' --output text   # e.g. /hostedzone/ZXXXXXXXXXXXXX
  ```

  Use the bare ID (the part after `/hostedzone/`).
- IAM permissions to create ACM certificates, API Gateway domain names, and
  Route 53 record sets.

#### Enable (prod example)

Set the CI secrets once so every tagged release preserves the domain:

```sh
gh secret set PROD_API_DOMAIN_NAME --body "api.example.com"
gh secret set PROD_HOSTED_ZONE_ID --body "ZXXXXXXXXXXXXX"
```

To apply it now without waiting for a tag, run a full-state deploy (keep the
stage's other persistent flags, e.g. the demo key — see
[Deployment notes](#deployment-notes)):

```sh
make deploy STAGE=prod ENABLE_DEMO_KEY=true \
  API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
```

> The first deploy that sets a domain blocks for a few minutes while ACM
> validates the certificate via the DNS record CloudFormation writes into the
> zone. This is expected; do not cancel the deploy. Subsequent deploys are fast.

Verify, then point clients at the new base URL (the `execute-api` URL keeps
working too):

```sh
aws cloudformation describe-stacks --stack-name bdo-market-prod \
  --query "Stacks[0].Outputs[?ends_with(OutputKey,'CustomApiUrl')].OutputValue | [0]" \
  --output text
curl -H "x-api-key: <KEY>" https://api.example.com/v1/items
```

#### Disable

Unset the CI secrets (so releases stop re-adding it), then redeploy the full
state without the domain vars — the domain reverts to empty, removing the cert,
domain, base-path mapping, and DNS record:

```sh
gh secret delete PROD_API_DOMAIN_NAME
gh secret delete PROD_HOSTED_ZONE_ID
make deploy STAGE=prod ENABLE_DEMO_KEY=true   # no domain vars -> domain removed
```

### Public demo API key

A public, **read-only** API key for "try the API" links (e.g. a published
Postman workspace). Opt-in and off by default. It runs on a tight usage plan
(2 req/s sustained, 5 burst, 500/day) and is read-only: write requests to
`/v1/items` (`POST`/`PATCH`/`DELETE`) return `403`, enforced in the
`itemRegistry` handler (API Gateway keys can't be scoped to specific methods).
Never publish the privileged stage key — only this demo key.

#### Enable

Add `ENABLE_DEMO_KEY=true` to a full-state deploy (see
[Deployment notes](#deployment-notes)). For **prod** the demo key should persist
across releases — the tag-gated CI deploy sets `EnableDemoKey=true`, so once this
is on `main` every release keeps it live.

Apply it immediately with a full-state deploy. Prod (include the domain so it is
not dropped):

```sh
make deploy STAGE=prod ENABLE_DEMO_KEY=true \
  API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
```

Dev (no custom domain), for testing:

```sh
make deploy STAGE=dev ENABLE_DEMO_KEY=true
```

> The demo usage plan is ordered after the API stage (`DependsOn: BdoApiStage`),
> so the "API Stage not found" race on a fresh-create deploy is handled.
> Enabling it on an existing stack (the usual prod case) is a plain update.

#### Retrieve the key value

The value is generated by API Gateway and is **never** stored in the repo.
Fetch it by the key name (`bdo-<stage>-demo`):

```sh
aws apigateway get-api-keys --name-query "bdo-prod-demo" --include-values \
  --query 'items[0].value' --output text
```

Put the value into the published Postman environment's `apiKey` variable (and
set `baseUrl` to the stage's base URL). To rotate, disable then re-enable (a
new key is created); the usage-plan caps limit abuse in the meantime.

#### Verify (read-only)

Confirm the demo key can read but not write. Resolve the base URL and the demo
key, then check a read (`200`) and a write (`403`):

```sh
API_ID=$(aws apigateway get-rest-apis --query "items[?name=='bdo-dev-api'].id | [0]" --output text)
BASE="https://${API_ID}.execute-api.us-east-1.amazonaws.com/dev"
KEY=$(aws apigateway get-api-keys --name-query "bdo-dev-demo" --include-values \
  --query 'items[0].value' --output text)

# read -> 200
curl -s -o /dev/null -w "GET  items -> %{http_code}\n" -H "x-api-key: ${KEY}" "${BASE}/v1/items"
# write -> 403 (rejected by itemRegistry before any arsha.io call)
curl -s -o /dev/null -w "POST items -> %{http_code}\n" -X POST -H "x-api-key: ${KEY}" \
  -H 'content-type: application/json' -d '{"id":12094}' "${BASE}/v1/items"
```

Expected: `GET items -> 200` and `POST items -> 403`. Swap `dev` for `prod` in
the names/stage to verify prod. (A fresh stack returns an empty item list on the
read, which is fine — only the status codes matter here.)

#### Disable

Run a full-state deploy with the demo key off (omit `ENABLE_DEMO_KEY`, which
defaults to false) — this removes the demo key, its usage plan, and the
association. Include the domain so it is not dropped:

```sh
make deploy STAGE=prod \
  API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
```

> For prod, the CI deploy sets `EnableDemoKey=true`, so a manual disable is
> reverted by the next tagged release. To disable it permanently, flip
> `EnableDemoKey=true` to `false` in the deploy step of `.github/workflows/ci.yml`.

## Database access

There is **no standing bastion** (ADR-0027). Reach Postgres by escalating scope:

### Routine: the admin-query Lambda (ADR-0026)

Ad-hoc inspection and the occasional row fix go through the in-VPC,
IAM-authenticated `admin-query` Lambda — no tunnel, no host. Read-only by
default (statements run in a Postgres `READ ONLY` transaction); add `WRITE=1`
for a committing DML transaction:

```sh
make db-admin STAGE=dev SQL='select count(*) from item'
make db-admin STAGE=dev SQL="delete from market_snapshot where id = 42" WRITE=1
```

It cannot run DDL (schema changes go through migrations — see
[Running migrations](#running-migrations)).

### Rare: on-demand break-glass (ADR-0027)

For DDL outside migrations, bulk work, or master-level recovery, provision an
**ephemeral** t4g.nano + EICE, tunnel through it as the RDS **master**, and tear
it down afterward. Nothing is left standing.

Prerequisites: AWS CLI v2 with a local `ssh` on `PATH`; IAM for EICE
(`ec2-instance-connect:OpenTunnel`, `…:SendSSHPublicKey`, `ec2:DescribeInstances`,
`ec2:DescribeInstanceConnectEndpoints`) plus `cloudformation:*` on the
`bdo-market-<stage>-break-glass` stack.

```sh
# 1. Stand up the break-glass host and open the tunnel (leave running).
make break-glass-up STAGE=<dev|prod>       # localhost:5432 -> RDS via EICE

# 2. In a second terminal, connect as the RDS master (resolve the master secret).
MASTER_ARN=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-<dev|prod>')].Outputs[] \
           | [?OutputKey=='MasterSecretArn'].OutputValue | [0]" --output text)
aws secretsmanager get-secret-value --secret-id "$MASTER_ARN" \
  --query SecretString --output text        # -> {"username":"postgres","password":...}
# psql "host=localhost port=5432 dbname=bdo user=postgres" (password from above)

# 3. Tear it down when finished (Ctrl-C the tunnel first).
make break-glass-down STAGE=<dev|prod>
```

## Market Insights: dev evaluation

The insights pipeline reads RDS `market_daily` and `insightsCompute` targets
**yesterday**, with `top_movers` needing a prior day (and ~7–14 days for
volatility/anomaly). A fresh dev stack with no history therefore produces an
empty digest (the deterministic "No significant market movements detected."),
so a live ETL cycle can't be evaluated for days. To exercise the narration now,
backfill a small synthetic dataset and trigger the state machine by hand.

> `scripts/seed_market_dev.py` is **dev-only**. It writes synthetic items
> (IDs ≥ 90,000,000, so no collision with real arsha.io IDs) over a 14-day
> window ending yesterday, shaped to produce a gainer, a loser, an anomalous
> spike, and an accessory whose enhancement-cost ladder moves.

```sh
# 1. Backfill synthetic market data into dev RDS (over an on-demand break-glass
#    tunnel; there is no standing bastion — ADR-0027).
make break-glass-up STAGE=dev      # deploys an ephemeral EICE + host and opens the tunnel; leave running

# In the second terminal: a DB URL with write access over the tunnel, as the RDS
# master (see First-time role bootstrap). The script accepts the +psycopg form too.
export DATABASE_URL="postgresql://postgres:<MASTER_PW>@localhost:5432/bdo"
uv run python scripts/seed_market_dev.py --dry-run   # preview
uv run python scripts/seed_market_dev.py             # seeds region tw, 14 days

# 2. Trigger the insights state machine for daily and weekly.
SM_ARN=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-dev')].Outputs[] | [?OutputKey=='InsightsStateMachineArn'].OutputValue | [0]" \
  --output text)
aws stepfunctions start-execution --state-machine-arn "$SM_ARN" \
  --input '{"region":"tw","period":"daily"}'
aws stepfunctions start-execution --state-machine-arn "$SM_ARN" \
  --input '{"region":"tw","period":"weekly"}'

# 3. Read the narration back (resolve API_URL + API_KEY as in
#    "Post-deploy verification (dev)" above).
curl -s -H "x-api-key: ${API_KEY}" "${API_URL}/v1/insights?region=tw&period=daily"  | jq .
curl -s -H "x-api-key: ${API_KEY}" "${API_URL}/v1/insights?region=tw&period=weekly" | jq .

# 4. Clean up the synthetic rows when finished, then tear down the break-glass host.
uv run python scripts/seed_market_dev.py --clean
make break-glass-down STAGE=dev    # closes the tunnel and deletes the ephemeral host
```

> The `Summarize` step calls Bedrock. If the dev account/region has **no
> Bedrock model access**, the step catches the error and stores the
> deterministic narrative instead (`model_id` = `deterministic-v1`) — still
> populated, just not LLM-written. Check `model_id` in the response to tell
> which path produced it.

## Recovery & teardown

### Cleanup and teardown

Two levels: reverting a temporary test setup (non-destructive), and deleting a
whole stack (destructive). The legacy pre-v3 decommission is a separate,
one-time exercise -- see `docs/cleanup-tasks.md`.

#### Revert a test setup (non-destructive)

After a dev evaluation, undo the opt-in pieces without touching the stack:

```sh
# Remove synthetic insights rows seeded into RDS (needs an open break-glass
# tunnel; see "Market Insights - dev evaluation").
uv run python scripts/seed_market_dev.py --clean

make break-glass-down STAGE=dev  # close the tunnel and delete the ephemeral EICE + host
# Custom domain: see "Custom API domain → Disable" (unset the CI secrets, then
# redeploy the full state without the domain vars). Only if one was enabled.
make clean                       # local build artifacts (.aws-sam/, etc.)
```

#### Delete a whole stack (destructive)

Deleting `bdo-market-<stage>` tears down the root stack and the nested stacks it
still owns (network, data, ETL, API, insights, observability). Stacks orphaned by
an earlier failed deploy are not removed, so verify afterwards (see "Verify, and
remove any orphaned nested stacks" below). Know what goes with it:

- **RDS is destroyed.** Nothing sets `DeletionPolicy: Retain`, so the Postgres
  instance is deleted. CloudFormation takes a **final snapshot by default** (the
  default deletion policy for a standalone RDS DB instance is `Snapshot`); that
  snapshot persists and bills for storage until you delete it manually
  (`aws rds delete-db-snapshot`).
- **The `bdo-<stage>-items` DynamoDB table is deleted** -- the tracked-items
  list is lost. Export it first if you need it.
- The RDS-managed master secret is removed with the DB. (There is no `dba`
  secret; an on-demand break-glass stack, if one is up, is separate — delete it
  with `make break-glass-down`.)
- Lambda-created CloudWatch log groups can remain orphaned -- delete separately
  if desired. The shared SAM deploy bucket is not part of the stack and stays.
- **The `bdo-<stage>-icons` S3 bucket's fate depends on stage** (ADR-0019):
  - **Prod** *retains* the bucket (`DeletionPolicy: Retain`), so it and its
    objects survive teardown. Its fixed name also makes a later fresh deploy
    fail to *re-create* it (`IconsStack` -> `CREATE_FAILED`, bucket already
    exists) until you purge and delete it by hand (icons are re-fetchable from
    the Pearl Abyss CDN, so the next iconSync run just re-materializes them):

    ```sh
    aws s3 rm s3://bdo-prod-icons --recursive   # purge objects first
    aws s3api delete-bucket --bucket bdo-prod-icons
    ```
  - **Dev** deletes the bucket automatically: `IconsBucketJanitor` (a
    CloudFormation custom resource, non-prod only) empties it during the stack
    delete, then CloudFormation deletes the (non-retaining) bucket itself. No
    manual step, and a later fresh deploy does not collide.

##### Dev

Dev RDS has no deletion protection (`DeletionProtection: !If [IsProd, true,
false]` in `infra/data.yaml`), so the stack deletes directly:

```sh
sam delete --stack-name bdo-market-dev --region us-east-1
# prompts for confirmation; add --no-prompts to skip
```

##### Prod

Prod RDS sets `DeletionProtection: true`, so `sam delete` will FAIL (the DB
lands in `DELETE_FAILED`) until you disable it. This is irreversible -- take a
snapshot you control first.

```sh
# Resolve the (CFN-generated) DB instance id via its Name tag.
RDS_ID=$(aws rds describe-db-instances --region us-east-1 \
  --query "DBInstances[?TagList[?Key=='Name' && Value=='bdo-prod-postgres']].DBInstanceIdentifier | [0]" \
  --output text)

# 1. (Recommended) take a manual final snapshot you control:
aws rds create-db-snapshot --region us-east-1 \
  --db-instance-identifier "$RDS_ID" \
  --db-snapshot-identifier "bdo-prod-final-$(date +%Y%m%d)"

# 2. Disable deletion protection (required before the stack can delete the DB):
aws rds modify-db-instance --region us-east-1 \
  --db-instance-identifier "$RDS_ID" \
  --no-deletion-protection --apply-immediately

# 3. Delete the stack:
sam delete --stack-name bdo-market-prod --region us-east-1
```

##### Verify, and remove any orphaned nested stacks

`sam delete` removes the root stack and cascades to the nested stacks it
**currently owns**. Nested stacks detached by an earlier failed or rolled-back
deploy are no longer linked to the root, so they survive its deletion. A parent
can never reach `DELETE_COMPLETE` while it owns live children, so anything left
behind is an orphan -- always verify, then delete the leftovers directly (a
nested stack can be deleted on its own only once its root is gone):

```sh
# What's still around for this app?
aws cloudformation list-stacks --region us-east-1 \
  --query "StackSummaries[?contains(StackName,'bdo-market-dev') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus,ParentId]" \
  --output table

# Delete leftovers in reverse-dependency order. Network LAST: its subnets and
# security groups cannot delete while another stack's RDS or Lambda ENIs remain.
for S in Observability Api Insights Etl Data Network; do
  NAME=$(aws cloudformation list-stacks --region us-east-1 \
    --query "StackSummaries[?contains(StackName,'bdo-market-dev-${S}Stack') && StackStatus!='DELETE_COMPLETE'].StackName | [0]" \
    --output text)
  if [ -n "$NAME" ] && [ "$NAME" != "None" ]; then
    echo "Deleting $NAME ..."
    aws cloudformation delete-stack --region us-east-1 --stack-name "$NAME"
    aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name "$NAME"
  fi
done
```

For prod, swap `dev` -> `prod` and disable RDS deletion protection first (above).
A `DELETE_FAILED` is usually Network going before another stack released its
ENIs -- delete the remaining compute/data stacks, then retry Network.

> Irreversible. If others may depend on the stack, prefer the staged
> disable -> observe -> delete approach documented in `docs/cleanup-tasks.md`.

### Recreating a stack from scratch

Use this when a stack was deleted, or a first-time create failed and left it in
`ROLLBACK_COMPLETE` (that state can only be deleted, not updated). A few
fixed-name resources survive a delete/rollback and will fail the fresh CREATE
with "already exists", so clear them first, then rebuild the data. Commands show
`dev`; swap `dev` -> `prod` as needed.

#### Clear orphaned resources

```sh
# Lambda-auto-created log groups. ObservabilityStack declares each Lambda's log
# group explicitly, so any group a prior invocation created blocks its CREATE.
for lg in $(aws logs describe-log-groups \
  --log-group-name-prefix /aws/lambda/bdo-dev- \
  --query 'logGroups[].logGroupName' --output text); do
  aws logs delete-log-group --log-group-name "$lg"
done
```

> Icons bucket: on **dev**, `IconsBucketJanitor` empties it and the (non-retaining)
> bucket goes with the stack delete, so no manual step is needed here
> (ADR-0019). On **prod** it is still `Retain` with a fixed name, so it survives
> teardown/rollback and needs the same purge-and-delete as above (swap
> `dev` -> `prod` in the "Delete a whole stack" section).

Confirm nothing lingers:

```sh
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/bdo-dev- \
  --query 'logGroups[].logGroupName'
```

#### Deploy the empty stack

```sh
make deploy STAGE=dev
```

If it still fails with "already exists", that named resource needs the same
delete-then-retry -- see [Troubleshooting](#troubleshooting).

#### Rebuild the data

RDS and DynamoDB come back empty (neither is retained), so rebuild them by
following [First-time bring-up](#first-time-bring-up): role bootstrap → catalog
→ tracked set → icons → verify. Nothing else is needed — the bring-up path is
the same one used for any new stack. The two DynamoDB steps (catalog backfill +
tracked seed) run together, in the correct order, via `make seed-data STAGE=<stage>`.

## Troubleshooting

### General

| Symptom | Investigation |
|---------|---------------|
| ETL timeout | Check arsha.io status page; verify Lambda timeout config in `template.yaml`. |
| RDS connection failures | Check security group rules; verify IAM auth token generation; confirm RDS instance status. |
| `make break-glass-up`: "Unable to connect to target" | EICE can't reach the break-glass host on :22. Confirm the break-glass SG has a self-referencing port-22 egress rule (`BreakGlassSshEgress`) and that the EICE is `available`. |
| Master login: "PAM authentication failed for user postgres" | Master became a (transitive) member of `rds_iam`. See [First-time role bootstrap](#first-time-role-bootstrap) — IAM-auth in and `REVOKE` the role memberships. |
| `make migrate-lambda`: "permission denied for table alembic_version" | `lambda_migrator` lacks DML on `alembic_version`. Re-run the `0003` grant, or one-off as master: `GRANT SELECT, INSERT, UPDATE, DELETE ON alembic_version TO lambda_migrator;`. |
| API 5xx spike | Filter CloudWatch logs by `correlation_id`; look for connection pool exhaustion or query timeouts. |
| Custom-domain deploy hangs at `CREATE_IN_PROGRESS` on the certificate | ACM is waiting for DNS validation. Confirm `HostedZoneId` is the correct zone for `ApiDomainName`, and that the zone is the authoritative one for the domain (NS records at the registrar point to it). Validation usually completes within minutes. |
| Custom domain returns 403 "Forbidden" | Base-path mapping or DNS not resolved yet, or the request omits `x-api-key`. Confirm the A-alias resolves to the regional API domain and include the API key. |
| Missed ETL runs | Safe to re-execute - writes are idempotent on `(region, item_id, sid, snapshot_at)`. |
| CI deploy: "Could not load credentials from any providers" | The `AWS_DEPLOY_ROLE_ARN` secret (and its OIDC role) is not set up. See "CI/CD deploy role (GitHub OIDC) bootstrap". |
| `IconsStack` `CREATE_FAILED` ("Validation failure detected") on deploy | Only expected on **prod** (dev's icons bucket is non-retaining since ADR-0019, and `IconsBucketJanitor` empties it on teardown). The retained `bdo-<stage>-icons` bucket (fixed name) survives an earlier teardown/rollback, so a fresh CREATE collides with the existing bucket. Purge + delete it (see "Cleanup and teardown"), then redeploy. |

### Insights

| Symptom | Investigation |
|---------|---------------|
| Summaries always `model_id=deterministic-v1` | Bedrock not enabled, or IAM denies the model/profile. Check `bdo-<stage>-insights-summarize` logs for `AccessDeniedException`; verify model access + `BedrockModelId`/`BedrockFoundationModelId`. |
| `bdo-<stage>-insight-failures` alarm | `StoreSummary` failed (RDS/IAM). Check its logs + the execution history. Writes are idempotent; re-run via `start-execution`. |
| `bdo-<stage>-insights-execution-failure` alarm | A non-`StoreSummary` state failed (usually `ComputeDigest` — RDS unreachable, or `market_daily` empty for the date). Inspect the Step Functions execution. |
| No Discord message | Check `DiscordDeliveryFailures`; verify the SSM param exists, is `https`, and the webhook is valid. Delivery is best-effort — the summary is still stored and served via the API. |
| `/v1/insights?period=weekly` returns 404 | No weekly run has completed yet (first one lands the Monday after deploy), or the requested `date` predates the first weekly summary. |
