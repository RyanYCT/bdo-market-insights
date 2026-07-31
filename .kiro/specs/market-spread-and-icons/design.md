# Market spread & item icon URL — Design

## 1. Spread

### Source

`item_sid` already stores, per `(region, item_id, sid)`, the central market's
system bid floor (`price_min`) and ask ceiling (`price_max`), normalized from
arsha `priceMin`/`priceMax`. The spread is the width of that band relative to
the floor.

### Formula (single source of truth)

`bdo_common.analytics.spread_pct`:

```
spread_pct = (price_max - price_min) / price_min * 100     # rounded to 1 dp
spread_pct = None                                          # price_min/price_max missing, or price_min <= 0
```

- `price_min == price_max` → `0.0` (a genuinely tight market).
- Missing row or non-positive floor → `null` (unknown).

### Read path

`ItemSidRepo.get(conn, region=, item_id=, sid=)` fetches the row by its primary
key on the same warm connection the analysis handler already uses. The handler
computes `spread_pct` from that row (or `None` when absent) and adds it to the
response — no new fan-out (NFR-1).

### Placement

Top-level on the analysis response, beside `enhancement`/`analytics`:

```jsonc
GET /v1/market/items/12094/analysis?sid=3&region=tw
{
  "item_id": 12094,
  "region": "tw",
  "sid": 3,
  "window_days": 14,
  "spread_pct": 2.3,          // NEW — number | null
  "enhancement": { ... },
  "analytics": { ... }
}
```

Unknown-spread example (no `item_sid` row): `"spread_pct": null`.

## 2. Icon URL

### Convention

`bdo_common.icons.public_icon_url`, item-level:

```
icon_url = f"{ICON_BASE_URL}/icons/{item_id}.png"   # icon_status == "stored" AND ICON_BASE_URL set
icon_url = None                                     # otherwise
```

- The `icons/<id>.png` path mirrors the object key `iconSync` writes into the
  `bdo-<stage>-icons` bucket (`ICON_KEY_PREFIX`), so the URL addresses the real
  object once the bucket is served publicly.
- `ICON_BASE_URL` is the `IconBaseUrl` deploy-time parameter (empty default),
  threaded `template.yaml → api.yaml → itemRegistry` env. Empty ⇒ `icon_url` is
  `null` for every item (opt-in, mirrors `ApiDomainName`; NFR-5).
- Deterministic and stable for a given base + id → cacheable (NFR-3). The
  delivery CDN sets long-lived cache headers (NFR-2).

### Placement

On the `/v1/items` `ItemResponse` (list + single item), so it is item-level
(FR-9) and lives with the rest of the catalog metadata:

```jsonc
GET /v1/items/12094
{ "id": 12094, "name": "Deboreka Ring", "grade": 4,
  "icon_status": "stored", "icon_url": "https://icons.example.com/icons/12094.png", ... }
```

`icon_status != "stored"` or no base configured → `"icon_url": null`.

## 3. Null semantics (NFR-4)

Both fields are always present with an explicit `null` when unavailable — not an
omitted key — so a consumer branches on `null` to render an "unknown"/placeholder
state, and the fields can populate partially across items without ambiguity.

## 4. Delivery follow-up (out of scope here)

The icons bucket is private (`infra/icons.yaml`). A CloudFront-OAC distribution
in front of it — the origin `ICON_BASE_URL` points at, with immutable
long-max-age caching — is a separate change. Until it lands and `IconBaseUrl` is
set for the stage, `icon_url` stays `null`; nothing else in this design depends
on it.

## 5. OpenAPI

`spread_pct` is emitted from the analysis handler's untyped dict, so it does not
appear in the generated schema. `icon_url` is a field on the typed
`ItemResponse`, so `infra/openapi.yaml` is regenerated (`make openapi`) and the
CI drift check passes.
