# LLM Market Insights — Design

## Architecture

The crux is the **no-NAT VPC** (ADR-0006): in-VPC Lambdas can read RDS but have
no internet egress; Bedrock and the Discord webhook are only reachable from
outside the VPC. A Step Functions state machine therefore **splits the work**
across the VPC boundary — compute/store run in-VPC, summarise/publish run
outside (ADR-0015).

```
        EventBridge  (daily ~01:00 UTC; weekly Mon ~01:15 UTC), per region
                 |  {"region":"tw","period":"daily|weekly"}
                 v
      +-------------------------------------------------------+
      | StateMachine: bdo-${Stage}-insights                   |
      +-------------------------------------------------------+
      | ComputeDigest   (in-VPC)   RDS market_daily + item    |
      |    -> digest JSON + bedrock request payload           |
      | Summarize       (NOT in-VPC)  Bedrock InvokeModel     |
      |    -> structured narrative (schema-validated)         |
      | StoreSummary    (in-VPC)   upsert market_summary      |
      | Publish         SNS:Publish (native SF task)          |
      +-------------------------------------------------------+
                 |                                  |
                 v                                  v
        SNS bdo-${Stage}-insights        (failures: fallback + metric)
                 |
        +--------+---------+
        v                  v
  DiscordNotifier L   (optional email sub)
  (NOT in-VPC) -> Discord webhook

  Client --API key--> API Gateway --> marketQuery L (in-VPC)
                                        GET /v1/insights  (reads market_summary)
```

### New Lambdas

| Name             | Trigger        | In VPC | Reads / calls            | Writes              |
|------------------|----------------|--------|--------------------------|---------------------|
| computeDigest    | Step Functions | yes    | RDS `market_daily`,`item`| —                   |
| summarize        | Step Functions | no     | Bedrock InvokeModel      | —                   |
| storeSummary     | Step Functions | yes    | (input)                  | RDS `market_summary`|
| discordNotifier  | SNS            | no     | SSM (webhook URL), HTTPS | —                   |

`Publish` is a native Step Functions `SNS:Publish` task (no Lambda).
`marketQuery` gains the `/v1/insights` routes (already in-VPC, already wired to
API Gateway) rather than standing up a new API Lambda.

> **Why a `summarize` Lambda and not the native Bedrock SF integration?** The
> Lambda lets us assemble the prompt, validate the model's JSON against a
> Pydantic schema, and fall back deterministically — all unit-testable with a
> stubbed Bedrock client. The native integration would push prompt shape into
> ASL and make the guardrails (ADR-0016) hard to test. Trade-off accepted.

## Shared-layer additions (`bdo-common`)

Keeps logic in the layer (ADR-0003); the handlers stay thin.

- `insights/digest.py` — `build_digest(conn, *, region, period, date, top_n)`
  → `MarketDigest`. Pure selection/aggregation over repository results;
  no AWS imports beyond the injected `psycopg` connection.
- `insights/categories.py` — the **category registry**: `category -> handler`.
  `accessory` handler adds enhancement-cost movement (`pricing`); `buff`
  handler does price/vol/liquidity only. Adding a category = register a handler.
- `insights/prompt.py` — builds the Bedrock request (system + user messages)
  from a `MarketDigest`; pins low temperature and a bounded max-tokens.
- `insights/narrative.py` — Pydantic `Narrative` schema + parser + the
  deterministic fallback renderer.
- `models.py` — add `MarketDigest`, `DigestEntry`, `Narrative`, `MarketSummary`.
- `repositories.py` — `InsightRepo`:
  - `top_movers(conn, *, region, category, period, date, limit)` — joins
    `item` (category) with `market_daily`; latest day vs prior (daily) or
    7-day window (weekly); ranks by |close % change|.
  - `SummaryRepo.upsert(...)` / `get(region, period, date|latest, lang)`.

## Data model

New Alembic migration `0004_market_summary.py`:

```sql
CREATE TABLE market_summary (
    region        VARCHAR(16) NOT NULL,
    period        VARCHAR(8)  NOT NULL,           -- 'daily' | 'weekly'
    summary_date  DATE        NOT NULL,           -- the day/week-ending date
    lang          VARCHAR(8)  NOT NULL DEFAULT 'en',
    model_id      TEXT        NOT NULL,
    digest        JSONB       NOT NULL,           -- authoritative structured facts
    narrative     JSONB       NOT NULL,           -- {headline, categories[], overall}
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (region, period, summary_date, lang)
);
CREATE INDEX ON market_summary (region, period, summary_date DESC);  -- "latest"
```

