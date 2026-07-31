# ADR-0021: Public item icon URL on the item registry

## Status

Accepted

## Context

`iconSync` materializes item icons into the private `bdo-<stage>-icons` bucket
and records `icon_status` (`unset`/`stored`/`missing`) on each item. The item
registry (`/v1/items`) exposes `icon_status` but not a URL, so a consumer knows
an icon *exists* but has no address to fetch it from.

This is a read-model concern: present a URL derived from stored state, without
changing ingestion or storage.

## Decision

Add `icon_url` to the `/v1/items` item shape (`ItemResponse`), resolved
item-level:

```
icon_url = f"{ICON_BASE_URL}/icons/{item_id}.png"   # when icon_status == "stored" and a base is set
icon_url = null                                     # otherwise
```

- `ICON_BASE_URL` is a deploy-time parameter (`IconBaseUrl`, empty default)
  passed to `itemRegistry`, mirroring the `ApiDomainName` opt-in convention
  (empty ⇒ no URL, never committed). The path mirrors the object key the
  materializer writes (`icons/<id>.png`). Deterministic and stable for a given
  base + id, so it is cacheable.
- The field is **additive and nullable**: existing consumers are unaffected, and
  a `null` (icon `unset`/`missing`, or no base configured) is an explicit
  "not available" signal.

The icons bucket **stays private**; putting a CloudFront-OAC distribution in
front of it (the public delivery origin `ICON_BASE_URL` points at) is a separate
follow-up, already noted in `infra/icons.yaml`. Until that exists and a base is
configured, `icon_url` is `null`.

## Consequences

- (+) Consumers get a fetchable icon address directly from the API, gated on a
  stable, documented convention.
- (+) Additive/nullable — no breaking change; the field can populate partially
  (some rows `null`) safely.
- (−) `icon_url` is `null` until the icons-bucket delivery CDN and
  `ICON_BASE_URL` are configured — the serving distribution is a tracked
  follow-up, not part of this change.

## Notes

Spec: `.kiro/specs/item-icon-url/`. The icon-URL convention is asserted by
`tests/unit/test_icons.py`; `icon_url` is a field on the typed `ItemResponse`,
so `infra/openapi.yaml` is regenerated.
