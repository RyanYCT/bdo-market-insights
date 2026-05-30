# v3 — Design

## Architecture

```
                EventBridge cron 0 * * * *
                            |  {"region": "tw"}
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

   Operator PC --IAM-auth SSH tunnel--> EICE --> bastion --> RDS:5432
```

## Lambdas

| # | Name              | Trigger        | In VPC | Reads                   | Writes                                |
|---|-------------------|----------------|--------|-------------------------|---------------------------------------|
| 1 | retrieveItems     | Step Functions | no     | DynamoDB                | —                                     |
| 2 | fetchData         | Step Functions | no     | arsha.io                | —                                     |
| 3 | cleanData         | Step Functions | no     | (input)                 | —                                     |
| 4 | storeData         | Step Functions | yes    | (input)                 | RDS item + item_sid + market_snapshot |
| 5 | rollupDaily       | Step Functions | yes    | RDS market_snapshot     | RDS market_daily                      |
| 6 | purgeOldSnapshots | EventBridge    | yes    | —                       | RDS market_snapshot                   |
| 7 | itemRegistry      | API Gateway    | no     | DynamoDB, arsha.io      | DynamoDB                              |
| 8 | marketQuery       | API Gateway    | yes    | RDS                     | —                                     |

All eight share one Lambda Layer `bdo-common`:
`arsha_client`, `db`, `dynamo`, `repositories`, `models` (Pydantic v2),
`pricing` (BDO domain math), `analytics`, `config`, `rates.json`.

## Data Model

### RDS Postgres (database `bdo`)

DDL is owned by Alembic (`migrations/versions/0001_initial.py`).

#### `item` — catalog (mirrors DynamoDB; ~500 rows)

