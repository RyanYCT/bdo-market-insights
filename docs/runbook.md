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
   sam deploy --config-env dev \
     --parameter-overrides "Stage=dev BdoRegion=tw UseRdsProxy=false EnableBastion=true"
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
bastion again once you're done (`sam deploy … EnableBastion=false`).

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

## Custom API Domain (optional, ADR-0013)

The API custom domain is opt-in and off by default. The hostname and hosted
zone are **not** stored in committed config — they are passed at deploy time via
`--parameter-overrides` (zone ID is account-specific). Use
`{service}.{env}.example.com`: `api.example.com` for prod, `api.dev.example.com`
for dev.

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
sam deploy --config-env prod \
  --parameter-overrides "ApiDomainName=api.example.com HostedZoneId=ZXXXXXXXXXXXXX"
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

### Disable

Redeploy without the overrides (params default to empty), which removes the
cert, domain, base-path mapping, and DNS record:

```sh
sam deploy --config-env prod
```
