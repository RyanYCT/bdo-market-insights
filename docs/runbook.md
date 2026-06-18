# Runbook

## Daily Operations

ETL runs hourly via EventBridge (one execution per active region).
Monitor health from the CloudWatch dashboard:

- **BdoMarket/EtlSuccessfulItems** - items processed without error
- **BdoMarket/EtlFailedItems** - items that failed in the current run

Step Functions console shows full execution history, per-state
input/output, and retry behaviour.

## Database Access via Bastion

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
   `EnableBastion=false` (the samconfig default), redeploy with it on — this
   is purely additive (adds the bastion + EICE, touches nothing else):

   ```sh
   # `make bastion-up` builds + verify-layer FIRST, then deploys, so this
   # additive toggle can never republish a source-only CommonLayer (which
   # would break every function at init with "No module named
   # 'aws_lambda_powertools'"). Build on a native Linux filesystem, NOT a
   # Windows-mounted /mnt/* path (pip --target can silently vendor nothing).
   make bastion-up STAGE=dev
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

### First-time role bootstrap (via bastion)

The Postgres roles themselves are cluster-level objects created by
migrations `0002`/`0003` and need privileges the `lambda_migrator` role
does not hold:

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
bastion again once you're done (`make bastion-down STAGE=dev`).

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

## Common Failure Scenarios

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

## Deployment Procedures

| Environment | Method |
|-------------|--------|
| Dev | `make deploy-dev` (manual; CI deploy is tag-only) |
| Prod | Push a `v*` tag to trigger CI deploy |
| Rollback | Deploy the previous tag |

## Updating a Deployment

### Dev Deployment (Manual, for Testing)

**Use this workflow to test changes on the dev stack before promoting to prod.**

#### Pre-Deploy Checklist

- [ ] Code review complete (PR merged to `main`)
- [ ] All CI checks passed (lint, typecheck, tests, audit, scan, OpenAPI drift)
- [ ] Schema changes? Ensure migrations are in `migrations/versions/` with sequential numbers
- [ ] Local `make test` passes (including integration if `TEST_DATABASE_URL` is set)

#### Deploy Dev

```bash
# Build and validate the SAM template
make build

# Deploy to dev stack (prompts for changeset confirmation)
make deploy-dev

# Watch the deploy progress (optional)
aws cloudformation describe-stacks --stack-name bdo-market-dev \
  --query 'Stacks[0].StackStatus' --output text

