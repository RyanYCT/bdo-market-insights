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
2. `make db-tunnel-up` - opens an SSH tunnel via EICE through the
   bastion host to RDS on port 5432.
3. Connect pgAdmin (or psql) to `localhost:5432` using the `dba` role.
   Credentials are stored in Secrets Manager.
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
