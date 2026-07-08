# Runbook

Operational guide for the live v3 stacks (`bdo-market-dev` / `bdo-market-prod`):
daily operations, deployment, database access, the custom domain, insights
evaluation, cleanup/teardown, and troubleshooting.

## Contents

- [Daily operations](#daily-operations)
- [Deployment](#deployment)
  - [Quick reference](#quick-reference)
  - [Dev deployment (manual)](#dev-deployment-manual)
  - [CI/CD deploy role (GitHub OIDC) bootstrap](#cicd-deploy-role-github-oidc-bootstrap)
  - [Prod deployment (CI/CD)](#prod-deployment-cicd)
  - [Rollback](#rollback)
  - [Breaking changes](#breaking-changes)
- [Database access via bastion](#database-access-via-bastion)
  - [Running migrations](#running-migrations)
  - [First-time role bootstrap](#first-time-role-bootstrap)
- [Custom API domain](#custom-api-domain)
- [Public demo API key](#public-demo-api-key)
- [Market Insights: dev evaluation](#market-insights-dev-evaluation)
- [Cleanup and teardown](#cleanup-and-teardown)
- [Troubleshooting](#troubleshooting)

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

### Dev deployment (manual)

**Use this workflow to test changes on the dev stack before promoting to prod.**

#### Pre-deploy checklist

- [ ] Code review complete (PR merged to `main`)
- [ ] All CI checks passed (lint, typecheck, tests, audit, scan, OpenAPI drift)
- [ ] Schema changes? Ensure migrations are in `migrations/versions/` with sequential numbers
- [ ] Local `make test` passes (including integration if `TEST_DATABASE_URL` is set)

#### Deploy dev

```bash
# Build, verify the layer, and deploy the dev stack (prompts for changeset
# confirmation). `make deploy` runs `make build` (incl. verify-layer) first.
make deploy STAGE=dev

# Watch the deploy progress (optional)
aws cloudformation describe-stacks --stack-name bdo-market-dev \
  --query 'Stacks[0].StackStatus' --output text

# Once `CREATE_COMPLETE` or `UPDATE_COMPLETE`, verify...
```

#### Apply migrations (if applicable)

If your changes include schema migrations (`migrations/versions/*`):

```bash
# One-time only: enable the bastion for dev (if not already deployed).
# `make deploy` runs verify-layer first so it can't republish a broken
# (source-only) CommonLayer. Build on a native Linux FS, not /mnt/*.
make deploy STAGE=dev ENABLE_BASTION=true

# Open tunnel in one terminal
make db-tunnel-up STAGE=dev

# In another terminal, run migrations
make migrate STAGE=dev

# Verify head migration
uv run alembic -c migrations/alembic.ini current

# Close tunnel
make db-tunnel-down

# Optional: disable the bastion to save costs
make deploy STAGE=dev ENABLE_BASTION=false
```

#### Backfill the tracked-index marker (one-time, when adding the GSI)

The `tracked-index` GSI is keyed on a marker attribute (`t`) written only on
tracked items. When the deployment that *adds* the GSI first goes out, items
registered earlier lack the marker and are invisible to the ETL's tracked-item
query. Run the backfill once, right after that deploy and before the next
hourly ETL run, so the tracked set repopulates the index:

```bash
# Preview first, then apply. DynamoDB is reachable via the AWS API (no bastion).
uv run python scripts/backfill_tracked_marker.py --target-table bdo-dev-items --dry-run
uv run python scripts/backfill_tracked_marker.py --target-table bdo-dev-items
```

This is only needed the one time the GSI is introduced; afterwards the marker
is maintained automatically on registration and tracked/untracked changes.

On a fresh or empty table the backfill reports `0 tracked items found` — that is
expected (there is nothing to migrate), not an error. To smoke-test the Query
path on such a table, register one item (see *Post-deploy verification* below):
the API write path stamps the marker automatically, so the item lands in the
`tracked-index` immediately.

#### Post-deploy verification (dev)

```bash
# Resolve the API base URL from the nested API stack. The root stack
# (bdo-market-dev) exposes no outputs of its own, so query across all stacks
# whose name starts with the stack prefix and pick the nested API stack's output.
API_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-dev')].Outputs[] | [?OutputKey=='ApiUrl'].OutputValue | [0]" \
  --output text)

# The API key is an API Gateway key created by the usage plan (NOT Secrets
# Manager). Resolve it via the REST API id so the dev/prod keys in the same
# account are never confused.
API_ID=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-dev')].Outputs[] | [?OutputKey=='ApiId'].OutputValue | [0]" \
  --output text)
# Exclude the read-only demo plan (if enabled) so this resolves the PRIVATE key.
USAGE_PLAN_ID=$(aws apigateway get-usage-plans \
  --query "items[?apiStages[?apiId=='${API_ID}'] && name!='bdo-dev-demo-plan'].id | [0]" --output text)
API_KEY_ID=$(aws apigateway get-usage-plan-keys --usage-plan-id "${USAGE_PLAN_ID}" \
  --query 'items[0].id' --output text)
API_KEY=$(aws apigateway get-api-key --api-key "${API_KEY_ID}" --include-value \
  --query 'value' --output text)

# Test the key-required API (ApiUrl already includes the stage path)
curl -H "x-api-key: ${API_KEY}" "${API_URL}/v1/items" | head -20

# Smoke-test the tracked-index Query path (esp. useful on a fresh/empty table).
# Registering an item validates the id against arsha.io and writes it via
# put_item, which stamps the sparse marker (t="1") because it is tracked -- so
# it must appear in the tracked-index the ETL's retrieveItems now queries.
curl -s -X POST "${API_URL}/v1/items" -H "x-api-key: ${API_KEY}" \
  -H "Content-Type: application/json" -d '{"id": 12094}' | head -20
# Confirm the item is present in the sparse tracked-index (Count >= 1)
aws dynamodb query --table-name bdo-dev-items --index-name tracked-index \
  --key-condition-expression "t = :t" \
  --expression-attribute-values '{":t": {"S": "1"}}' \
  --query 'Count'

# Swagger UI + spec are key-less; use their dedicated outputs
DOCS_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-dev')].Outputs[] | [?OutputKey=='DocsUrl'].OutputValue | [0]" \
  --output text)
curl -s "${DOCS_URL}" | grep -q "swagger-ui" && echo "Swagger UI OK"

# Check CloudWatch Logs for errors (last 10 minutes)
aws logs tail /aws/lambda/bdo-dev-market-query --since 10m --follow
```

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
> (migrations `0001`–`0003` as the master user via the bastion — see "First-time
> role bootstrap") before the CI migrator step can run as `lambda_migrator`.

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

Once the workflow completes (indicated by a ✓ or ✗ badge):

```bash
# Resolve the API base URL from the nested API stack (same cross-stack query
# pattern as dev; the root stack exposes no outputs of its own).
API_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-prod')].Outputs[] | [?OutputKey=='ApiUrl'].OutputValue | [0]" \
  --output text)

# Or, if the custom domain is enabled (ADR-0013), use it instead:
CUSTOM_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-prod')].Outputs[] | [?OutputKey=='CustomApiUrl'].OutputValue | [0]" \
  --output text)

# Resolve the API Gateway key via the REST API id (NOT Secrets Manager)
API_ID=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-prod')].Outputs[] | [?OutputKey=='ApiId'].OutputValue | [0]" \
  --output text)
# Exclude the read-only demo plan (if enabled) so this resolves the PRIVATE key.
USAGE_PLAN_ID=$(aws apigateway get-usage-plans \
  --query "items[?apiStages[?apiId=='${API_ID}'] && name!='bdo-prod-demo-plan'].id | [0]" --output text)
API_KEY_ID=$(aws apigateway get-usage-plan-keys --usage-plan-id "${USAGE_PLAN_ID}" \
  --query 'items[0].id' --output text)
API_KEY=$(aws apigateway get-api-key --api-key "${API_KEY_ID}" --include-value \
  --query 'value' --output text)

# Test the API
curl -H "x-api-key: ${API_KEY}" "${API_URL}/v1/items?limit=1" | head -20

# Check Swagger UI is serving (key-less) via its dedicated output
DOCS_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-prod')].Outputs[] | [?OutputKey=='DocsUrl'].OutputValue | [0]" \
  --output text)
curl -s "${DOCS_URL}" | grep -q "swagger-ui" && echo "Swagger UI OK"

# Verify recent ETL runs succeeded (state machine ARN is exported by the ETL stack)
ETL_ARN=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-prod')].Outputs[] | [?OutputKey=='EtlStateMachineArn'].OutputValue | [0]" \
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

**Note:** Rollbacks are safe for data — ETL writes are idempotent on `(region, item_id, sid, snapshot_at)`. If you rolled back past a schema migration, you may need to manually run `REVOKE` commands on your RDS roles; see the bootstrap note in "Database access via bastion" for details.

### Breaking changes

If you make a breaking change (new required field, schema incompatibility, etc.):

1. **Create a feature branch** and test on dev first
2. **Communicate the change** in the PR and release notes
3. **Deploy with a minor/major version bump** (e.g., `v2.0.0` if breaking)
4. **Update API consumers** before removing old behavior
5. **Consider a canary approach** if possible — deploy to dev/staging first, then prod after a soak period

## Database access via bastion

### Prerequisites

- AWS CLI v2 (with a local `ssh` binary on `PATH`)
- IAM permissions for EICE: `ec2-instance-connect:OpenTunnel`,
  `ec2-instance-connect:SendSSHPublicKey`, `ec2:DescribeInstances`,
  `ec2:DescribeInstanceConnectEndpoints`
- pgAdmin or `psql` (optional; `make migrate` uses the bundled `alembic`)

### Flow

The bastion has **no public IP** (ADR-0009). Access is brokered by the EC2
Instance Connect Endpoint (EICE), so you never SSH to it directly — the
`db-tunnel-up` target tunnels through the EICE with `--connection-type eice`.

1. Ensure the bastion is deployed. If your stack was deployed with
   `EnableBastion=false` (the default), redeploy with it on. The bastion is a
   transient toggle — `make deploy` re-declares the full stack state, so include
   the stage's persistent flags (demo key, domain) too if it has them:

   ```sh
   # `make deploy` builds + verify-layer FIRST, then deploys, so this toggle
   # can never republish a source-only CommonLayer (which would break every
   # function at init with "No module named 'aws_lambda_powertools'"). Build on
   # a native Linux filesystem, NOT a Windows-mounted /mnt/* path (pip --target
   # can silently vendor nothing).
   make deploy STAGE=dev ENABLE_BASTION=true
   ```

2. `make db-tunnel-up STAGE=<dev|prod>` — opens the EICE tunnel to RDS on
   `localhost:5432`. Leave it running; open a second terminal for the next
   steps. Ctrl-C (or `make db-tunnel-down`) closes it.
3. Connect pgAdmin (or psql) to `localhost:5432` using the `dba` role.
   Credentials are in the `bdo-<stage>-dba-credentials` Secrets Manager secret.

### Running migrations

Routine schema migrations run **from inside the VPC**. The CI deploy job
invokes the migrator Lambda (`bdo-<stage>-migrator`) after `sam deploy`;
the function connects to RDS as `lambda_migrator` via IAM auth and runs
`alembic upgrade head`. A GitHub runner cannot reach the private RDS
directly, so it drives the migration through this Lambda (control-plane
invoke). Trigger it by hand for dev:

```sh
make migrate-lambda STAGE=dev
```

### First-time role bootstrap

(Run once, via the bastion.) The Postgres roles themselves are cluster-level
objects created by migrations `0002`/`0003` and need privileges the
`lambda_migrator` role does not hold:

- `0002_bootstrap_roles` — `lambda_rds_user` (runtime, IAM auth) and
  `dba` (human login; created only when `DBA_PASSWORD` is set).
- `0003_migrator_role` — `lambda_migrator` (IAM auth) used by the
  migrator Lambda above; also grants it DML on `alembic_version`.

Apply the full chain (`0001`–`0003`) **once** as the RDS master user
through the bastion tunnel. With the tunnel open (step 2 above), in a second
terminal:

```sh
STAGE=dev

# dba password (so 0002 creates the dba login role):
export DBA_PASSWORD="$(aws secretsmanager get-secret-value \
  --secret-id bdo-${STAGE}-dba-credentials \
  --query SecretString --output text \
  | python -c 'import json,sys; print(json.load(sys.stdin)["password"])')"

# master password from the RDS-managed master secret (NOT the dba secret):
MASTER_SECRET_ARN=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-${STAGE}')].Outputs[] \
           | [?OutputKey=='MasterSecretArn'].OutputValue | [0]" --output text)
MASTER_PW=$(aws secretsmanager get-secret-value --secret-id "$MASTER_SECRET_ARN" \
  --query SecretString --output text \
  | python -c 'import json,sys; print(json.load(sys.stdin)["password"])')

# Use the +psycopg driver -- this project ships psycopg v3 only, so a plain
# postgresql:// URL fails:
export DATABASE_URL="postgresql+psycopg://postgres:${MASTER_PW}@localhost:5432/bdo"

make migrate
uv run alembic -c migrations/alembic.ini current   # expect: 0003 (head)
make db-tunnel-down
```

After this one-time bootstrap, all later schema changes go through
`make migrate-lambda` (or the CI deploy step) — no tunnel required. Drop the
bastion again once you're done (`make deploy STAGE=dev ENABLE_BASTION=false`).

> **Why the bootstrap revokes the master's role membership.** On RDS the
> master is not a superuser, and PostgreSQL 16 auto-grants the creating role
> membership in every role it creates. Because `lambda_rds_user` and
> `lambda_migrator` hold `rds_iam`, that auto-membership makes the master a
> *transitive* member of `rds_iam` — which makes RDS route the master to PAM
> (IAM) auth and **breaks master password login** (`FATAL: PAM authentication
> failed for user "postgres"`). Migrations `0002`/`0003` therefore
> `REVOKE … FROM CURRENT_USER` at the end so the master keeps password auth.
> If you are ever locked out this way, connect once with an IAM token (the
> master now has `rds_iam`, so IAM auth works) and run
> `REVOKE lambda_rds_user FROM postgres; REVOKE lambda_migrator FROM postgres;`.

## Custom API domain

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

### Prerequisites

- The parent domain's hosted zone exists in Route 53 (shared infra; **not**
  created by this stack). Get its ID:

  ```sh
  aws route53 list-hosted-zones-by-name --dns-name example.com \
    --query 'HostedZones[0].Id' --output text   # e.g. /hostedzone/ZXXXXXXXXXXXXX
  ```

  Use the bare ID (the part after `/hostedzone/`).
- IAM permissions to create ACM certificates, API Gateway domain names, and
  Route 53 record sets.

### Enable (prod example)

Set the CI secrets once so every tagged release preserves the domain:

```sh
gh secret set PROD_API_DOMAIN_NAME --body "api.example.com"
gh secret set PROD_HOSTED_ZONE_ID --body "ZXXXXXXXXXXXXX"
```

To apply it now without waiting for a tag, run a full-state deploy — include the
stage's other persistent flags (e.g. the demo key) so they are not dropped:

```sh
make deploy STAGE=prod ENABLE_DEMO_KEY=true \
  API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
```

> `make deploy` runs the verify-layer guard before deploying, so it can never
> republish a source-only `CommonLayer`. Build on a native Linux filesystem,
> not a Windows-mounted `/mnt/*` path.

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

### Disable

Unset the CI secrets (so releases stop re-adding it), then redeploy the full
state without the domain vars — the domain reverts to empty, removing the cert,
domain, base-path mapping, and DNS record:

```sh
gh secret delete PROD_API_DOMAIN_NAME
gh secret delete PROD_HOSTED_ZONE_ID
make deploy STAGE=prod ENABLE_DEMO_KEY=true   # no domain vars -> domain removed
```

## Public demo API key

A public, **read-only** API key for "try the API" links (e.g. a published
Postman workspace). Opt-in and off by default. It runs on a tight usage plan
(2 req/s sustained, 5 burst, 500/day) and is read-only: write requests to
`/v1/items` (`POST`/`PATCH`/`DELETE`) return `403`, enforced in the
`itemRegistry` handler (API Gateway keys can't be scoped to specific methods).
Never publish the privileged stage key — only this demo key.

### Enable

`make deploy` declares the full stack state, so just add `ENABLE_DEMO_KEY=true`
(the [Deployment](#deployment) section covers why the whole state is passed each
time). For **prod** the demo key should persist across releases — the tag-gated
CI deploy sets `EnableDemoKey=true`, so once this is on `main` every release
keeps it live.

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

### Retrieve the key value

The value is generated by API Gateway and is **never** stored in the repo.
Fetch it by the key name (`bdo-<stage>-demo`):

```sh
aws apigateway get-api-keys --name-query "bdo-prod-demo" --include-values \
  --query 'items[0].value' --output text
```

Put the value into the published Postman environment's `apiKey` variable (and
set `baseUrl` to the stage's base URL). To rotate, disable then re-enable (a
new key is created); the usage-plan caps limit abuse in the meantime.

### Verify (read-only)

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

### Disable

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
# 1. Backfill synthetic market data into dev RDS (over the bastion tunnel).
make deploy STAGE=dev ENABLE_BASTION=true   # if the bastion isn't already deployed
make db-tunnel-up STAGE=dev        # leave running; use a second terminal below

# In the second terminal: a DB URL with write access over the tunnel. The RDS
# master (see the bootstrap section) always works; dba works if it has been
# granted table privileges. The script accepts the +psycopg form too.
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
#    "Post-Deploy Verification (Dev)" above).
curl -s -H "x-api-key: ${API_KEY}" "${API_URL}/v1/insights?region=tw&period=daily"  | jq .
curl -s -H "x-api-key: ${API_KEY}" "${API_URL}/v1/insights?region=tw&period=weekly" | jq .

# 4. Clean up the synthetic rows when finished.
uv run python scripts/seed_market_dev.py --clean
make db-tunnel-down
make deploy STAGE=dev ENABLE_BASTION=false   # optional, saves the bastion cost
```

> The `Summarize` step calls Bedrock. If the dev account/region has **no
> Bedrock model access**, the step catches the error and stores the
> deterministic narrative instead (`model_id` = `deterministic-v1`) — still
> populated, just not LLM-written. Check `model_id` in the response to tell
> which path produced it.

## Cleanup and teardown

Two levels: reverting a temporary test setup (non-destructive), and deleting a
whole stack (destructive). The legacy pre-v3 decommission is a separate,
one-time exercise -- see `docs/cleanup-tasks.md`.

### Revert a test setup (non-destructive)

After a dev evaluation, undo the opt-in pieces without touching the stack:

```sh
# Remove synthetic insights rows seeded into RDS (needs an open tunnel; see
# "Market Insights - dev evaluation").
uv run python scripts/seed_market_dev.py --clean

make db-tunnel-down              # close the EICE tunnel
make deploy STAGE=dev ENABLE_BASTION=false   # remove the bastion + EICE (saves cost)
# Custom domain: see "Custom API domain → Disable" (unset the CI secrets, then
# redeploy the full state without the domain vars). Only if one was enabled.
make clean                       # local build artifacts (.aws-sam/, etc.)
```

### Delete a whole stack (destructive)

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
- The `bdo-<stage>-dba-credentials` secret is scheduled for deletion (default
  recovery window); the RDS-managed master secret is removed with the DB.
- Lambda-created CloudWatch log groups can remain orphaned -- delete separately
  if desired. The shared SAM deploy bucket is not part of the stack and stays.

#### Dev

Dev RDS has no deletion protection (`DeletionProtection: !If [IsProd, true,
false]` in `infra/data.yaml`), so the stack deletes directly:

```sh
sam delete --stack-name bdo-market-dev --region us-east-1
# prompts for confirmation; add --no-prompts to skip
```

#### Prod

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

#### Verify, and remove any orphaned nested stacks

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
for S in Observability Api Insights Etl Bastion Data Network; do
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

## Troubleshooting

### General

| Symptom | Investigation |
|---------|---------------|
| ETL timeout | Check arsha.io status page; verify Lambda timeout config in `template.yaml`. |
| RDS connection failures | Check security group rules; verify IAM auth token generation; confirm RDS instance status. |
| `make db-tunnel-up`: "Unable to connect to target" | EICE can't reach the bastion on :22. Confirm the bastion SG has a self-referencing port-22 egress rule (`BastionSshEgress`) and that the EICE is `available`. |
| Master login: "PAM authentication failed for user postgres" | Master became a (transitive) member of `rds_iam`. See the bootstrap note above — IAM-auth in and `REVOKE` the role memberships. |
| `make migrate-lambda`: "permission denied for table alembic_version" | `lambda_migrator` lacks DML on `alembic_version`. Re-run the `0003` grant, or one-off as master: `GRANT SELECT, INSERT, UPDATE, DELETE ON alembic_version TO lambda_migrator;`. |
| API 5xx spike | Filter CloudWatch logs by `correlation_id`; look for connection pool exhaustion or query timeouts. |
| Custom-domain deploy hangs at `CREATE_IN_PROGRESS` on the certificate | ACM is waiting for DNS validation. Confirm `HostedZoneId` is the correct zone for `ApiDomainName`, and that the zone is the authoritative one for the domain (NS records at the registrar point to it). Validation usually completes within minutes. |
| Custom domain returns 403 "Forbidden" | Base-path mapping or DNS not resolved yet, or the request omits `x-api-key`. Confirm the A-alias resolves to the regional API domain and include the API key. |
| Missed ETL runs | Safe to re-execute - writes are idempotent on `(region, item_id, sid, snapshot_at)`. |
| CI deploy: "Could not load credentials from any providers" | The `AWS_DEPLOY_ROLE_ARN` secret (and its OIDC role) is not set up. See "CI/CD deploy role (GitHub OIDC) bootstrap". |

### Insights

| Symptom | Investigation |
|---------|---------------|
| Summaries always `model_id=deterministic-v1` | Bedrock not enabled, or IAM denies the model/profile. Check `bdo-<stage>-insights-summarize` logs for `AccessDeniedException`; verify model access + `BedrockModelId`/`BedrockFoundationModelId`. |
| `bdo-<stage>-insight-failures` alarm | `StoreSummary` failed (RDS/IAM). Check its logs + the execution history. Writes are idempotent; re-run via `start-execution`. |
| `bdo-<stage>-insights-execution-failure` alarm | A non-`StoreSummary` state failed (usually `ComputeDigest` — RDS unreachable, or `market_daily` empty for the date). Inspect the Step Functions execution. |
| No Discord message | Check `DiscordDeliveryFailures`; verify the SSM param exists, is `https`, and the webhook is valid. Delivery is best-effort — the summary is still stored and served via the API. |
| `/v1/insights?period=weekly` returns 404 | No weekly run has completed yet (first one lands the Monday after deploy), or the requested `date` predates the first weekly summary. |
