# v3 — Requirements

## Product

A serverless market-data platform for Black Desert Online: hourly snapshot
ingestion from arsha.io, time-series storage, BDO-domain analytics
(per-tier expected enhancement cost, volatility, liquidity, anomaly
flags), and a programmatic item registry — all on AWS Lambda.

Server region: TW (configurable).

## Functional Requirements

### Data ingestion (ETL)

- **FR-1** Hourly EventBridge cron triggers the ETL state machine.
- **FR-2** ETL reads the active item list (`tracked = true`) from DynamoDB.
- **FR-3** ETL fetches market data from
  `https://api.arsha.io/v2/{region}/GetWorldMarketSubList?id=<csv>` in
  batches of ≤ 50 IDs, parallelism ≤ 5, ≤ 1 RPS per worker.
- **FR-4** A response normalizer flattens the 5 polymorphic shapes
  (single/multi × enhanceable/non-enhanceable, plus mixed) into
  `list[Record]`.
- **FR-5** Records are validated by Pydantic and bulk-inserted into
  `market_snapshot`, idempotent on `(item_id, sid, snapshot_at)`.
- **FR-6** Once per day at 00:05 UTC, `rollupDaily` aggregates the
  previous day's hourly snapshots into `market_daily`.
- **FR-7** Once per day at 00:30 UTC, `purgeOldSnapshots` deletes
  snapshots older than 90 days.

### Item Registry API (`/v1/items`)

- **FR-8** `GET /v1/items` lists items; query params: `category`, `tracked`.
- **FR-9** `GET /v1/items/{id}` returns one item or 404.
- **FR-10** `POST /v1/items` registers a new item; the handler validates
  the ID exists by calling arsha.io before persisting.
- **FR-11** `PATCH /v1/items/{id}` updates metadata (incl. `tracked`).
- **FR-12** `DELETE /v1/items/{id}` soft-deletes (sets `tracked = false`).

### Market Query API (`/v1/market`)

- **FR-13** `GET /v1/market/items/{id}/snapshots?sid=&from=&to=&limit=`
  returns raw hourly snapshots, capped at 1000 per request.
- **FR-14** `GET /v1/market/items/{id}/daily?sid=&from=&to=` returns
  daily rollups.
- **FR-15** `GET /v1/market/items/{id}/analysis?sid=&window_days=14`
  returns: `expected_enhance_cost` per sid step (BDO base-rate model),
  rolling-window volatility (σ, CV), liquidity (avg trade-volume per
  day), and `is_anomalous` flag (|z-score| > 3 vs trailing window).

### Auth

- **FR-16** All `/v1/*` endpoints require an API key (API Gateway usage
  plan).
- **FR-17** Default plan: 10 RPS burst / 5 RPS sustained, 1000 req/day
  quota per key.

## Non-Functional Requirements

### Performance
- **NFR-1** ETL p95 ≤ 60 s end-to-end for 500 items.
- **NFR-2** API p95 ≤ 500 ms (cold) / ≤ 100 ms (warm).

### Reliability
- **NFR-3** SLO: 99% monthly availability of `/v1/*`. ETL: ≥ 23 of 24
  successful runs/day.
- **NFR-4** Snapshot writes are idempotent; missed runs are recoverable
  by re-execution.

### Observability
- **NFR-5** Structured JSON logs with correlation IDs, propagated via
  Step Functions execution name.
- **NFR-6** X-Ray tracing across API Gateway, Lambda, RDS, DynamoDB.
- **NFR-7** One CloudWatch dashboard defined in CFN; alarms on ETL
  failure, API 5xx > 1% (5-min window), p95 latency breach.
- **NFR-8** Custom metrics in namespace `BdoMarket/*` via EMF.

### Security
- **NFR-9** Postgres credentials retrieved via IAM database
  authentication (preferred) or AWS Secrets Manager.
- **NFR-10** IAM least-privilege per Lambda; no wildcard resources.
- **NFR-11** RDS in private subnets; only DB-touching Lambdas attach
  to the VPC; no NAT Gateway.
- **NFR-12** `pip-audit` and `bandit` gate every PR.

### Cost
- **NFR-13** Target ≤ US$15/month incremental over the existing RDS
  baseline.
- **NFR-14** RDS Proxy is opt-in via SAM parameter `UseRdsProxy`
  (default `false`).

### Maintainability
- **NFR-15** Python 3.12; one `pyproject.toml` (uv); one `template.yaml`
  with nested stacks (`network`, `data`, `etl`, `api`, `observability`).
- **NFR-16** Single GitHub Actions workflow:
  ruff → mypy → pytest (moto + ephemeral Postgres) → SAM deploy on tag.
- **NFR-17** Alembic owns the Postgres schema.
- **NFR-18** No hand-rolled retry, circuit breaker, rate limiter, or
  structured logger — use AWS Lambda Powertools.

## Out of scope (Phase 1)

- Price-alert eventing
- Snapshot export to S3
- Public dashboard / frontend
- Cognito / OAuth
- Multi-region / multi-server (TW only)
- Failstack-aware enhancement modelling (base-rate only in v1)
- Backfill from old market data — only the 23 item IDs from
  `bdo.accessory` are carried over via a one-time seed script
