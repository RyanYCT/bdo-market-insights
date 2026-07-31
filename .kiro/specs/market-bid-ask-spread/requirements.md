# Market bid-ask spread — Requirements

> **Design only — not scheduled for implementation.** Gated on the
> ingestion-cost decision (ADR-0022). This spec records the correct data source
> and the open cost/scope question so the feature can be sized before any code.

## Product

Expose a per-`(item_id, sid)` **bid-ask spread** — a liquidity signal derived
from the central-market order book. Wide or one-sided books indicate thin
markets. The field is additive and nullable (`null` when the book is one-sided
or empty, which is common).

## Why the current data can't provide it

`item_sid.price_min` / `price_max` are the **enforced price-limit band** (the
min/max price the game permits), not a live bid/ask — so they cannot produce a
meaningful spread. The real bid/ask requires the order book (below), which the
ETL does not currently ingest.

## Functional Requirements

- **FR-1** Ingest the central-market order book per tracked `(item_id, sid)`
  from the order-book endpoint (`GetItemSellBuyInfo` / arsha
  `GetBiddingInfoList`), a ladder of `{ price, buyCount, sellCount }`.
- **FR-2** Derive **best bid** = highest price with `buyCount > 0`, **best ask**
  = lowest price with `sellCount > 0`.
- **FR-3** Compute `spread_pct = (best_ask - best_bid) / best_bid * 100`
  (1 dp) via a single canonical definition; consumers do not re-derive it.
- **FR-4** WHEN the book is one-sided (only bids or only asks) or empty, emit
  `spread_pct: null` (not `0`).
- **FR-5** Expose `spread_pct` additively/nullable (on the analysis response
  and/or a dedicated order-book endpoint); regenerate OpenAPI as needed.
- **FR-6** Persist `best_bid` / `best_ask` (and optionally a compact
  top-of-book) with a capture timestamp, so reads do not hit the game API.

## Non-Functional Requirements — cost is the gating constraint

- **NFR-1 (ingestion cost)** The order book is **one request per
  `(item_id, sid)`** — a per-item fan-out, unlike the batched
  `GetWorldMarketSubList` (≤ 50 ids/call). Total volume MUST stay within the
  usage plan (~5 RPS / 1000 req/day; ADR-0005). This bounds cadence and scope
  (see the open question) and MUST be decided before implementation.
- **NFR-2 (storage/retention)** Order-book captures need a retention policy
  consistent with the existing snapshot sweep (FR-7 of the v3 spec).
- **NFR-3 (explicit nulls)** One-sided/empty books are first-class `null`.

## Open question (blocking; decide before building)

Pick an ingestion strategy that fits the usage plan:

1. **Reduced cadence** — snapshot the tracked set daily (not hourly).
2. **Narrowed scope** — only high-value / high-interest items.
3. **On-demand + cache** — fetch per request with a TTL cache, no scheduled
   sweep.

Each trades freshness against request volume differently; the choice drives the
ETL/schedule and storage design.

## Out of scope

- Full order-book depth analytics (this feature only needs top-of-book for the
  spread). Deeper book metrics could be a later extension.
