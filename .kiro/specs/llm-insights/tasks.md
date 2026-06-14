# LLM Market Insights — Implementation Tasks

Feature branches off `main`; one commit per box (tick its checkbox in the same
commit); open a PR per phase. Phases are checkpoints — no phase ships
half-built. Domain/analytics reuse is specified in
`.kiro/specs/v3/domain-model.md`; new decisions in ADR-0015 / ADR-0016.

## Phase 0 — Spec & ADRs

- [x] `.kiro/specs/llm-insights/{requirements,design,tasks}.md`
- [x] `docs/adr/0015-llm-bedrock-out-of-vpc.md`
- [x] `docs/adr/0016-deterministic-digest-llm-narration.md`
- [x] `log.md` entry for the planning session

## Phase 1 — Deterministic digest (no LLM)

- [ ] `0004_market_summary.py` Alembic migration (`market_summary` table)
- [ ] `models.py` — `MarketDigest`, `DigestEntry`, `Narrative`, `MarketSummary`
- [ ] `repositories.py` — `InsightRepo.top_movers` (item⋈market_daily, daily &
      weekly windows) + `SummaryRepo.upsert` / `get`
- [ ] `bdo_common/insights/categories.py` — category registry (`accessory`
      with enhancement-cost movement via `pricing`; `buff` price/vol/liq only)
- [ ] `bdo_common/insights/digest.py` — `build_digest(...)` over the registry
- [ ] Unit tests (fake repo) + integration test (ephemeral Postgres)

## Phase 2 — Summarisation, storage, scheduling (tw, daily + weekly)

- [ ] `bdo_common/insights/prompt.py` — Bedrock request from a `MarketDigest`
- [ ] `bdo_common/insights/narrative.py` — `Narrative` parser + deterministic
      fallback renderer
- [ ] `src/functions/insights_compute/app.py` (in-VPC) — digest + prompt payload
- [ ] `src/functions/insights_summarize/app.py` (out-of-VPC) — Bedrock invoke +
      schema validation
- [ ] `src/functions/insights_store/app.py` (in-VPC) — upsert `market_summary`
- [ ] `infra/insights.yaml` — state machine (Compute→Summarize→Store→SNS:Publish),
      EventBridge daily + weekly schedules, IAM (bedrock:InvokeModel least-priv),
      `BedrockModelId` param; wire into `template.yaml`
- [ ] Unit tests (stubbed Bedrock client; fallback path)

## Phase 3 — API

- [ ] `/v1/insights` routes in `src/functions/market_query/app.py` (typed
      Query params: region enum, `period`, `date`, `lang`)
- [ ] Regenerate `infra/openapi.yaml`; handler tests
- [ ] README: document the `/v1/insights` route + params

## Phase 4 — Delivery (SNS + Discord) & observability

- [ ] SNS topic `bdo-${Stage}-insights` (+ optional email sub) in
      `infra/insights.yaml`
- [ ] `src/functions/insights_discord/app.py` (out-of-VPC, SNS-subscribed) —
      webhook URL from SSM SecureString; POST; no-raise on failure
- [ ] EMF metrics (`SummariesGenerated`, `InsightFailures`,
      `DiscordDeliveryFailures`); dashboard widget + `InsightFailures` alarm
      in `infra/observability.yaml`
- [ ] Tests: mocked HTTPS POST (no raise on failure)

## Phase 5 — Enablement & docs

- [ ] Track `buff` items in the registry (seed or `POST /v1/items`); confirm
      ETL ingests them with `category=buff` (no ETL change expected)
- [ ] Runbook: Bedrock model enablement (account/region), the
      `/bdo/${Stage}/discord-webhook` SSM param, region activation for insights
- [ ] `docs/architecture.md` update (insights stack + data flow)
- [ ] Deploy `dev`, verify a daily + weekly run end-to-end; then `prod`

## Reserved (out of scope; upgrade paths in requirements.md)

- Game-version / patch-note context block in the digest
- Additional languages (the `lang` column is reserved)
- Categories beyond accessory/buff (registry is reserved)
- Multi-region scheduling (region-aware; add an EventBridge rule)
