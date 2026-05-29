---
inclusion: always
---

# Product

`bdo-market-insights` — a serverless market-data platform for Black
Desert Online (BDO). Hourly ETL ingests price and stock data from
arsha.io into RDS Postgres; an API exposes raw snapshots, daily
rollups, and BDO-domain analytics (per-tier expected enhancement
cost, volatility, liquidity, anomaly flags).

## Audience

Single user (the repository owner), low traffic, cost-sensitive. Also
a portfolio piece — choices should read as deliberate enterprise
patterns done at appropriate scale, not as ceremony for its own sake.

## BDO domain primer

- BDO has a centralised market with system-set price floors/ceilings
  (`price_min`, `price_max`); `last_sold_price` is the actual trade
  price; `total_trades` is a lifetime cumulative count.
- Items have a `sid` (sub-id) representing enhancement level:
  0 = base, 1 = PRI (+1), 2 = DUO (+2), 3 = TRI, 4 = TET, 5 = PEN, …
  Cap depends on the item and the game version.
- **Accessory enhancement**: enhancing `(item, sid)` to
  `(item, sid+1)` consumes one `(item, sid=0)` (the "clean" version)
  per attempt, with a probability of success that depends on
  enhancement level. Failure destroys the target item.
- Expected cost per attempted upgrade ≈
  `price(item, sid) + price(item, 0)`; expected total cost to land
  `sid+1` is that divided by the per-attempt success probability.
- The product computes this per-tier — that is the project's reason
  to exist. **Do not** replace it with generic statistics; the
  previous rewrite did and the project lost its purpose.

## Source data

- `https://api.arsha.io/v2/{region}/GetWorldMarketSubList?id=<csv>` —
  primary endpoint. Polymorphic response (5 shapes); the
  `bdo_common.arsha_client` normalizer flattens them all into a
  single `list[Record]`.
- Default region: `tw`. Schema and pipeline are region-aware so
  `kr`, `eu`, `na`, … can be activated by adding an EventBridge rule.

## Active spec

`.kiro/specs/v3/{requirements,design,tasks}.md` is the source of
truth. Read it before changing scope.
