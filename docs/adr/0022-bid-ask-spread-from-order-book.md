# ADR-0022: Bid-ask spread sourced from the central-market order book

## Status

Proposed — design only; not implemented. Gated on the ingestion-cost decision
in "Consequences / open question" below.

## Context

A per-`(item_id, sid)` **bid-ask spread** is a useful liquidity signal (a wide
or one-sided book means a thin market). The platform does not expose one today,
and it **cannot be derived from data currently stored**:

- `item_sid.price_min` / `price_max` are the central market's **enforced
  price-limit band** — the min/max price the game permits an item to be listed
  or pre-ordered at, a regulated range around the base price. They are *not* a
  live best-bid/best-ask, so `(price_max - price_min) / price_min` measures the
  width of the price cap, not market spread.
- The ETL ingests only `GetWorldMarketSubList` (base price, stock, cumulative
  trades, and that price-limit band), batched at ≤ 50 ids per call.

The real bid/ask lives in the central-market **order book**, a separate
endpoint (`GetItemSellBuyInfo`; arsha's `GetBiddingInfoList`) returning a price
ladder of `{ price, buyCount, sellCount }` per `(item_id, sid)`:

- **best bid** = highest price with `buyCount > 0`
- **best ask** = lowest price with `sellCount > 0`
- **spread** = `(best_ask - best_bid) / best_bid`

In practice the book is frequently **one-sided** (only buy orders, or only sell
orders) at any given moment, so the spread is often undefined.

## Decision (proposed)

Source the spread from the order-book endpoint:

1. **Ingest** the order book per tracked `(item_id, sid)` on a schedule, deriving
   `best_bid` / `best_ask` (and optionally a compact top-of-book snapshot).
2. **Store** them (new columns or a small order-book table keyed by
   `(region, item_id, sid, captured_at)`).
3. **Compute** `spread_pct = (best_ask - best_bid) / best_bid * 100` (1 dp);
   emit `null` when the book is one-sided or empty.
4. **Expose** `spread_pct` on the analysis response (and/or a dedicated
   order-book endpoint), additive and nullable.

## Consequences / open question

- (+) A genuine liquidity metric, with one canonical definition and explicit
  `null` for the common one-sided case.
- (−) **New ingestion cost — the crux decision.** Unlike the batched
  `GetWorldMarketSubList` (≤ 50 ids/call), the order book is **one call per
  `(item_id, sid)`** — a per-item fan-out that materially increases request
  volume against the usage plan (~5 RPS / 1000 req/day; ADR-0005). This must be
  sized and bounded before building. Options to weigh (deferred to the spec):
  - a **reduced cadence** (e.g. daily, not hourly) for the tracked set;
  - a **narrowed scope** (only high-value / high-interest items);
  - **on-demand + cache** rather than a scheduled sweep.
- (−) New storage + retention for order-book data, and the one-sided/empty
  handling.

Because the cost/scope is unresolved, this ADR stays **Proposed** until we pick
an ingestion strategy. It records the correct data source so the earlier,
incorrect price-band approach is not retried.

## Notes

Spec: `.kiro/specs/market-bid-ask-spread/`. Supersedes nothing (the price-band
`spread_pct` explored earlier was withdrawn before shipping). The item-icon-URL
work shipped separately (ADR-0021).
