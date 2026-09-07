# Market range shortcut — Design (draft)

> Draft; not implemented. Finalize once the endpoint-shape decision is made
> (requirements.md "Open questions").

## Routing

`range` maps to a granularity and a `from`/`to` window over the existing source:

```
range  -> granularity, from
1d     -> hourly, now - 24h
7d     -> hourly, now - 168h
30d    -> hourly, now - 720h        # crossover; see cap note
90d    -> daily,  today - 90d
1y     -> daily,  today - 365d
all    -> daily,  no lower bound (all retained daily rows)
```

Rule: **span ≤ 30 days → hourly (`market_snapshot`); span > 30 days → daily
(`market_daily`)**. `to` defaults to now/today. The resolver produces the same
`from`/`to` the endpoints already accept, so the query, caps, and coverage
computation are reused unchanged — the feature is bound-derivation + source
selection, nothing more.

A pure helper keeps one definition, asserted by unit tests:

```python
# returns (granularity, from_, to) for a range token; raises on unknown token
def resolve_range(token: str, *, now: datetime) -> tuple[str, datetime | date, ...]
```

## Request / response

Two candidate shapes (Open question 1):

- **(a) Unified endpoint** `GET /v1/market/items/{id}/series?range=30d[&sid=]`
  — resolves granularity internally, returns rows plus a `granularity` field.
- **(b) Sugar on existing endpoints** — `/snapshots?range=…` clamped to hourly,
  `/daily?range=…` clamped to daily; a range wider than the endpoint's
  granularity supports is a `400` (or documented clamp).

Either way the response echoes what was served (FR-4):

```jsonc
{
  "item_id": 12094,
  "region": "tw",
  "sid": 0,
  "range": "30d",
  "granularity": "hourly",        // what was actually served
  "window_start": "…", "window_end": "…",
  "rows": [ /* existing snapshot|daily row shape */ ],
  "coverage": { /* existing Coverage|DailyCoverage */ },
  "truncated": false
}
```

`range` and `from`/`to` are mutually exclusive (FR-5). Existing bare-call
defaults are unchanged (`/snapshots` 7d hourly, `/daily` 90d daily).

## Reuse (no new data plane)

- Bounds feed the existing `SnapshotRepo.get_snapshots` / `DailyRepo.get_daily`
  paths; existing hard caps and `truncated`/coverage reporting apply as-is.
- No new table, no new ingestion, no schedule. Read-only over `market_snapshot`
  / `market_daily`.
- Hourly ranges are bounded by the 90-day snapshot retention; the resolver never
  emits an hourly window wider than retention (NFR-3) — it routes to daily.

## Cap interaction (Open question 2)

`30d` hourly is ~720 rows/sid; a multi-sid item can exceed `MAX_SNAPSHOT_LIMIT`
(1848) and be `truncated`. Options: (i) keep `30d` hourly and document/raise the
multi-sid cap; (ii) move the crossover so `30d` → daily; (iii) resolve per-`sid`
so the cap is per single series. Decide before implementing.

## Exposure

Additive: a new `range` query param (+ `granularity` response field), or a new
`/series` endpoint. Regenerate `infra/openapi.yaml`. No change to the analytics
`window_days` default (that shipped separately) — this is purely the time-series
read path.

## Tasks (deferred)

Enumerated once the endpoint shape (Open question 1) is chosen; no work is
scheduled yet.
