# Market bid-ask spread — Design (draft)

> Draft; not implemented. Finalize once the ingestion strategy is chosen
> (requirements.md "Open question"; ADR-0022).

## Data source

Central-market order book, per `(item_id, sid)`:

- Endpoint: `GetItemSellBuyInfo` (official) / `GetBiddingInfoList` (arsha),
  returning a price ladder `[{ price, buyCount, sellCount }, ...]`.
- **best_bid** = max `price` where `buyCount > 0`; **best_ask** = min `price`
  where `sellCount > 0`.

Example (illustrative — one-sided books are common):

```jsonc
// only sell orders present -> no bid -> spread null
[{ "price": 200000000, "buyCount": 0, "sellCount": 1 },
 { "price": 203000000, "buyCount": 0, "sellCount": 2 }]
// best_ask = 200000000, best_bid = None  -> spread_pct = null
```

## Formula

```
spread_pct = (best_ask - best_bid) / best_bid * 100     # rounded to 1 dp
spread_pct = None                                       # one-sided or empty book
```

A pure helper (e.g. `analytics.spread_pct(best_bid, best_ask)`) keeps one
definition, asserted by unit tests.

## Ingestion (depends on the chosen strategy)

The order book is a **per-item** call, so the ingest path is separate from the
batched `GetWorldMarketSubList` poll. Sketch, per the option chosen in
requirements.md:

- **Reduced cadence:** an EventBridge schedule (e.g. daily) → a Lambda that
  walks the tracked set, fetching the book per `(item_id, sid)` with the
  existing arsha client rate limits (≤ 5 RPS, ≤ 1 RPS/worker), and upserts
  best_bid/best_ask.
- **Narrowed scope:** the same, restricted to a curated high-interest subset.
- **On-demand + cache:** resolve on the read path with a TTL cache; no schedule,
  but read latency and cache-miss fan-out must respect the plan.

## Storage (sketch)

A small table keyed by `(region, item_id, sid, captured_at)` holding
`best_bid`, `best_ask` (and optionally a compact top-of-book), with a retention
sweep matching the snapshot policy. Alternatively, latest-only columns on a
per-`(region, item_id, sid)` row if history isn't needed.

## Exposure

`spread_pct` (nullable) on the analysis response and/or a dedicated order-book
endpoint. Additive; regenerate `infra/openapi.yaml` if it lands on a typed
response model.

## Tasks (deferred)

Enumerated once the ingestion strategy is chosen; no work is scheduled yet.
