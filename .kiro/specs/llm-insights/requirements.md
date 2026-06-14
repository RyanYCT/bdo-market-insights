# LLM Market Insights — Requirements

## Product

A scheduled feature that turns the existing daily market data into
**concise, trader-style natural-language summaries** — daily and weekly —
and makes them available three ways: stored in RDS, served over the API,
and pushed to subscribers (SNS, with a Discord relay).

Summaries are **market-wide digests**, organised by item **category**, each
reporting the **top-N** notable items. Two categories ship first —
`accessory` and `buff` — and the design keeps adding more a config-only change.

This builds directly on the analytics the platform already computes
(`bdo_common.analytics`, `bdo_common.pricing`); the LLM only narrates a
pre-computed, deterministic digest (see ADR-0016). Default region: `tw`
(region-aware, like the ETL).

## Functional Requirements

### Digest computation (deterministic)

- **FR-1** A digest builder computes a structured, market-wide digest from
  `market_daily` (+ `item.category`) for a given `(region, period, date)`,
  where `period ∈ {daily, weekly}` (daily = latest trade day vs prior;
  weekly = trailing 7 days).
- **FR-2** The digest is grouped by category and, per category, surfaces the
  **top-N** items by notability: largest close-price moves (gainers/losers),
  anomalies (`analytics.detect_anomaly`), and volatility/liquidity shifts
  (`analytics.daily_volatility` / `daily_liquidity`). `N` is configurable
  (default 5).
- **FR-3** Category behaviour is pluggable via a **category registry**
  (mirrors the pricing-model registry, ADR-0012):
  - `accessory` — additionally reports expected enhancement-cost movement via
    `pricing.enhancement_analysis` (per-tier, base-rate `accessory_v1`).
  - `buff` — price / volatility / liquidity / anomaly only (no enhancement).
- **FR-4** All numeric facts in the digest are computed by `bdo_common`; the
  digest is a self-contained, JSON-serialisable object (ADR-0016).

### Summarisation (LLM)

- **FR-5** A summariser sends the digest (as grounded facts) to Amazon
  Bedrock and returns a **structured** narrative (headline, per-category
  bullets, overall note) validated against a Pydantic schema.
- **FR-6** The LLM must narrate only figures present in the digest; the prompt
  forbids inventing numbers. On invalid/unavailable model output the pipeline
  falls back to a **deterministic template** narrative so a summary is always
  produced (ADR-0016).
- **FR-7** Summaries are English, concise, trader-style.

### Storage

- **FR-8** Each run upserts one `market_summary` row keyed on
  `(region, period, summary_date, lang)` (idempotent / re-runnable). The row
  stores both the structured `digest` and the `narrative`, plus `model_id`.

### API (`/v1/insights`)

- **FR-9** `GET /v1/insights?region=&period=&date=&lang=` returns a stored
  summary (digest + narrative); `date` omitted returns the most recent for
  that `(region, period)`. Typed query params (region enum, `period ∈
  {daily, weekly}`), part of the OpenAPI contract.
- **FR-10** All `/v1/insights*` routes require an API key (existing usage
  plan, FR-16/17 of the v3 spec).

### Scheduling and delivery

- **FR-11** EventBridge triggers a daily digest (after the daily rollup is
  available) and a weekly digest, per active region, with input
  `{ "region": "<r>", "period": "daily|weekly" }`.
- **FR-12** On completion the summary is published to an SNS topic
  `bdo-${Stage}-insights`; a Discord-relay subscriber posts it to a Discord
  webhook. Delivery failures are logged/metered but never fail the run.

## Non-Functional Requirements

- **NFR-1 (Cost)** Holds the ≤ US$15/month incremental line. Summarisation
  runs **outside the VPC** so it needs no NAT and no VPC endpoint (ADR-0006,
  ADR-0015); Bedrock usage is ~2 small calls/day → cents/month.
- **NFR-2 (Security)** No secrets in the repo. The Discord webhook URL lives in
  SSM (SecureString) / Secrets Manager, read via the Powertools parameters
  cache. Bedrock access is IAM, least-privilege to the chosen model ARN
  (no static keys).
- **NFR-3 (Non-critical / isolation)** The insights pipeline must not affect
  ETL or the query API. Bedrock/Discord failures degrade gracefully
  (fallback narrative; run still stores a digest).
- **NFR-4 (Trust)** No hallucinated numbers reach a consumer: the API always
  returns the authoritative structured digest alongside the prose (ADR-0016).
- **NFR-5 (Observability)** EMF metrics in `BdoMarket/*`
  (`SummariesGenerated`, `InsightFailures`); structured logs with the run's
  correlation id; optional dashboard widget + failure alarm.
- **NFR-6 (Consistency)** Reuses existing patterns: Step Functions + EventBridge
  (ADR-0004), shared `bdo-common` layer (ADR-0003), Powertools (ADR-0007),
  IAM DB auth (ADR-0008), typed Powertools `Query` params, SAM nested stack.

## Out of scope (reserved upgrade paths)

- **Game-version / patch-note aggregation** to explain and speculate on moves
  — reserved; the digest schema can carry an external-context block later.
- **Additional languages** — reserved via the `lang` column (default `en`);
  depends on a localised item-name source and storage scheme.
- **Additional categories** beyond `accessory` / `buff` — reserved via the
  category registry (config-only addition once items are tracked).
- **Multi-region insights** — region-aware from day one; only `tw` is
  scheduled initially (add an EventBridge rule to activate another region).
- **Richer delivery** (email digest formatting, web view) — SNS fan-out leaves
  this open.
