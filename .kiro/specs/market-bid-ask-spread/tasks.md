# Market bid-ask spread — Implementation Tasks

> **Not scheduled.** Design only (ADR-0022 is *Proposed*). Tasks are enumerated
> once the ingestion strategy is chosen (requirements.md "Open question").

## Blocked on decision

- [ ] Choose the ingestion strategy: reduced cadence / narrowed scope /
      on-demand+cache (sizes request volume against the usage plan — ADR-0005)

## Then (sketch, to be detailed after the decision)

- [ ] Order-book fetch in the arsha client (`GetBiddingInfoList`), per
      `(item_id, sid)`, honoring existing rate limits
- [ ] Derive + persist `best_bid` / `best_ask` (+ retention)
- [ ] `analytics.spread_pct(best_bid, best_ask)` pure helper + tests
- [ ] Expose `spread_pct` (nullable) on the analysis response / order-book
      endpoint; regenerate OpenAPI if typed
- [ ] `ruff` + `mypy` + `pytest` + `bandit` + `sam validate --lint`
