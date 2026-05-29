# v3 — Implementation Tasks

Each box is one PR. Phases are checkpoints; no phase ships half-built.

## Phase 0 — Branch & specs (this PR)

- [x] Specs at `.kiro/specs/v3/{requirements,design,tasks}.md`
- [ ] Tag `archive/main-v1` at `origin/main` (deferred to cutover)
- [ ] Rename remote `rewrite-project` → `archive/rewrite-project`
  (deferred to cutover)

## Phase 1 — Project scaffolding

- [ ] `pyproject.toml` (Python 3.12; uv; ruff, mypy, pytest,
      aws-lambda-powertools, pydantic v2)
- [ ] `Makefile` (build / test / lint / deploy)
- [ ] `.github/workflows/ci.yml` — single workflow:
      ruff → mypy → pytest → sam validate; deploy on tag
- [ ] `template.yaml` skeleton with parameters
      `Stage`, `BdoRegion`, `UseRdsProxy`
- [ ] `samconfig.toml` for `dev` and `prod`
- [ ] `docs/adr/0001..0008-*.md` — Michael Nygard format, 1 page each
- [ ] `docs/runbook.md`, `docs/slo.md`, `docs/architecture.md`
- [ ] `.gitignore`, `LICENSE`, `README.md` outline

## Phase 2 — Network & data infrastructure

- [ ] `infra/network.yaml` — VPC, 2 private subnets, DynamoDB gateway
      endpoint, security groups
- [ ] `infra/data.yaml` — RDS Postgres (db `bdo`), DynamoDB
      `bdo-v3-items`, IAM auth role
- [ ] `migrations/` — Alembic init + `0001_initial.py`
      (`market_snapshot` + `market_daily`)
- [ ] CI step runs `alembic upgrade head` on deploy
- [ ] `scripts/seed_items.py` — one-time copy of 23 items from
      `bdo.accessory` → `bdo-v3-items`

## Phase 3 — Shared layer (`bdo-common`)

- [ ] `arsha_client.py` + normalizer — handles all 5 response shapes
- [ ] `models.py` — Pydantic v2 schemas
      (`Record`, `SnapshotRow`, `DailyRow`, `Item`)
- [ ] `db.py` — psycopg3 module-global connection helper, IAM-auth aware
- [ ] `dynamo.py` — typed wrappers for the items table
- [ ] `pricing.py` — `expected_enhance_cost(records, rates)`
- [ ] `analytics.py` — volatility (σ, CV), liquidity, z-score anomaly
- [ ] `rates.json` — placeholder enhancement probabilities, TODO-marked
- [ ] `config.py` — env reader (Powertools `parameters` cache)
- [ ] Unit tests for normalizer, pricing, analytics

## Phase 4 — ETL pipeline

- [ ] `src/functions/{retrieve_items,fetch_data,clean_data,store_data,
      rollup_daily,purge_old_snapshots}/app.py`
- [ ] `infra/etl.yaml` — Step Functions ASL (Map, Choice),
      EventBridge crons
- [ ] Integration test: full ETL run with moto + dockerised Postgres

## Phase 5 — APIs

- [ ] `src/functions/item_registry/app.py` (Powertools event handler)
- [ ] `src/functions/market_query/app.py`
      (snapshots, daily, analysis routes)
- [ ] `infra/api.yaml` — REST API GW, usage plan, API key, OpenAPI export
- [ ] CI step regenerates `infra/openapi.yaml` from Powertools handlers

## Phase 6 — Observability

- [ ] `infra/observability.yaml` — CloudWatch dashboard, alarms,
      log-group retention
- [ ] Verify X-Ray service map end-to-end against `dev` stack
- [ ] Smoke-test alarms (force ETL failure, force 5xx)

## Phase 7 — Cutover

- [ ] Deploy `dev` stack; soak for 24 h
- [ ] Deploy `prod` stack
- [ ] Tag `archive/main-v1`; rename `rewrite-project` →
      `archive/rewrite-project`
- [ ] Force-update `main` to `redesign-v3`
- [ ] `docs/cleanup-tasks.md` — list of legacy AWS resources to delete
      (old IAM users/roles/policies, old RDS, old DynamoDB tables);
      manual sign-off required per item
