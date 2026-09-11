---
inclusion: always
---

# Product

`bdo-market-insights` — a serverless market-data platform for Black
Desert Online (BDO). Hourly ETL ingests price and stock data from
arsha.io into RDS Postgres; an API exposes raw snapshots, daily
rollups, and BDO-domain analytics (per-tier expected enhancement
cost, volatility, liquidity, anomaly flags).

Computing per-tier expected enhancement cost is the project's reason
to exist. **Do not** replace it with generic statistics; the previous
rewrite did and the project lost its purpose.

## Language

Canonical vocabulary for the BDO market domain. Definitions only — the
quantitative model (formulas, probabilities, worked numbers) lives in
the active spec's `domain-model.md`, never here.

### Market data

**base_price**:
The canonical central-market price; the basis for all pricing math and
the daily rollup.
_Avoid_: market price, current price, spot price

**last_sold_price**:
The most recent actual trade price. Retained for anomaly and trend
signals only, never for pricing.
_Avoid_: current price, spot price

**price_min / price_max**:
The central market's system-enforced price floor and ceiling for an
item — a regulated band, not a live bid/ask.
_Avoid_: spread, bid/ask, order book

**total_trades**:
Lifetime cumulative count of completed trades for an item.
_Avoid_: volume (daily volume is derived from the change in this)

**snapshot**:
One hourly captured market row for a `(region, item, sid)`.
_Avoid_: sample, reading, tick

**daily rollup**:
The once-daily OHLC-style compaction of snapshots into `market_daily`.
_Avoid_: aggregate, summary

**region**:
A game server / market region (`tw` is the default; `kr`, `eu`, `na`,
… activate by adding an EventBridge rule).
_Avoid_: server, world

### Enhancement

**sid**:
An item's sub-id, i.e. its enhancement level: 0 = base, 1 = PRI,
2 = DUO, 3 = TRI, 4 = TET, 5 = PEN, … (cap depends on the item and the
game version).
_Avoid_: grade, plus level, enhancement number

**clean**:
The `sid = 0` (unenhanced) copy of an item, consumed as fuel on each
enhancement attempt.
_Avoid_: base copy, raw, stock item

**enhancement tier**:
The named level for an `sid` — PRI, DUO, TRI, TET, PEN, …
_Avoid_: plus level, grade

**accessory enhancement**:
Enhancing `(item, sid)` to `(item, sid+1)` by consuming a clean copy;
on failure the target item is destroyed.
_Avoid_: upgrading, levelling

**failstack**:
The accumulated failed-attempt counter that raises the next attempt's
success probability (the code parameter is `stack`).
_Avoid_: fail count, streak

**cron stone**:
A consumable that protects an in-progress item from destruction on a
failed attempt (used by the cron-protected cost model).
_Avoid_: protection stone

### Analytics

**volatility**:
Price variability over a rolling window (standard deviation and
coefficient of variation).
_Avoid_: variance, noise

**liquidity**:
A tradability measure derived from daily trade volume.
_Avoid_: turnover, activity

**anomaly**:
A snapshot flagged as an outlier against its trailing window
(z-score based).
_Avoid_: outlier, spike

**spread (bid-ask spread)**:
The gap between the best bid and the best ask in the central-market
order book; undefined when the book is one-sided. Distinct from the
`price_min`/`price_max` band.
_Avoid_: price band, price_min/price_max (that is the enforced band,
not the spread)

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
