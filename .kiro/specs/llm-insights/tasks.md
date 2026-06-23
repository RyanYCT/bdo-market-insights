# LLM Market Insights — Implementation Tasks

Feature branches off `main`; one commit per box (tick its checkbox in the same
commit); open a PR per phase. Phases are checkpoints — no phase ships
half-built. Sequence is **value-first / daily-first**: a queryable product
lands by Phase 2 using the deterministic fallback narrative (ADR-0016), and the
LLM (Phase 3) then upgrades the prose. Domain/analytics reuse is specified in
`.kiro/specs/v3/domain-model.md`; new decisions in ADR-0015 / ADR-0016.

## Phase 0 — Spec & ADRs

- [x] `.kiro/specs/llm-insights/{requirements,design,tasks}.md`
- [x] `docs/adr/0015-llm-bedrock-out-of-vpc.md`
- [x] `docs/adr/0016-deterministic-digest-llm-narration.md`
- [x] `log.md` entry for the planning session

## Phase 1 — Deterministic digest core (no infra, no LLM)

The engine, fully unit/integration-tested before any Lambda or Bedrock wiring.

- [x] `0004_market_summary.py` Alembic migration (`market_summary` table)
- [x] `models.py` — `MarketDigest`, `DigestEntry`, `Narrative`, `MarketSummary`
- [x] `repositories.py` — `InsightRepo.top_movers` (item⋈market_daily, daily &
      weekly windows) + `SummaryRepo.upsert` / `get`
- [x] `bdo_common/insights/categories.py` — category registry (`accessory`
      with enhancement-cost movement via `pricing`; `buff` price/vol/liq only)
- [x] `bdo_common/insights/digest.py` — `build_digest(...)` over the registry
- [x] `bdo_common/insights/narrative.py` — `Narrative` schema + the
      deterministic fallback renderer (doubles as Phase 2's narrative)
- [x] Unit tests (fake repo) + integration test (ephemeral Postgres)

## Phase 2 — Generate, store & serve (deterministic narrative; daily; tw)

Ships a queryable product: structured digest + deterministic prose, no LLM yet.

- [x] `src/functions/insights_compute/app.py` (in-VPC) — `build_digest`
- [x] `src/functions/insights_store/app.py` (in-VPC) — render the deterministic
      narrative + upsert `market_summary`
- [x] `infra/insights.yaml` — state machine `ComputeDigest → StoreSummary`,
      EventBridge **daily** schedule, IAM (`rds-db:connect`); wire into
      `template.yaml`
- [x] `/v1/insights` routes in `src/functions/market_query/app.py` (typed Query:
      region enum, `period`, `date`, `lang`); regenerate `infra/openapi.yaml`;
      handler tests
- [x] README: document the `/v1/insights` route + params

## Phase 3 — LLM narrative (summarize Lambda, Bedrock Converse; daily)

- [x] `bdo_common/insights/prompt.py` — Converse request from a `MarketDigest`
- [x] `src/functions/insights_summarize/app.py` (out-of-VPC) — Bedrock
      **Converse** call + `Narrative` schema validation
- [x] Insert the `Summarize` state between `ComputeDigest` and `StoreSummary`;
      `StoreSummary` prefers the LLM narrative, falls back to deterministic on
      failure
- [x] `BedrockModelId` param (Amazon Nova or Anthropic Claude) + least-privilege
      `bedrock:Converse` on the model ARN
- [x] Unit tests (stubbed Bedrock client; assert fallback path)

## Phase 4 — Weekly cadence

- [x] Weekly EventBridge rule (`period=weekly`); confirm the period flows
      end-to-end (weekly window selection in `build_digest`)
- [x] Tests for the weekly window

## Phase 5 — Delivery (SNS + Discord) & observability

- [x] SNS topic `bdo-${Stage}-insights` + native `SNS:Publish` state (+ optional
      email sub) in `infra/insights.yaml`
- [x] `src/functions/insights_discord/app.py` (out-of-VPC, SNS-subscribed) —
      webhook URL from SSM SecureString; POST; no-raise on failure
- [x] EMF metrics (`SummariesGenerated`, `InsightFailures`,
      `DiscordDeliveryFailures`); dashboard widget + `InsightFailures` alarm
      in `infra/observability.yaml`
- [x] Tests: mocked HTTPS POST (no raise on failure)

## Phase 6 — Enablement & docs

- [x] Runbook: Bedrock model enablement (account/region), the
      `/bdo/${Stage}/discord-webhook` SSM param, region activation for insights
- [x] `docs/architecture.md` update (insights stack + data flow)
- [ ] Deploy `dev`, verify a daily + weekly run end-to-end; then `prod`
      (operator action — procedure + smoke test in the "Market Insights"
      section of docs/runbook.md)

## Reserved (out of scope; upgrade paths in requirements.md)

- **Collapse `summarize` into a native `bedrock:invokeModel` SF task** once the
  prompt/output schema are stable (ADR-0015) — prompt/parse already live in the
  shared layer, so the swap is localised
- Game-version / patch-note context block in the digest
- Additional languages (the `lang` column is reserved)
- Categories beyond accessory/buff (registry is reserved)
- Multi-region scheduling (region-aware; add an EventBridge rule)
