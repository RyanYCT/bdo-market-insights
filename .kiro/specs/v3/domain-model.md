# v3 — Domain Model

Precise source of truth for `bdo_common.pricing` and `bdo_common.analytics`.
Worked numbers are normative: the unit tests assert them.

## Enhancement stages
sid = enhancement level: 0 (base/clean), 1=PRI, 2=DUO, 3=TRI, 4=TET, 5=PEN,
6=HEX, 7=SEP, 8=OCT, 9=NOV, 10=DEC. Uncapped in schema. v1 prices accessory
transitions PRI..PEN, i.e. sid 0->1 .. 4->5.

## Canonical price
base_price is the canonical market price for all pricing math and the daily
OHLC rollup. last_sold_price is retained for anomaly/trend only.

## Probability model
Piecewise-linear in failstack `stack`, global hard cap 0.90:
  p = base + growth1*stack                              if stack <= soft_cap_stack
    = soft_cap_rate + growth2*(stack - soft_cap_stack)  otherwise
  p = min(p, max_rate)            # max_rate = 0.90
soft_cap_rate is authoritative at/above the breakpoint (~0.5pp discontinuity
vs base+growth1*soft_cap_stack accepted). Curves in rates.json (accessory_v1).
Verified: 0->1@18=0.70, 1->2@40=0.50, 2->3@44≈0.40, 3->4@110=0.30.
Default stack per transition = default_stack (= soft_cap_stack; 4->5 has none
-> 0, TODO real target).

## Cost model — accessory_v1 (Model A1)
Market-input, probability-weighted, no cron. Each attempt consumes one clean
(sid:0) copy; failure destroys the in-progress item.
  expected_cost(sid -> sid+1) = (base_price[sid] + base_price[0]) / p(stack)
Pluggable via model registry (ADR-0012). accessory_v1 is the only v1 model.

## Tax — net sale rate
  net_rate = 0.65 * (1 + value_pack + ring + family_fame)
  value_pack  = 0.30 if active else 0          # default ON
  ring        = 0.05 if owned  else 0          # default OFF
  family_fame = {0:0, 1000:0.005, 4000:0.010, 7000:0.015}  # default 7000
Verified: 100M @ VP+ring+7000 = 88,725,000; VP+7000 = 85,475,000;
7000 only = 65,975,000. Buying materials incurs no tax (buyer side).

## Analysis output (per transition)
enhancement_analysis(prices, model_id, rates, stack, intent, tax):
  sid_from, sid_to, stack, p, expected_attempts, clean_stone_price,
  input_item_price, expected_cost, target_market_price, net_rate, revenue,
  expected_profit, roi, verdict
- intent=personal: net_rate=1.0, revenue=target_market_price;
  verdict = "enhance" if expected_cost < target_market_price else "buy".
- intent=resale: revenue = target_market_price * net_rate;
  verdict = "profit" if expected_profit > 0 else "loss".
- Missing base_price[sid] -> transition omitted. Plus cumulative
  "clean -> sid:N" cost and best_tier (max roi).

### Worked example (Deboreka Ring, base prices, stack=18, p=0.70)
cost(0->1) = (448e6 + 448e6)/0.70 = 1.28e9. Target price[1]=1.02e9.
Personal -> verdict="buy" (1.28e9 > 1.02e9). Resale (VP+7000):
revenue=1.02e9*0.85475=8.718e8, profit<0 -> verdict="loss".

## Analytics defaults
- Window 14 days; < 7 daily points -> {"insufficient_data": true}.
- Volatility on daily close_price: sample stddev (n-1); CV = sigma/mean.
- Liquidity = mean daily volume, daily volume =
  total_trades(end of day) - total_trades(start of day).
- Anomaly: z = (latest_close - window_mean)/window_sigma;
  is_anomalous = |z| > 3; sigma == 0 -> not anomalous.
- Pure functions, zero AWS imports (Python statistics stdlib only).

## Cron model — deferred (accessory_cron_v1)
Data captured now in rates.json.cron_counts (tables a/b); model built later.
Items carry cron_table ("a"|"b") in DynamoDB; table b = Deboreka series only
(12094, 12276, 11653, 11882), all else a. Cron price 3,000,000 flat. With
cron, failure does not destroy: 60% retain level, 40% drop exactly one level
(Markov chain). Cron consumed on success too; failstack does not build.
