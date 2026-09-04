# ADR-0033: Read-through icon delivery via CloudFront origin failover

## Status

Accepted

## Context

Item icons are self-hosted in the delivery bucket and served through the CDN
(ADR-0023). Materialization was push-based: a daily `iconSync` fetched icons
from the Pearl Abyss CDN for **tracked** items whose `icon_status` was `unset`
and recorded `stored`/`missing` on the item.

Two limitations surfaced:

- **Coverage.** Only tracked items (~dozens) ever got icons. The full-catalog
  artifact (ADR-0031, tens of thousands of items) therefore had `icon_url: null`
  for everything untracked, so a catalog-browsing frontend couldn't show icons.
- **Fragility.** `icon_status` is item-table state that is independent of the
  bucket. If the bucket is recreated (as during the ADR-0032 CDN migration), the
  stored icons vanish but `icon_status` still says `stored`, so `iconSync`
  no-ops and the icons never come back until the status is manually reset.

## Decision

Deliver icons **read-through**: materialize on first request, driven by the CDN
rather than by a scheduled job.

- The delivery distribution's icons behavior uses a **CloudFront origin group**:
  the S3 bucket is the primary origin; a new **`icon_origin` Lambda** (a Function
  URL, OAC-signed) is the secondary. CloudFront fails over on **403/404** — the
  status S3+OAC returns for a missing object.
- On failover, `icon_origin` fetches the icon from the Pearl CDN, `PutObject`s it
  into the delivery bucket, and returns the bytes for the current request. Every
  subsequent request is served straight from S3 (and the edge cache). Icons thus
  materialize on first view, for the whole catalog, and self-heal if the bucket
  is ever recreated — no `icon_status` bookkeeping, no backfill.
- **`public_icon_url` becomes universal**: it returns `{base}/icons/{id}.png` for
  every item when a delivery base is configured (no `icon_status` gate). So the
  catalog artifact and `/v1/items` expose a working `icon_url` for all items.
- **`icon_origin` is standard-library + boto3 only** (no shared layer), keeping
  the `cdn` stack free of a platform-layer dependency and minimising cold-start /
  import-failure surface on the origin path.
- **`iconSync` is no longer scheduled.** The function is kept as an on-demand
  warm-prefetch for a fresh environment's tracked icons (invoked by the bootstrap
  orchestrator); ongoing and whole-catalog materialization is the read-through's
  job. `icon_status` is now vestigial (kept for now; a later change drops it).

## Consequences

- (+) Universal icon coverage for the entire catalog, self-healing on bucket
  recreation; the stale-`icon_status` failure mode is structurally gone.
- (+) No daily job for the steady state; the Lambda runs only on a genuine miss
  (rare, given the 7-day edge cache), so cost stays negligible.
- (−) First view of an un-materialized icon pays one upstream fetch (~a few
  hundred ms), then it's cached.
- (−) More moving parts on the distribution (a second origin + origin group + a
  Lambda Function URL with OAC and an invoke permission).
- (−) `icon_status` lingers as a vestigial column until a follow-up removes it.

## Notes

Builds on ADR-0023 (icons CDN) and ADR-0032 (the `cdn` stack). A genuinely
missing upstream icon returns 404 from `icon_origin`, **negatively cached** at
the edge (CloudFront `ErrorCachingMinTTL`, ~1h) so it is not re-fetched from
Pearl on every request; transient upstream errors return 502 and keep the short
default TTL so they retry quickly. Because `public_icon_url` is now universal,
`icon_url` is **best-effort** — an iconless item resolves to a cached 404, so a
consuming frontend should render a placeholder / use an `onerror` fallback. The
bootstrap warm-prefetch keeps the tracked set instant on a fresh environment
while the read-through covers the long tail.
