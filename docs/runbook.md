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

Migrations are **not** run by CI: RDS has no public access, so a
GitHub runner cannot reach it. Run them through the tunnel instead:

```sh
make db-tunnel-up STAGE=dev          # terminal 1 (keep open)

# terminal 2 -- master creds from the RDS-managed master secret:
export DATABASE_URL="postgresql://postgres:<master-pw>@localhost:5432/bdo"
make migrate
```

### First-time role bootstrap

Migration `0002_bootstrap_roles` creates the `lambda_rds_user`
(IAM auth) and `dba` (login) Postgres roles. The `dba` role is only
created when `DBA_PASSWORD` is set; source it from the dba secret
before the first migrate:

```sh
export DBA_PASSWORD="$(aws secretsmanager get-secret-value \
  --secret-id bdo-dev-dba-credentials \
  --query SecretString --output text | python -c 'import json,sys; print(json.load(sys.stdin)["password"])')"
make migrate
```

4. `make db-tunnel-down` - tears down the tunnel.

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
