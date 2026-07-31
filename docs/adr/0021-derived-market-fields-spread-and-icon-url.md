# ADR-0021: Derived market fields — bid-ask spread and public icon URL

## Status

Accepted

## Context

Two useful signals are derivable from data the platform already stores but does
not yet expose through the API:

- **Bid-ask spread.** The `item_sid` reference table already stores `price_min`
  and `price_max` per `(region, item_id, sid)` — the central market's system
  bid floor and ask ceiling (normalized from arsha `priceMin`/`priceMax`). The
  width of that band is a per-tradable liquidity signal, but the analysis
  endpoint (`/v1/market/items/{id}/analysis`) never reads `item_sid`, so
  consumers cannot get it without knowing the raw fields and the formula.
- **Item icon URL.** `iconSync` materializes icons into the private
  `bdo-<stage>-icons` bucket and records `icon_status` (`unset`/`stored`/
  `missing`) on each item. The item registry (`/v1/items`) exposes
  `icon_status` but not a URL, so a consumer knows an icon *exists* but has no
  address to fetch it from.

Both are read-model concerns: derive from stored data and present, without
changing ingestion or storage.

## Decision

Expose both as **additive, nullable** fields — no breaking change to existing
responses.

### `spread_pct` on the analysis response

Add `spread_pct` to `GET /v1/market/items/{id}/analysis`, computed server-side
from the `item_sid` band so there is one definition and consumers never
re-derive it (`bdo_common.analytics.spread_pct`):

```
spread_pct = (price_max - price_min) / price_min * 100   # rounded to 1 dp
spread_pct = null                                        # no item_sid row, or price_min <= 0
```

Computed in the read path from a single additional indexed lookup
(`ItemSidRepo.get`) on the primary key — no new per-item fan-out or extra
upstream calls. A well-formed row with `price_min == price_max` yields `0.0` (a
genuinely tight market); a missing/degenerate row yields `null` (unknown).

> If a true order-book best-bid/best-ask is tracked later, the field name and
> semantics are unchanged (`(ask - bid) / bid * 100`); only the inputs move.

### `icon_url` on the item registry

Add `icon_url` to the `/v1/items` item shape, resolved item-level:

```
icon_url = f"{ICON_BASE_URL}/icons/{item_id}.png"   # when icon_status == "stored" and a base is set
icon_url = null                                     # otherwise
```

`ICON_BASE_URL` is a deploy-time parameter (`IconBaseUrl`, empty default) passed
to `itemRegistry`, mirroring the `ApiDomainName` opt-in convention (empty ⇒ no
resource/URL, never committed). The path mirrors the object key the materializer
writes (`icons/<id>.png`). The URL is deterministic and stable for a given base
+ id, so it is cacheable.

The icons bucket **stays private**; putting a CloudFront-OAC distribution in
front of it (the public delivery origin `ICON_BASE_URL` points at) is a separate
follow-up, already noted in `infra/icons.yaml`. Until that exists and a base is
configured, `icon_url` is `null` — the same "not available" signal consumers
already get for `unset`/`missing` icons.

## Consequences

- (+) Consumers get a liquidity signal and a fetchable icon address directly
  from the API, with no client-side market math or knowledge of raw columns.
- (+) One canonical spread definition (a pure, tested function); one stable,
  documented icon-URL convention.
- (+) Both fields are additive/nullable — existing consumers are unaffected, and
  the fields can populate partially (some rows `null`) safely.
- (−) The analysis path does one extra indexed `item_sid` read per request
  (negligible; primary-key lookup on the warm connection).
- (−) `icon_url` is `null` until the icons-bucket delivery CDN and
  `ICON_BASE_URL` are configured — the serving distribution is a tracked
  follow-up, not part of this change.

## Notes

Spec: `.kiro/specs/market-spread-and-icons/`. The spread definition is asserted
by `tests/unit/test_analytics.py`; the icon-URL convention by
`tests/unit/test_icons.py`. `spread_pct` is emitted from an untyped analysis
dict (not captured in the OpenAPI schema); `icon_url` is on the typed
`ItemResponse`, so `infra/openapi.yaml` is regenerated.
