# BDO Market Insights

A **serverless, event-driven market-data platform** for the *Black Desert Online* in-game economy, built on AWS. It ingests hourly price/stock data, stores it as a time series, and exposes it through a REST API with domain-specific analytics — expected item-enhancement cost, price volatility, liquidity, and anomaly detection.

The project is a study in building a **production-grade serverless data pipeline cheaply and safely**: IAM-authenticated database access (no passwords), a no-NAT VPC, single shared Lambda layer, infrastructure-as-code with nested stacks, and a full CI quality gate — all targeted at **under ~US$15/month** of incremental cost.

> **Status:** Live (v3). 9 Lambdas, a Step Functions ETL pipeline, and two REST APIs deployed across `dev` and `prod` via AWS SAM. All implementation phases complete.

---

## Why this project

Game economies are large, volatile, real-time datasets — a realistic stand-in for any financial/IoT time-series problem. The interesting engineering isn't the game; it's the constraints:

- **Time-series ingestion** from a third-party API with [five different polymorphic response shapes](#data-pipeline) that must be normalized into one schema.
- **Cost discipline** — a serverless architecture that deliberately avoids the usual cost traps (NAT gateways, idle compute, RDS Proxy unless needed).
- **Security by default** — Lambdas reach Postgres via **IAM database authentication**, so there are no database passwords anywhere in the system.
- **Domain analytics** — turning raw prices into decisions (e.g. *what does it actually cost, on average, to enhance this accessory to PEN?*) using a probabilistic Markov model.

## What it does

| Capability | Detail |
|---|---|
| **Hourly ETL** | EventBridge → Step Functions fans out fetch → clean → store across tracked items; idempotent writes to Postgres. |
| **Time-series storage** | Hourly `market_snapshot` rows, compacted daily into `market_daily` (OHLC-style), with 90-day retention. |
| **Item Registry API** | `/v1/items` — CRUD over a DynamoDB-backed catalog; new items validated against the upstream API before being tracked. |
| **Market Query API** | `/v1/market` — raw snapshots, daily rollups, and a combined **analysis** endpoint (enhancement cost + volatility + liquidity + anomaly flag). |
| **Region-aware** | Defaults to the TW server; KR/NA/EU/… can be activated by adding one EventBridge rule — **no code or schema change**. |

## Architecture

```
                    EventBridge (hourly cron, per region)
                              │
                              ▼
                   Step Functions state machine
                              │
        retrieveItems ──► Map(≤5) [ fetchData ─► cleanData ─► storeData ] ──► rollupDaily
        (DynamoDB scan)        (arsha.io)     (normalize)   (1 txn upsert)   (daily OHLC)
                                                                  │
                                                                  ▼
                                                          RDS PostgreSQL
                                                  item · item_sid · market_snapshot · market_daily
                              ▲                                   ▲
                              │                                   │ (IAM auth, in-VPC)
   API Gateway (REST + API key + usage plan)                      │
        ├─ itemRegistry Lambda ─► DynamoDB (item catalog, outside VPC)
        ├─ marketQuery  Lambda ─► RDS (read-only, in-VPC)
        └─ docs        Lambda ──► OpenAPI spec + Swagger UI (key-less routes)
```

**Shared Lambda layer (`bdo-common`)** holds all reusable logic — the arsha.io client + normalizer, psycopg connection helper, Pydantic models, SQL repositories, the pricing models, and the analytics functions — so the eight handlers stay thin.

See [`docs/architecture.md`](docs/architecture.md) for the full breakdown and [`docs/adr/`](docs/adr/) for the 13 Architecture Decision Records explaining the *why* behind each major choice.

## Tech stack

- **Language:** Python 3.12, fully type-annotated (`mypy --strict`)
- **Compute:** AWS Lambda (8 functions + an in-VPC migrator + docs API), Step Functions, EventBridge
- **Data:** Amazon RDS for PostgreSQL (time series), DynamoDB (item registry), Alembic (schema migrations)
- **API:** API Gateway (REST) with API-key usage plans; OpenAPI 3.1 spec auto-generated from handlers and served via interactive Swagger UI
- **IaC:** AWS SAM — one root `template.yaml` with nested stacks (`network`, `data`, `etl`, `api`, `observability`, `bastion`)
- **Libraries:** AWS Lambda Powertools (logging, tracing, metrics, validation), Pydantic v2, psycopg 3, PyYAML (spec parsing)
- **Observability:** CloudWatch dashboard + SLO alarms, X-Ray tracing, EMF custom metrics (`BdoMarket/*`)
- **Tooling:** `uv` (deps), `ruff` (lint/format), `pytest` + `moto` (tests), GitHub Actions (CI/CD), OpenAPI spec generation and drift detection

## Engineering highlights

These are the decisions a reviewer might find most relevant:

- **Passwordless database access.** Every Lambda connects to Postgres using **IAM database authentication** — short-lived tokens, no secrets to rotate or leak. A separate `dba` role (Secrets Manager) exists only for human access. *(ADR-0008)*
- **No-NAT private networking.** DB-touching Lambdas run in a VPC; a DynamoDB **Gateway Endpoint** gives in-VPC access with zero NAT cost. Human DBA access goes through an **EC2 Instance Connect Endpoint** + a `t4g.nano` bastion with **no public IP**. *(ADR-0006, ADR-0009)*
- **Resilient ingestion.** The arsha.io client normalizes five polymorphic JSON shapes, caps URL length, auto-splits oversized ID batches, and bounds concurrency/rate. Retries, tracing, and structured logging come from Powertools rather than hand-rolled code. *(ADR-0007)*
- **Idempotent, transactional writes.** `storeData` upserts `item`/`item_sid` and bulk-inserts snapshots in a single transaction, keyed on `(region, item_id, sid, snapshot_at)` — so a missed or re-run ETL execution is always safe. *(NFR-4)*
- **Domain pricing model.** A pluggable model registry computes expected enhancement cost via a Markov chain over enhancement tiers (including the "cron stone" path), validated against worked examples on real in-game data. *(ADR-0012)*
- **Cost-first design.** Single-AZ workload, opt-in RDS Proxy, no NAT, shared layer, 90-day snapshot retention — targeting **≤ US$15/month** incremental. *(ADR-0011)*
- **Quality gate.** Every change runs through `ruff`, `mypy --strict`, `pytest` (unit + an integration suite against ephemeral Postgres), `bandit`, `pip-audit`, `cfn-lint`, and an **OpenAPI drift check** — production deploys are tag-gated with OIDC (no long-lived AWS keys in CI).

## API overview

All `/v1/*` endpoints require an API key (usage plan: 10 RPS burst / 5 sustained, 1000 req/day). The API is served from the generated API Gateway URL, with an optional branded custom domain (`api[.<env>].example.com`) via ACM + Route 53 (ADR-0013).

### Interactive API Documentation

The API publishes auto-generated, interactive documentation via two key-less routes:

- **`GET /v1/docs`** — Swagger UI interface for exploring and testing endpoints
- **`GET /v1/openapi.json`** — The OpenAPI 3.1 specification (generated by `scripts/export_openapi.py`)

The specification is rebuilt on each code change, validated in CI, and serves as the single source of truth for the API contract. Swagger UI automatically repoints to the correct base URL when accessed through different stages or custom domains.

### Endpoints

```
# Item registry (DynamoDB-backed)
GET    /v1/items?category=&tracked=
GET    /v1/items/{id}
POST   /v1/items                      # validates id against arsha.io before tracking
PATCH  /v1/items/{id}
DELETE /v1/items/{id}                 # soft delete (tracked=false)

# Market data (RDS-backed)
GET    /v1/market/items/{id}/snapshots?region=&sid=&from=&to=&limit=
GET    /v1/market/items/{id}/daily?region=&sid=&from=&to=
GET    /v1/market/items/{id}/analysis?region=&sid=&window_days=14
```

The `analysis` response combines a per-tier `expected_enhance_cost`, rolling-window volatility (σ, CV), a liquidity measure, and an `is_anomalous` flag (|z-score| > 3 vs the trailing window).

## Getting started

### Prerequisites
- Python 3.12, [`uv`](https://docs.astral.sh/uv/), [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html), and AWS credentials.

### Local development
```bash
uv sync          # install dependencies
make lint        # ruff check + format check
make typecheck   # mypy --strict
make test        # pytest (unit; integration auto-skips without TEST_DATABASE_URL)
```

### Deployment
```bash
make deploy-dev          # deploy the dev stack
git tag v1.x.x && git push origin v1.x.x   # tag-gated CI deploy to prod
```
Database schema is managed by Alembic and applied via an in-VPC migrator Lambda. See the [runbook](docs/runbook.md) for the one-time role bootstrap and bastion/tunnel workflow.

## Documentation

- **[Architecture](docs/architecture.md)** — components, data flow, networking
- **[ADRs](docs/adr/)** — 13 architecture decision records
- **[Runbook](docs/runbook.md)** — operations, DB access, migrations, failure scenarios
- **[SLOs](docs/slo.md)** — availability and latency targets

## Project status & scope

All implementation phases (infrastructure, shared layer, ETL, APIs, observability, production cutover) are complete. Intentionally **out of scope** for this version — each with a documented upgrade path: price-alert eventing, S3 export, a public frontend, Cognito/OAuth, multi-region activation, failstack-aware enhancement modelling, and multi-AZ deployment.

## License

MIT — see [LICENSE](LICENSE).
