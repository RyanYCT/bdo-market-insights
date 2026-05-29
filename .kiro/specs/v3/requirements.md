# v3 — Requirements

## Product

A serverless market-data platform for Black Desert Online: hourly snapshot
ingestion from arsha.io, time-series storage, BDO-domain analytics
(per-tier expected enhancement cost, volatility, liquidity, anomaly
flags), and a programmatic item registry — all on AWS Lambda.

Default server region: TW; the schema and pipeline are region-aware so
additional regions (KR, NA, EU, …) can be activated by adding an
EventBridge rule, with no code or schema change.

## Functional Requirements

### Data ingestion (ETL)

- **FR-1** Hourly EventBridge cron triggers the ETL state machine with
  input `{ "region": "<region>" }`.
- **FR-2** ETL reads the active item list (`tracked = true`) from
  DynamoDB, propagating the full metadata (id, name, category, …)
  through the Step Functions execution input.
- **FR-3** ETL fetches market data from
  `https://api.arsha.io/v2/{region}/GetWorldMarketSubList?id=<csv>`.
  Items are dispatched in groups of ≤ 50 IDs (~350-char URL); the
  `arsha_client` defensively caps URL length at 1900 chars and splits
  oversized batches automatically. Parallelism ≤ 5, ≤ 1 RPS per worker.
- **FR-4** A response normalizer flattens the 5 polymorphic shapes
  (single/multi × enhanceable/non-enhanceable, plus mixed) into
  `list[Record]`.
- **FR-5** `storeData` upserts `item` and `item_sid`, then bulk-inserts
  `market_snapshot`, all in one transaction; idempotent on
  `(region, item_id, sid, snapshot_at)`.
- **FR-6** Once per day at 00:05 UTC, `rollupDaily` aggregates the
  previous day's hourly snapshots into `market_daily`.
- **FR-7** Once per day at 00:30 UTC, `purgeOldSnapshots` deletes
  snapshots older than 90 days.

### Item Registry API (`/v1/items`)

- **FR-8** `GET /v1/items` lists items; query params: `category`,
  `tracked`. Source: DynamoDB.
- **FR-9** `GET /v1/items/{id}` returns one item or 404. Source: DynamoDB.
- **FR-10** `POST /v1/items` registers a new item; the handler validates
  the ID via arsha.io and writes to DynamoDB. Postgres `item` is
  populated lazily by the next ETL run (ADR-0010).
- **FR-11** `PATCH /v1/items/{id}` updates metadata (incl. `tracked`)
  in DynamoDB.
- **FR-12** `DELETE /v1/items/{id}` soft-deletes (sets `tracked = false`)
  in DynamoDB.

### Market Query API (`/v1/market`)

- **FR-13** `GET /v1/market/items/{id}/snapshots?region=&sid=&from=&to=&limit=`
  returns raw hourly snapshots, capped at 1000 per request. Default
  `region=tw`.
- **FR-14** `GET /v1/market/items/{id}/daily?region=&sid=&from=&to=`
  returns daily rollups.
- **FR-15** `GET /v1/market/items/{id}/analysis?region=&sid=&window_days=14`
  returns `expected_enhance_cost` per sid step (BDO base-rate model),
  rolling-window volatility (σ, CV), liquidity, and `is_anomalous`
  flag (|z-score| > 3 vs trailing window).

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
  successful runs/day per active region.
- **NFR-4** Snapshot writes are idempotent; missed runs are recoverable
  by re-execution.

### Observability
- **NFR-5** Structured JSON logs with correlation IDs propagated via
  Step Functions execution name and API Gateway request ID.
- **NFR-6** X-Ray tracing across API Gateway, Lambda, RDS, DynamoDB.
- **NFR-7** One CloudWatch dashboard defined in CFN; alarms on ETL
  failure, API 5xx > 1% (5-min window), p95 latency breach.
- **NFR-8** Custom metrics in namespace `BdoMarket/*` via EMF.

### Security
- **NFR-9** Lambdas authenticate to Postgres via IAM database
  authentication (no static passwords). A separate `dba` Postgres role
  with credentials in Secrets Manager exists for human operator access
  via the bastion (NFR-19).
- **NFR-10** IAM least-privilege per Lambda; no wildcard resources.
- **NFR-11** RDS in private subnets; only DB-touching Lambdas attach
  to the VPC; no NAT Gateway.
- **NFR-12** `pip-audit` and `bandit` gate every PR.

### Cost
- **NFR-13** Target ≤ US$15/month incremental over the existing RDS
  baseline. Single-AZ workload (ADR-0011); RDS Proxy opt-in (NFR-14);
  no NAT Gateway (ADR-0006).
- **NFR-14** RDS Proxy is opt-in via SAM parameter `UseRdsProxy`
  (default `false`).

### Maintainability
- **NFR-15** Python 3.12; one `pyproject.toml` (uv); one `template.yaml`
  with nested stacks (`network`, `data`, `etl`, `api`,
  `observability`, `bastion`).
- **NFR-16** Single GitHub Actions workflow:
  ruff → mypy → pytest (moto + ephemeral Postgres) → SAM deploy on tag.
- **NFR-17** Alembic owns the Postgres schema.
- **NFR-18** No hand-rolled retry, circuit breaker, rate limiter, or
  structured logger — use AWS Lambda Powertools.

### Operability
- **NFR-19** A `t4g.nano` bastion in the private subnet, fronted by
  EC2 Instance Connect Endpoint, provides on-demand IAM-authenticated
  SSH port-forwarding from operator machines to RDS:5432 for pgAdmin
  access. Provisioned via SAM parameter `EnableBastion` (default
  `false`); managed via `make db-tunnel-up` / `make db-tunnel-down`.

## Out of scope (Phase 1)

- Price-alert eventing
- Snapshot export to S3
- Public dashboard / frontend
- Cognito / OAuth
- Multi-region activation (schema is ready; only `tw` cron runs)
- Failstack-aware enhancement modelling (base-rate only in v1)
- DynamoDB Streams sync of `item` rows (lazy ETL-driven population in
  v1; ADR-0010 documents the upgrade path)
- Multi-AZ deployment (Single-AZ in v1; ADR-0011 documents the upgrade
  path)
