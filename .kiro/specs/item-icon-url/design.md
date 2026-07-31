# Item icon URL — Design

## Convention

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
  `null` for every item (opt-in, mirrors `ApiDomainName`; NFR-4).
- Deterministic and stable for a given base + id → cacheable (NFR-2). The
  delivery CDN sets long-lived cache headers (NFR-1).

## Placement

On the `/v1/items` `ItemResponse` (list + single item), so it is item-level
(FR-4) and lives with the rest of the catalog metadata:

```jsonc
GET /v1/items/12094
{ "id": 12094, "name": "Deboreka Ring", "grade": 4,
  "icon_status": "stored", "icon_url": "https://icons.example.com/icons/12094.png", ... }
```

`icon_status != "stored"` or no base configured → `"icon_url": null`.

## Null semantics (NFR-3)

The field is always present with an explicit `null` when unavailable — not an
omitted key — so a consumer branches on `null` to render a placeholder, and the
field can populate partially across items without ambiguity.

## Delivery follow-up (out of scope here)

The icons bucket is private (`infra/icons.yaml`). A CloudFront-OAC distribution
in front of it — the origin `ICON_BASE_URL` points at, with immutable
long-max-age caching — is a separate change. Until it lands and `IconBaseUrl` is
set for the stage, `icon_url` stays `null`.

## OpenAPI

`icon_url` is a field on the typed `ItemResponse`, so `infra/openapi.yaml` is
regenerated (`make openapi`) and the CI drift check passes.
