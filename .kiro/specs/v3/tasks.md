# v3 — Implementation Tasks

Each box is one commit on `redesign-v3` (tick its checkbox in the same
commit). Phases are checkpoints; no phase ships half-built. `main` is
force-replaced from `redesign-v3` at Phase 7 cutover (no per-phase PRs).

## Phase 0 — Branch & specs

- [x] Specs at `.kiro/specs/v3/{requirements,design,tasks}.md`
- [x] First commit on `redesign-v3` (orphan branch)
- [x] `AGENTS.md` operating manual for AI coding agents
- [x] `.kiro/steering/{product,tech,structure}.md` auto-loaded
      context files
- [x] `log.md` append-only session journal with Phase 0 entry

Branch-archive operations (`tag archive/main-v1`, rename
`rewrite-project` → `archive/rewrite-project`) are deliberately
deferred to Phase 7 — cutover — and are listed there.

## Phase 1 — Project scaffolding

- [x] `pyproject.toml` (Python 3.12; uv; ruff, mypy, pytest,
      aws-lambda-powertools, pydantic v2, psycopg[binary], boto3)
- [x] `Makefile` (build / test / lint / deploy / db-tunnel-up /
      db-tunnel-down)
- [x] `.github/workflows/ci.yml` — single workflow:
      ruff → mypy → pytest, plus pip-audit and bandit (NFR-12);
      deploy on tag with OIDC. (`sam validate` deferred to Phase 2
      once nested infra/*.yaml templates exist.)
- [x] `template.yaml` skeleton with parameters
      `Stage`, `BdoRegion`, `UseRdsProxy`, `EnableBastion`
- [x] `samconfig.toml` for `dev` and `prod`
- [x] `docs/adr/0001..0011-*.md` — Michael Nygard format, 1 page each
- [x] `docs/runbook.md` (incl. db-tunnel runbook), `docs/slo.md`,
      `docs/architecture.md`
- [x] `.gitignore`, `LICENSE`, `README.md` outline

## Phase 2 — Network, data, and bastion infrastructure

- [x] `infra/network.yaml` — VPC, 2 private subnets, DynamoDB gateway
      endpoint, security groups (Lambda SG, RDS SG, bastion SG)
- [x] `infra/data.yaml` — RDS Postgres (db `bdo`), DynamoDB
      `bdo-v3-items`, IAM auth role, Secrets Manager entry for `dba`
      role
- [x] `infra/bastion.yaml` — gated by `EnableBastion`; t4g.nano in
      private subnet; EC2 Instance Connect Endpoint
- [x] `migrations/` — Alembic init + `0001_initial.py`
      (`item`, `item_sid`, `market_snapshot`, `market_daily`) +
      `0002_bootstrap_roles.py` (`lambda_rds_user` IAM, `dba` login)
- [x] Migrations run via bastion tunnel (`make migrate`); the CI
      deploy job does not run them (private RDS is unreachable from a
      GitHub runner). In-VPC migrator Lambda deferred to Phase 4.
- [x] `scripts/seed_items.py` — one-time copy of 23 items from
      `bdo.accessory` → `bdo-v3-items` (Postgres seeds itself via ETL)
- [x] Restore `sam validate` step in `.github/workflows/ci.yml` now
      that nested infra templates exist

## Phase 3 — Shared layer (`bdo-common`)

Domain math is specified normatively in `.kiro/specs/v3/domain-model.md`.

- [x] `arsha_client.py` + normalizer — handles all 5 response shapes
- [x] `models.py` — Pydantic v2 schemas
      (`Record`, `Item` incl. `model_id`/`cron_table`, `ItemSid`,
      `SnapshotRow`, `DailyRow`)
- [x] `db.py` — psycopg3 module-global connection helper, IAM-auth aware
- [x] `dynamo.py` — typed wrappers for `bdo-v3-items`
- [x] `repositories.py` — `ItemRepo`, `ItemSidRepo`, `SnapshotRepo`,
      `DailyRepo` (parameterized SQL, no ORM)
- [x] `pricing.py` — model registry (ADR-0012); `accessory_v1` (A1)
      with `success_probability`, `expected_enhance_cost`, `net_rate`
      (tax), and `enhancement_analysis` (per-tier output + verdict)
- [x] `analytics.py` — volatility (σ, CV), liquidity, z-score anomaly
- [x] `rates.json` — accessory_v1 curves + cron tables + tax constants
- [x] `config.py` — env reader (Powertools `parameters` cache)
- [x] Unit tests for normalizer, pricing (assert domain-model.md worked
      numbers), analytics, repositories

## Phase 4 — ETL pipeline

- [x] `src/functions/{retrieve_items,fetch_data,clean_data,store_data,
      rollup_daily,purge_old_snapshots}/app.py`
- [x] `retrieveItems` projects full DynamoDB metadata into the Step
      Functions input (id, name, category, …)
- [x] `storeData` upserts `item` and `item_sid`, then bulk-inserts
      `market_snapshot`, in one transaction
- [x] `infra/etl.yaml` — Step Functions ASL (Map, Choice),
      EventBridge crons (ETL hourly with `region` input;
      retention daily 00:30 UTC)
- [x] Integration test: full ETL run with moto + dockerised Postgres
- [x] In-VPC migrator Lambda — runs `alembic upgrade head` from inside
      the VPC on deploy (replaces the manual bastion-tunnel migration
      for routine schema changes; see Phase 2 notes)
- [x] `accessory_cron_v1` pricing model — Markov chain (60% retain /
      40% drop-one on cron failure), cron counts from `rates.json`
      tables a/b; registered alongside `accessory_v1` (ADR-0012)

## Phase 5 — APIs

- [x] `src/functions/item_registry/app.py` — Powertools event handler;
      writes only to DynamoDB; arsha.io ID validation on POST
- [x] `src/functions/market_query/app.py` — joins `item`, `item_sid`,
      `market_snapshot` for snapshots/daily/analysis routes
- [x] `infra/api.yaml` — REST API GW, usage plan, API key, OpenAPI export
- [x] CI step regenerates `infra/openapi.yaml` from Powertools handlers

## Phase 6 — Observability

- [ ] `infra/observability.yaml` — CloudWatch dashboard, alarms,
      log-group retention
- [ ] Verify X-Ray service map end-to-end against `dev` stack
- [ ] Smoke-test alarms (force ETL failure, force 5xx)

## Phase 7 — Cutover

- [ ] Deploy `dev` stack; soak for 24 h
- [ ] Deploy `prod` stack
- [ ] Tag `archive/main-v1` at `origin/main`
- [ ] Rename remote branch `rewrite-project` →
      `archive/rewrite-project`
- [ ] Force-update `main` to `redesign-v3`
- [ ] `docs/cleanup-tasks.md` — list of legacy AWS resources to delete
      (old IAM users/roles/policies, old RDS, old DynamoDB tables);
      manual sign-off required per item
