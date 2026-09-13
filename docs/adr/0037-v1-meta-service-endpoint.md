# ADR-0037: `GET /v1/meta` service-metadata (discovery) endpoint

## Status

Accepted

## Context

With regions now activated declaratively (ADR-0036), the frontend needs to know
what it can show: the deployed API version, which regions are active and whether
they hold data yet, and the supported insights periods. Without a discovery
call, a client either hardcodes this (and drifts from the backend) or makes
several preflight requests to infer it.

Two truths determine what a client can render for a region: whether the region
is **configured-active** (in the deployed `BdoRegions` list) and whether it is
**data-bearing** (does RDS hold market/insights rows, and how fresh). These are
independent — a region can be active with no data yet, or hold history but no
longer be active — so both must be reported.

The freshness half requires reading RDS. `marketQuery` is the only in-VPC RDS
reader and already owns top-level routes beyond `/v1/market/*` (it serves
`/v1/insights`); `itemRegistry` runs outside the VPC and cannot reach RDS.

## Decision

Add a single **service-metadata endpoint, `GET /v1/meta`**, on `marketQuery`.

- **One extensible envelope.** It returns `api_version`, `regions` (see below),
  and `periods` in one response, with no query parameters. The shape is
  additively extensible: future fields (limits, models, feature flags) can be
  added without introducing a new preflight call.
- **`regions` is the union** of configured-active regions (from the
  `ACTIVE_REGIONS` env, the comma-joined `BdoRegions` toggle) and data-bearing
  regions (from RDS). Each entry carries `active` (true iff configured-active),
  `item_count`, `latest_snapshot_at`, `latest_daily_date`, and `has_insights`.
  The freshness fields are non-null iff the corresponding rows exist, so an
  activated-but-empty region shows `active: true` with null freshness, and a
  deactivated-but-historical region shows `active: false`. The presence data
  comes from a read-only `RegionRepo.region_availability` aggregate (one
  `GROUP BY region` pass per table, cheap given the `(region, …)` leading
  indexes), merged with the active set in the handler.
- **`api_version` has a single source:** the `API_VERSION` env var injected at
  deploy from the release tag (the prod deploy is tag-gated). It is not read
  from `pyproject.toml`.
- **API-key-gated.** `/v1/meta` inherits the API-wide `ApiKeyRequired` and the
  default usage plan; it is **not** made keyless like `/v1/docs`. Rationale: it
  reads RDS, so it should inherit the usage-plan rate limiting that protects the
  in-VPC reader. A keyless posture (as `/v1/docs`, which serves static docs and
  touches no datastore) was considered and rejected.
- **Cache-Control.** Served with a rolling `max-age` of ~1h, aligned to the
  hourly ETL cadence (the region freshness only advances once per ETL run). The
  header is attached by a route-scoped middleware so the model return still
  drives the generated OpenAPI schema.

## Consequences

- (+) The frontend bootstraps from one call instead of several preflight
  requests, and gets a truthful active/data-presence picture per region.
- (+) No new VPC Lambda: `marketQuery` already owns the only RDS read path.
- (+) The endpoint lands in the generated `infra/openapi.yaml` (with the
  `MetaResponse`/`RegionAvailability` schemas) and is guarded by the existing CI
  OpenAPI drift check.
- (−) The rolling `max-age` does not align to the `:07` wall-clock ETL boundary,
  so a cached response can be up to ~2h stale relative to the freshest run.
  Accepted for a bootstrap/discovery endpoint whose freshness fields are
  advisory.
- (−) `region_availability` runs three aggregate queries per call; the ~1h cache
  keeps that off the hot path, and the queries are cheap on the region-leading
  indexes.

## Notes

- `active` is driven entirely by `ACTIVE_REGIONS`, which is the same
  `BdoRegions` toggle from ADR-0036 — so `/v1/meta` and the deployed schedules
  can never disagree about which regions are active.
- On DB unavailability the endpoint fails the same way as the other in-VPC read
  routes (5xx counted by the API SLO alarm); it invents no partial or stale
  cache.
