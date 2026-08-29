# ADR-0031: Deliver the full item catalog as a static CDN artifact

## Status

Accepted

## Context

`GET /v1/items` was backed by an unbounded DynamoDB `Scan`. Once `catalogSync`
began populating the entire arsha `util/db` catalog (tens of thousands of items,
ADR-0018) while only a small subset is `tracked`, a bare listing paged through
the whole table and materialized every row, exceeding the API Gateway
integration timeout.

The endpoint's original intent is CMS-style: hand the frontend the whole catalog
so a user can browse and choose which items to track. But two datasets with
opposite characteristics were being served through one synchronous request:

- **The catalog** — large, changes only on the weekly sync (game patches),
  read-mostly reference data.
- **The tracked flags** — tiny, mutable on user action.

A single API-Gateway request (29 s hard integration cap, in-memory
serialization) is the wrong transport for "give me the entire catalog".

The bounded/paginated API (a separate change) makes `/v1/items` *safe* but not
*pleasant* for whole-catalog browse: DynamoDB returns rows in key order, name
search is not indexable, and a full browse becomes many sequential round-trips.

## Decision

Split delivery by data shape:

- **The catalog** is published as a single static **`catalog/catalog.json`**
  object into the existing **icons bucket**, delivered through the **same
  CloudFront CDN** (ADR-0023). `catalogSync` (already weekly) scans the items
  table, projects the public catalog shape (`id, name, names, grade, category,
  main_category, sub_category, icon_url`), and writes the object at the end of
  each run. The frontend downloads it once (HTTP-cached) and does client-side
  search/filter/sort.
- **The tracked flags** stay on the API: `GET /v1/items?tracked=true` (served
  from the sparse `tracked-index` GSI) for the current selection, and
  `POST`/`PATCH`/`DELETE` to mutate it.

Delivery details:

- Stored as **plain UTF-8 JSON** (not pre-gzipped): CloudFront compresses it on
  the wire (`Compress: true`), so the stored object stays human-readable and no
  `Content-Encoding` is pinned while the browser still receives compressed
  bytes. The payload (~a few MB) is comfortably inside CloudFront's 10 MB
  auto-compression window.
- A dedicated `catalog/*` cache behavior carries the AWS managed **SimpleCORS**
  response-headers policy (`Access-Control-Allow-Origin: *`) — the frontend
  fetches the JSON via `fetch()` (cross-origin), unlike icons loaded via `<img>`
  — plus `CachingOptimized`, which honours the object's `Cache-Control:
  public, max-age=3600`.
- `catalogSync` gains the icons bucket name + CDN base as parameters (a
  `Catalog -> Icons` stack dependency; no cycle), an `s3:PutObject` grant scoped
  to `catalog/*`, and emits a `CatalogArtifactItems` metric. The artifact is
  republished on every non-skipped run (including the checksum-unchanged
  fast-path) so it self-heals and reflects current icon materialization.

## Consequences

- (+) Whole-catalog browse becomes an O(1) CDN hit with instant client-side
  search; the timeout is impossible for this path. `/v1/items` narrows to the
  small, mutable tracked subset.
- (+) Reuses the existing icons bucket + distribution, so the frontend uses a
  single origin for both icons and catalog; near-zero incremental cost.
- (−) The artifact is up to a week stale (plus the 1 h cache), which matches the
  catalog's real change cadence (weekly patches).
- (−) `catalogSync` now depends on the icons stack outputs and does a full table
  scan per run for the artifact; both are acceptable at a weekly cadence.

## Notes

Builds on ADR-0018 (full-catalog sync into DynamoDB) and ADR-0023 (icons CDN).
The catalog is served at `<IconBaseUrl>/catalog/catalog.json`. `icon_url` in the
artifact is resolved by the shared `public_icon_url`, so its coverage tracks
whatever the icon materialization path has stored.