```sql
CREATE TABLE item (
    id              INTEGER     PRIMARY KEY,
    name            TEXT        NOT NULL,
    category        TEXT,
    main_category   TEXT,
    sub_category    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `item_sid` — slowly-changing per-(region, item, sid) reference (~3k rows)

```sql
CREATE TABLE item_sid (
    region          VARCHAR(16) NOT NULL,
    item_id         INTEGER     NOT NULL REFERENCES item(id),
    sid             SMALLINT    NOT NULL,
    max_enhance     SMALLINT    NOT NULL,
    price_min       BIGINT      NOT NULL,
    price_max       BIGINT      NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (region, item_id, sid)
);
```

#### `market_snapshot` — hourly time-series (90-day TTL; ~6.5M rows)

```sql
CREATE TABLE market_snapshot (
    region           VARCHAR(16) NOT NULL,
    snapshot_at      TIMESTAMPTZ NOT NULL,
    item_id          INTEGER     NOT NULL,
    sid              SMALLINT    NOT NULL,
    base_price       BIGINT      NOT NULL,
    current_stock    INTEGER     NOT NULL,
    total_trades     BIGINT      NOT NULL,
    last_sold_price  BIGINT      NOT NULL,
    last_sold_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (region, item_id, sid, snapshot_at),
    FOREIGN KEY (region, item_id, sid)
        REFERENCES item_sid (region, item_id, sid)
);
CREATE INDEX ON market_snapshot (snapshot_at);                       -- retention sweep
CREATE INDEX ON market_snapshot (region, item_id, snapshot_at DESC); -- latest-N
```

#### `market_daily` — daily rollup (retained indefinitely; ~270k rows)

```sql
CREATE TABLE market_daily (
    region             VARCHAR(16) NOT NULL,
    trade_date         DATE        NOT NULL,
    item_id            INTEGER     NOT NULL,
    sid                SMALLINT    NOT NULL,
    open_price         BIGINT      NOT NULL,
    high_price         BIGINT      NOT NULL,
    low_price          BIGINT      NOT NULL,
    close_price        BIGINT      NOT NULL,
    avg_price          BIGINT      NOT NULL,
    total_trades_delta BIGINT      NOT NULL,
    avg_stock          INTEGER     NOT NULL,
    snapshot_count     SMALLINT    NOT NULL,
    PRIMARY KEY (region, item_id, sid, trade_date),
    FOREIGN KEY (region, item_id, sid)
        REFERENCES item_sid (region, item_id, sid)
);
CREATE INDEX ON market_daily (region, item_id, trade_date DESC);
```

All price columns are `BIGINT` (BDO prices reach 3 × 10¹¹).
`total_trades` is lifetime cumulative; daily volume is
`end-of-day − start-of-day`.

### DynamoDB

- **`bdo-v3-items`** — PK `id` (Number), `PAY_PER_REQUEST`. Sole
  authoritative source for item registry. Postgres `item` is its
  read-side projection, populated lazily by `storeData` on first
  observation (ADR-0010). Seeded once from `bdo.accessory`.
  GSI **`category-tracked-index`** (PK `category`, SK `tracked`,
  projection ALL) backs `GET /v1/items?category=&tracked=` (FR-8) and
  the ETL's "active items" scan (FR-2). `category` is therefore a
  required attribute; the seed script omits items that lack it rather
  than write an empty GSI key. Items also carry `model_id` (pricing
  model, default `accessory_v1`) and `cron_table` ("a"|"b") for the
  domain math (see `domain-model.md`, ADR-0012).

### Domain model

BDO enhancement economics (probability curves, A1 cost model, tax,
cron tables, analytics definitions) are specified in
[`domain-model.md`](domain-model.md) — the normative source for
`bdo_common.pricing` and `bdo_common.analytics`. The pricing-model
registry is ADR-0012.

## Networking

- **Single-AZ workload** (ADR-0011). One VPC; two private subnets
  spanning two AZs **only because AWS requires the DB Subnet Group to
  span ≥ 2 AZs**; the actual workload (RDS instance, in-VPC Lambdas,
  bastion) runs in the primary AZ only. No cross-AZ data-transfer
  charges; no Multi-AZ RDS premium.
- RDS Postgres in the primary private subnet (Single-AZ deployment);
  SG allows 5432 from the Lambda SG and the bastion SG only.
- **Lambdas attach to the VPC only when they need RDS** (storeData,
  rollupDaily, purgeOldSnapshots, marketQuery), and only to the
  primary subnet. The other four (retrieveItems, fetchData, cleanData,
  itemRegistry) run outside the VPC with default Lambda egress.
- DynamoDB access from in-VPC Lambdas via Gateway Endpoint (free).
- DB credentials: Lambdas use IAM database auth; bastion-mediated human
  access uses a separate `dba` role with password in Secrets Manager.
- **Bastion**: `t4g.nano`, no public IP, in the primary private subnet,
  gated by SAM parameter `EnableBastion`. Reachable via EC2 Instance
  Connect Endpoint (free) for IAM-auth SSH tunnel. Stop/start on
  demand via `make db-tunnel-{up,down}`.
- **No NAT Gateway, no NAT Instance.** Saves ~$32/month.

## Observability

- Powertools `Logger` emits JSON logs with `correlation_id`, propagated
  via Step Functions execution name and API Gateway request ID.
- Powertools `Tracer` wraps every handler.
- Powertools `Metrics` (EMF) emits `BdoMarket/EtlSuccessfulItems`,
  `EtlFailedItems`, `ApiKeyHits`, `AnomaliesDetected`.
- One CloudWatch dashboard (`infra/observability.yaml`).
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
| 0008 | IAM database authentication for Lambdas                        |
| 0009 | EICE bastion for human DBA access (vs publicly-accessible RDS) |
| 0010 | Postgres `item` populated lazily by ETL; Streams sync deferred |
| 0011 | Single-AZ workload; DB Subnet Group spans 2 AZs only because AWS requires it |
| 0012 | Pricing-model registry; `accessory_v1` (A1) shipped, others pluggable |

ADRs live in `docs/adr/`, one Markdown file each, Michael Nygard format.
