# v3 — Design

## Architecture

```
                EventBridge cron 0 * * * *
                            |
                            v
            +---------------------------------+
            | StateMachine: bdo-etl           |
            +---------------------------------+
            | retrieveItems   (DynamoDB Scan) |
            | Map(maxConc=5, batch=50)        |
            |   |- fetchData   (arsha.io)     |
            |   |- cleanData   (normalize)    |
            |   '- storeData   (RDS upsert)   |
            | Choice (end-of-day?)            |
            |   '- rollupDaily (RDS aggregate)|
            +---------------------------------+

                EventBridge cron 30 0 * * *
                            |
                            v
            +---------------------------------+
            | purgeOldSnapshots               |
            +---------------------------------+

   Client --API key--> API Gateway (/v1/*)
                            |
                +-----------+-----------+
                v                       v
         itemRegistry L          marketQuery L
            DynamoDB              RDS Postgres
```

## Lambdas

| # | Name              | Trigger        | In VPC | Reads               | Writes              |
|---|-------------------|----------------|--------|---------------------|---------------------|
| 1 | retrieveItems     | Step Functions | no     | DynamoDB            | —                   |
| 2 | fetchData         | Step Functions | no     | arsha.io            | —                   |
| 3 | cleanData         | Step Functions | no     | (input)             | —                   |
| 4 | storeData         | Step Functions | yes    | (input)             | RDS market_snapshot |
| 5 | rollupDaily       | Step Functions | yes    | RDS market_snapshot | RDS market_daily    |
| 6 | purgeOldSnapshots | EventBridge    | yes    | —                   | RDS market_snapshot |
| 7 | itemRegistry      | API Gateway    | no     | DynamoDB, arsha.io  | DynamoDB            |
| 8 | marketQuery       | API Gateway    | yes    | RDS                 | —                   |

All eight share one Lambda Layer `bdo-common`:
`arsha_client`, `db`, `dynamo`, `models` (Pydantic v2), `pricing` (BDO
domain math), `analytics`, `config`, `rates.json`.

## Data Model

### RDS Postgres (database `bdo`)

DDL is owned by Alembic (`migrations/versions/0001_initial.py`).

- **`market_snapshot`** — hourly raw, 90-day TTL.
  PK `(item_id, sid, snapshot_at)`.
  Cols: `name, base_price, current_stock, total_trades, price_min,
  price_max, last_sold_price, last_sold_at, min_enhance, max_enhance`.
  Indexes: `(snapshot_at)` for retention sweep,
  `(item_id, snapshot_at DESC)` for latest-N queries.
- **`market_daily`** — daily rollup, retained indefinitely.
  PK `(item_id, sid, trade_date)`.
  Cols: `name, open_price, high_price, low_price, close_price,
  avg_price, total_trades_delta, avg_stock, snapshot_count`.

All price columns are `BIGINT` (BDO prices reach 3 × 10¹¹).
`total_trades` is lifetime cumulative; daily volume is the diff between
end-of-day and start-of-day values.

### DynamoDB

- **`bdo-v3-items`** — PK `id` (Number), `PAY_PER_REQUEST`.
  Attrs: `name, category, main_category, sub_category, tracked,
  created_at, updated_at`.
  GSI `category-tracked-index`: PK `category`, SK `tracked`.
  Seeded once from the existing `bdo.accessory` table (23 items).

## Networking

- One VPC, 2 AZs, 2 private subnets.
- RDS Postgres in private subnets, security-grouped to the Lambda SG only.
- **Lambdas attach to the VPC only when they need RDS** (storeData,
  rollupDaily, purgeOldSnapshots, marketQuery). The other four run
  outside the VPC with default Lambda egress.
- DynamoDB access from in-VPC Lambdas via Gateway Endpoint (free).
- DB credentials via IAM database authentication (no Secrets Manager
  endpoint needed); fallback to env-var-passed Secrets Manager ARN.
- **No NAT Gateway, no NAT Instance.** Saves ~$32/month.

## Observability

- Powertools `Logger` emits JSON logs with `correlation_id`, propagated
  via Step Functions execution name.
- Powertools `Tracer` wraps every handler.
- Powertools `Metrics` (EMF) emits `BdoMarket/EtlSuccessfulItems`,
  `EtlFailedItems`, `ApiKeyHits`, `AnomaliesDetected`.
- One CloudWatch dashboard (`infra/observability.yaml`): per-Lambda
  invocations/errors/duration, state-machine success rate, custom
  business metrics.
- Alarms: ETL failure (any ABORTED in 24 h), API 5xx > 1% over 5 min,
  p95 latency breach.

## Auth

API Gateway REST API with usage plan + API key. Per-key throttle
10 RPS burst / 5 RPS sustain, quota 1000 req/day.

## Key Decisions (ADR pointers)

| ADR  | Decision                                                       |
|------|----------------------------------------------------------------|
| 0001 | AWS SAM over CDK                                               |
| 0002 | RDS Proxy opt-in; module-global psycopg connection by default  |
| 0003 | Single shared Lambda Layer `bdo-common`                        |
| 0004 | Step Functions ETL with per-stage retry & observability        |
| 0005 | API key + usage plan; Cognito deferred                         |
| 0006 | Mixed VPC placement to eliminate NAT cost                      |
| 0007 | AWS Lambda Powertools instead of hand-rolled utilities         |
| 0008 | IAM database authentication for RDS where supported            |

ADRs live in `docs/adr/`, one Markdown file each, Michael Nygard format.