# Once `CREATE_COMPLETE` or `UPDATE_COMPLETE`, verify...
```

#### Apply Migrations (if applicable)

If your changes include schema migrations (`migrations/versions/*`):

```bash
# One-time only: enable bastion for dev (if not already deployed).
# `make bastion-up` runs verify-layer first so it can't republish a broken
# (source-only) CommonLayer. Build on a native Linux FS, not /mnt/*.
make bastion-up STAGE=dev

# Open tunnel in one terminal
make db-tunnel-up STAGE=dev

# In another terminal, run migrations
make migrate STAGE=dev

# Verify head migration
uv run alembic -c migrations/alembic.ini current

# Close tunnel
make db-tunnel-down

# Optional: disable bastion to save costs
make bastion-down STAGE=dev
```

#### Post-Deploy Verification (Dev)

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
USAGE_PLAN_ID=$(aws apigateway get-usage-plans \
  --query "items[?apiStages[?apiId=='${API_ID}']].id | [0]" --output text)
API_KEY_ID=$(aws apigateway get-usage-plan-keys --usage-plan-id "${USAGE_PLAN_ID}" \
  --query 'items[0].id' --output text)
API_KEY=$(aws apigateway get-api-key --api-key "${API_KEY_ID}" --include-value \
  --query 'value' --output text)

# Test the key-required API (ApiUrl already includes the stage path)
curl -H "x-api-key: ${API_KEY}" "${API_URL}/v1/items" | head -20

# Swagger UI + spec are key-less; use their dedicated outputs
DOCS_URL=$(aws cloudformation describe-stacks \
  --query "Stacks[?starts_with(StackName,'bdo-market-dev')].Outputs[] | [?OutputKey=='DocsUrl'].OutputValue | [0]" \
  --output text)
curl -s "${DOCS_URL}" | grep -q "swagger-ui" && echo "Swagger UI OK"

# Check CloudWatch Logs for errors (last 10 minutes)
aws logs tail /aws/lambda/bdo-dev-market-query --since 10m --follow
```

---

### Prod Deployment (Automated via CI/CD)

**Use this workflow to release changes to production. All CI checks run automatically before merge.**

#### Pre-Release Checklist (Before Pushing Tag)

- [ ] Changes are merged to `main` and all CI checks passed
- [ ] Schema migrations are sequenced correctly and tested on dev
- [ ] No breaking API changes (or clearly communicated if intentional)
- [ ] Updated ADRs or architecture docs if architectural changes were made
- [ ] Reviewed the diff one final time: `git diff main~1 main`

#### Release to Prod

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

#### Post-Deploy Verification (Prod)

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
USAGE_PLAN_ID=$(aws apigateway get-usage-plans \
  --query "items[?apiStages[?apiId=='${API_ID}']].id | [0]" --output text)
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

#### Schema Migration Verification

After the CI deploy completes, verify migrations ran successfully:

```bash
# Check the migrator Lambda logs
aws logs tail /aws/lambda/bdo-prod-migrator --since 5m --follow

# Or manually invoke the migrator to check status
aws lambda invoke --function-name bdo-prod-migrator \
  --cli-binary-format raw-in-base64-out --payload '{}' /tmp/migrate.json
cat /tmp/migrate.json
```

---

### Rollback Procedure

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
# (see "Post-Deploy Verification (Prod)" section above)
```

**Note:** Rollbacks are safe for data — ETL writes are idempotent on `(region, item_id, sid, snapshot_at)`. If you rolled back past a schema migration, you may need to manually run `REVOKE` commands on your RDS roles; see the bootstrap note in "Database Access via Bastion" for details.

---

### Handling Breaking Changes or Major Versions

If you make a breaking change (new required field, schema incompatibility, etc.):

1. **Create a feature branch** and test on dev first
2. **Communicate the change** in the PR and release notes
3. **Deploy with a minor/major version bump** (e.g., `v2.0.0` if breaking)
4. **Update API consumers** before removing old behavior
5. **Consider a canary approach** if possible — deploy to dev/staging first, then prod after a soak period

## Custom API Domain (optional, ADR-0013)

The API custom domain is opt-in and off by default. The hostname and hosted
zone are **not** stored in committed config — they are passed at deploy time via
the `make domain-up` variables `API_DOMAIN_NAME` / `HOSTED_ZONE_ID` (zone ID is
account-specific). Use `{service}.{env}.example.com`: `api.example.com` for
prod, `api.dev.example.com` for dev.

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

Pass both params; this is additive to the existing stack:

```sh
make domain-up STAGE=prod API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=ZXXXXXXXXXXXXX
```

> `make domain-up` runs the verify-layer guard before this additive deploy, so
> it can never republish a source-only `CommonLayer`. Build on a native Linux
> filesystem, not a Windows-mounted `/mnt/*` path.

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

Redeploy without the domain params (they revert to empty), which removes the
cert, domain, base-path mapping, and DNS record:

```sh
make domain-down STAGE=prod
```

### Insights failure scenarios

| Symptom | Investigation |
|---------|---------------|
| Summaries always `model_id=deterministic-v1` | Bedrock not enabled, or IAM denies the model/profile. Check `bdo-<stage>-insights-summarize` logs for `AccessDeniedException`; verify model access + `BedrockModelId`/`BedrockFoundationModelId`. |
| `bdo-<stage>-insight-failures` alarm | `StoreSummary` failed (RDS/IAM). Check its logs + the execution history. Writes are idempotent; re-run via `start-execution`. |
| `bdo-<stage>-insights-execution-failure` alarm | A non-`StoreSummary` state failed (usually `ComputeDigest` — RDS unreachable, or `market_daily` empty for the date). Inspect the Step Functions execution. |
| No Discord message | Check `DiscordDeliveryFailures`; verify the SSM param exists, is `https`, and the webhook is valid. Delivery is best-effort — the summary is still stored and served via the API. |
| `/v1/insights?period=weekly` returns 404 | No weekly run has completed yet (first one lands the Monday after deploy), or the requested `date` predates the first weekly summary. |
