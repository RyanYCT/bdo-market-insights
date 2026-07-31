# Market spread & item icon URL — Requirements

## Product

Expose two read-model fields the platform can already derive from stored data
but does not yet surface through the API:

1. a **bid-ask spread** (`spread_pct`) per `(item_id, sid)` on the market
   analysis endpoint — a liquidity signal from the `item_sid` price band; and
2. a **public icon URL** (`icon_url`) per item on the item registry — an address
   for the self-hosted icon the materializer already stores.

Both are **additive and nullable**: existing consumers are unaffected, and each
field may be `null` when the underlying data is unavailable.

## Functional Requirements

### Spread

- **FR-1** `GET /v1/market/items/{item_id}/analysis` SHALL include a
  `spread_pct` field for the requested `(item_id, sid)`.
- **FR-2** `spread_pct` SHALL be a number (percentage; e.g. `2.3` = 2.3%) when
  computable, and `null` when it is not (FR-4).
- **FR-3** `spread_pct` SHALL be computed server-side from a single canonical
  definition (see design.md); it is not a raw passthrough of `price_min`/
  `price_max`, and consumers do not re-derive it.
- **FR-4** WHEN there is no `item_sid` row for the `(region, item_id, sid)` or
  the bid floor is non-positive, THE SYSTEM SHALL emit `spread_pct: null`
  (never `0`, a sentinel, or an error).
- **FR-5** The spread SHALL be derived from data already stored (`item_sid`) via
  a single primary-key lookup on the existing read path — no new per-item
  fan-out and no additional upstream (arsha.io) calls.

### Icon URL

- **FR-6** The `/v1/items` item shape (list and single-item) SHALL include an
  `icon_url` field per item.
- **FR-7** `icon_url` SHALL be an absolute, fetchable URL WHEN the item's
  `icon_status == "stored"` and a delivery base is configured; otherwise `null`.
- **FR-8** The URL SHALL follow a stable, documented convention (design.md)
  derived from a configured base and the `item_id`, matching the object key the
  materializer writes, so it is cacheable and stable across deploys.
- **FR-9** `icon_url` SHALL be item-level (independent of `sid`).

### Compatibility

- **FR-10** Both fields SHALL be additive and optional; consumers that ignore
  them are unaffected.
- **FR-11** Existing field names, types, routes, and the API version SHALL be
  preserved (no breaking rename or restructure). The OpenAPI document is
  regenerated for the typed `ItemResponse` change.

## Non-Functional Requirements

- **NFR-1 (cost)** No new request fan-out or upstream calls; the spread reuses
  the analysis request's warm DB connection with one indexed read.
- **NFR-2 (delivery)** Icons are served as static objects behind a CDN with
  cache-friendly headers; API payloads carry only the URL, never image bytes.
- **NFR-3 (stability)** A stored icon's URL is deterministic and durable for a
  given base + id (no per-request signing that churns the URL).
- **NFR-4 (explicit nulls)** Both fields are present with an explicit `null`
  when unavailable, so "unknown" is unambiguous and partial rollout is safe.
- **NFR-5 (config hygiene)** The icon delivery base is a deploy-time parameter
  with an empty default (opt-in); real hosts are never committed.

## Out of scope

- The **CloudFront-OAC distribution** in front of the private icons bucket (the
  public delivery origin `icon_url` points at) — a separate follow-up already
  noted in `infra/icons.yaml`. Until it exists and a base is configured,
  `icon_url` is `null`.
- Any change to ingestion, storage, or how `volatility`/`liquidity`/`anomaly`
  or the enhancement model are computed.