Volume is tiny (a few rows/day), so summaries are retained indefinitely; an
optional purge mirrors `purge_old_snapshots` if ever needed. `lang` is in the
PK so additional languages are additive (NFR reserved path).

## API (`/v1/insights`)

Added to `src/functions/market_query/app.py` (in-VPC, RDS). Typed Powertools
`Query` params (consistent with the recently-merged query-param work), so the
routes self-document in `infra/openapi.yaml` / Swagger UI:

```
GET /v1/insights?region=<enum>&period=<daily|weekly>&date=<YYYY-MM-DD?>&lang=en
```

- `region` — same enum as the market routes; default `tw`.
- `period` — `Literal["daily","weekly"]`; default `daily`.
- `date` — optional ISO date; omitted → most recent for `(region, period)`.
- Response: `{ region, period, summary_date, lang, model_id, digest, narrative }`
  — the structured `digest` carries the authoritative numbers (NFR-4).

## Bedrock

- Model id is a SAM parameter `BedrockModelId` (a low-cost model, e.g. an
  Amazon Nova or Claude Haiku-class model); default chosen at implementation.
- `summarize` role: `bedrock:InvokeModel` on the specific model ARN only
  (least privilege, NFR-10). Low temperature; bounded max tokens; the prompt
  caps input to top-N per category so token cost stays in cents/month.
- Model **enablement** in the account/region is a one-time prerequisite
  (runbook note); us-east-1 is used throughout.

## Prompt & guardrails (ADR-0016)

- The user message contains only the digest JSON, framed as the *complete set
  of facts*; the system message instructs: trader-style, concise, English,
  **never invent or recompute numbers**, output **only** the JSON schema.
- `summarize` parses the model output into the `Narrative` Pydantic schema. On
  parse/validation failure (or a Bedrock error after retries), `storeSummary`
  uses the deterministic fallback renderer over the same digest.
- The API returns the digest beside the prose, so a consumer can always show
  authoritative figures regardless of the narrative.

## Delivery

- State machine ends with a native `SNS:Publish` to `bdo-${Stage}-insights`
  (message = headline + a compact rendering; full summary via the API).
- `discordNotifier` (out-of-VPC) subscribes to the topic, reads the webhook URL
  from SSM SecureString (`/bdo/${Stage}/discord-webhook`) via the Powertools
  parameters cache, and POSTs a formatted message. Failure → log + metric, no
  raise. Optional email subscription mirrors the alarm topic pattern.

## Observability

- Powertools `Metrics` (EMF, `BdoMarket/*`): `SummariesGenerated`,
  `InsightFailures`, `DiscordDeliveryFailures`. `Tracer` on each handler;
  correlation id from the Step Functions execution name (NFR-5 of v3).
- Optional dashboard widget + a low-priority alarm on `InsightFailures`
  (added to `infra/observability.yaml`).

## Infrastructure

New nested stack `infra/insights.yaml` (own concern; structure.md convention):
the three task Lambdas + `discordNotifier`, the state machine, the two
EventBridge schedules, the SNS topic + subscription, and IAM (least-privilege:
`bedrock:InvokeModel`, `rds-db:connect` for in-VPC ones, `ssm:GetParameter`,
`sns:Publish`). Wired into `template.yaml` like the other nested stacks; in-VPC
functions reuse the existing Lambda SG / private subnet / IAM-auth role params.

## Testing

- `build_digest` + category handlers: unit tests with a fake repo; integration
  test against ephemeral Postgres (existing CI Postgres service).
- `summarize`: stubbed Bedrock client — assert prompt assembly, schema parsing,
  and the deterministic fallback path.
- `storeSummary` / `InsightRepo`: mocked connection + integration upsert.
- `discordNotifier`: mocked HTTPS POST; assert no raise on failure.
- `/v1/insights`: handler tests mirroring `tests/unit/test_market_query_handler.py`.

## Key decisions (ADR pointers)

| ADR  | Decision                                                              |
|------|-----------------------------------------------------------------------|
| 0015 | LLM = Amazon Bedrock; summarise out-of-VPC (no NAT) via SF split      |
| 0016 | Deterministic digest; LLM narrates only; structured-output + fallback |
| 0012 | (reused) registry pattern — category registry mirrors it             |
| 0006 | (constraint) no-NAT VPC drives the in/out-of-VPC split               |
