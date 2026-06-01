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

- AWS CLI v2
- EC2 Instance Connect (EIC) CLI plugin
- pgAdmin or `psql`

### Flow

1. Ensure the bastion is deployed (`EnableBastion=true` in samconfig)
   and the EC2 instance is running.
2. `make db-tunnel-up STAGE=<dev|prod>` - opens an SSH tunnel via EICE
   through the bastion host to RDS on `localhost:5432`. Leave it
   running; open a second terminal for the next steps. Press Ctrl-C
   (or `make db-tunnel-down`) to close it.
3. Connect pgAdmin (or psql) to `localhost:5432` using the `dba` role.
   Credentials are in the `bdo-<stage>-dba-credentials` Secrets Manager
   secret.

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
  migrator Lambda above.

Apply the full chain (`0001`–`0003`) **once** as the RDS master user
through the bastion tunnel. Use the `+psycopg` driver — this project
ships psycopg v3 only, so a plain `postgresql://` URL fails:

```sh
make db-tunnel-up STAGE=dev          # terminal 1 (keep open)

# terminal 2 -- master creds from the RDS-managed master secret.
# Source the dba password so 0002 creates the dba login role:
export DBA_PASSWORD="$(aws secretsmanager get-secret-value \
  --secret-id bdo-dev-dba-credentials \
  --query SecretString --output text | python -c 'import json,sys; print(json.load(sys.stdin)["password"])')"
export DATABASE_URL="postgresql+psycopg://postgres:<master-pw>@localhost:5432/bdo"
make migrate
make db-tunnel-down
```

After this one-time bootstrap, all later schema changes go through
`make migrate-lambda` (or the CI deploy step) — no tunnel required.

## Common Failure Scenarios

| Symptom | Investigation |
|---------|---------------|
| ETL timeout | Check arsha.io status page; verify Lambda timeout config in `template.yaml`. |
| RDS connection failures | Check security group rules; verify IAM auth token generation; confirm RDS instance status. |
| API 5xx spike | Filter CloudWatch logs by `correlation_id`; look for connection pool exhaustion or query timeouts. |
| Missed ETL runs | Safe to re-execute - writes are idempotent on `(region, item_id, sid, snapshot_at)`. |

## Deployment Procedures

| Environment | Method |
|-------------|--------|
| Dev | `make deploy-dev` (manual; CI deploy is tag-only) |
| Prod | Push a `v*` tag to trigger CI deploy |
| Rollback | Deploy the previous tag |
