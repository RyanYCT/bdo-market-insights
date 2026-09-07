# Market range shortcut — Requirements

> **Design only — not scheduled for implementation.** A read-ergonomics feature
> over existing data (no new ingestion, no new storage). Records the range →
> granularity routing so it can be sized before any code.

## Product

Expose a single `range` shortcut on the market read path so a client can ask for
a human window (`1d`, `7d`, `30d`, `90d`, `1y`, `all`) and get back a
sensibly-sampled series — **without** having to know that hourly and daily data
live behind two different endpoints. This mirrors the range picker common to
market/stock UIs (e.g. `1D / 5D / 1M / 6M / YTD / 1Y / 5Y / All`), where a longer
range is served at a coarser granularity so the point count — and payload — stay
bounded.

## Why (the convention, applied to our data)

The system stores two granularities on deliberately different retentions:

- **hourly** `market_snapshot` — 90-day retention (v3 FR-7 sweep).
- **daily** `market_daily` — retained indefinitely.

They are read today via two endpoints (`/snapshots`, `/daily`), each with its own
`from`/`to` and hard row cap (`MAX_SNAPSHOT_LIMIT` 1848, `MAX_DAILY_LIMIT` 990).
The finest sampling the ETL produces is hourly. So the natural range ladder has
**two rungs**, with the crossover around 30 days:

| `range` | span | granularity | source | ~points (1 sid) |
|---------|------|-------------|--------|-----------------|
| `1d`  | 24 h   | hourly | `market_snapshot` | 24 |
| `7d`  | 168 h  | hourly | `market_snapshot` | 168 |
| `30d` | 720 h  | hourly | `market_snapshot` | 720 |
| `90d` | 90 d   | daily  | `market_daily`    | 90 |
| `1y`  | 365 d  | daily  | `market_daily`    | 365 |
| `all` | max    | daily  | `market_daily`    | all available |

## Functional Requirements

- **FR-1** Accept a `range` query value from the fixed set
  `{1d, 7d, 30d, 90d, 1y, all}` on the market read path (see the Open questions
  for endpoint shape).
- **FR-2** Route `range` to a granularity by the ladder above: **≤ 30 days →
  hourly**, **> 30 days → daily**. `all` resolves to daily over all retained
  daily rows.
- **FR-3** Translate `range` into the concrete `from`/`to` bounds of the chosen
  source and reuse the existing query paths, caps, and coverage reporting — no
  new query logic beyond bound derivation.
- **FR-4** Echo the **granularity actually served** (`hourly`/`daily`) and the
  resolved window in the response, so a client can label the axis and know the
  sampling without guessing.
- **FR-5** `range` is **mutually exclusive** with explicit `from`/`to`; if both
  are supplied, return `400` (or define a documented precedence). A bare call
  keeps each endpoint's current default (unchanged).
- **FR-6** Additive and backward-compatible: existing `from`/`to`/`limit`
  behaviour is untouched; regenerate `infra/openapi.yaml` for the new param /
  response field.

## Non-Functional Requirements

- **NFR-1 (bounded payload)** Each `range` must yield a point count that respects
  the endpoint's hard cap. `30d` hourly ≈ 720 points/sid and can exceed
  `MAX_SNAPSHOT_LIMIT` (1848) for multi-sid items — the crossover / cap
  interaction MUST be resolved (see Open questions), not silently truncated.
- **NFR-2 (no new data plane)** Read-only over existing tables; **no new
  ingestion and no new storage** (unlike the bid-ask-spread feature).
- **NFR-3 (retention honesty)** Hourly ranges are bounded by the 90-day snapshot
  retention; a hourly `range` wider than retention MUST route to daily rather
  than return a silently short series.

## Open questions (decide before building)

1. **Endpoint shape.** Either (a) a new unified `/v1/market/items/{id}/series?range=`
   endpoint that auto-routes to the right source, or (b) `range` as sugar on the
   existing `/snapshots` + `/daily` endpoints (each clamped to its own
   granularity). (a) matches the range-picker UX in one call; (b) is a smaller
   change but leaves the client choosing the endpoint.
2. **`30d` crossover vs the snapshot cap.** Keep `30d` on hourly (and raise /
   document the multi-sid cap behaviour), or move the crossover so `30d` → daily
   for safety.
3. **`granularity` override.** Whether to also accept an explicit
   `granularity=hourly|daily` to override the ladder (power-user escape hatch),
   and how it interacts with `range`.

## Out of scope

- Sub-hourly granularity (the ETL samples hourly; there is no finer source).
- Server-side downsampling/aggregation of hourly → arbitrary buckets; this
  feature only routes between the two granularities that already exist.
