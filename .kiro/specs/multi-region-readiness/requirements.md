# Multi-region readiness — Requirements

> Derived from this spec's `design.md`. Records the settled decisions
> (ADR-0036 region-list toggle, ADR-0037 `/v1/regions` discovery) as
> requirements. The data plane is already region-partitioned and
> region-aware (v3); this feature changes only the **trigger layer** and
> adds a discovery endpoint. **Readiness-first:** default stays `[tw]`.

## Product

Make additional server regions activatable by editing **one** central list
and deploying — fanning out per-region EventBridge schedules across every
pipeline — and give the frontend a truthful endpoint for discovering which
regions are active and what data exists for each. No new region is turned on
to call this done; the mechanism must be provable with the default `[tw]`.

## Glossary

- **`BdoRegions`** — CommaDelimitedList SAM parameter (default `tw`); the
  single toggle for active regions. Each entry MUST be a member of the
  marketQuery region enum.
- **Primary region** — element 0 of `BdoRegions`; the scalar region threaded
  to consumers that need exactly one (POST validation, icon path).
- **Configured-active** — a region present in the deployed `BdoRegions` list.
- **Data-bearing** — a region for which RDS holds market/insights rows.

## Functional Requirements

### Central region toggle (readiness default)

- **FR-1** `template.yaml` SHALL replace the scalar `BdoRegion` parameter with
  a `BdoRegions` CommaDelimitedList (default `tw`), the single toggle for
  which regions are active.
- **FR-2** The primary region SHALL be derived as element 0 of `BdoRegions`
  (`Fn::Select`) and threaded — unchanged, as a scalar — to `itemRegistry`
  `POST /v1/items` validation and the icon-path builder (`api.yaml`,
  `cdn.yaml`, `icons.yaml`).
- **FR-3** WHEN deployed with the default `BdoRegions=tw`, the system SHALL
  produce exactly the schedules that exist today (one hourly ETL, one daily +
  one weekly insights, all `region=tw`) and no others, adding zero new cost.

### Per-region schedule fan-out

- **FR-4** From `BdoRegions`, `infra/etl.yaml` SHALL generate exactly one
  hourly ETL EventBridge rule per region, each starting the ETL state machine
  with input `{"region": "<region>"}`.
- **FR-5** From the same list, `infra/insights.yaml` SHALL generate exactly one
  daily and one weekly insights rule per region, each with input
  `{"region": "<region>", "period": "<daily|weekly>"}`.
- **FR-6** WHEN a region is added to `BdoRegions` and the stack is deployed,
  the system SHALL activate all three pipelines for it declaratively, with no
  manual EventBridge step; WHEN a region is removed and deployed, the system
  SHALL delete only that region's rules and leave other regions' rules intact.
- **FR-7** Per-region ETL executions SHALL remain independent: each execution
  SHALL roll up only its own region's previous UTC day (rollup independence).

### Region discovery endpoint (`GET /v1/regions`)

- **FR-8** `marketQuery` SHALL expose `GET /v1/regions` returning the **union**
  of configured-active and data-bearing regions; each entry SHALL carry
  `active`, `item_count`, `latest_snapshot_at`, `latest_daily_date`, and
  `has_insights`, plus a top-level `count`.
- **FR-9** For each region, `active` SHALL be true iff the region is in the
  deployed `BdoRegions` list (via the `ACTIVE_REGIONS` env var); the freshness
  fields SHALL be non-null iff RDS holds the corresponding rows. A
  configured-active region with no data SHALL appear with `active: true` and
  null freshness; a data-bearing region no longer configured SHALL appear with
  `active: false`.
- **FR-10** WHEN `GET /v1/regions` is called with a `region` filter that is not
  a member of the region enum, THEN the system SHALL respond `400`, consistent
  with the existing market endpoints.
- **FR-11** `/v1/regions` SHALL be included in the generated `infra/openapi.yaml`
  and guarded by the existing CI OpenAPI drift check.

### Unchanged guardrails

- **FR-12** Tracking SHALL remain global (single `tracked` boolean, one
  `tracked-index` GSI); `/v1/items` SHALL remain region-agnostic; the DynamoDB
  model and the RDS schema SHALL be unchanged by this feature.

## Non-Functional Requirements

- **NFR-1 (cost)** Each added region SHALL stay within the ≤ ~US$15/month
  incremental cap (v3 NFR-13); the default `[tw]` SHALL add zero new cost. A
  per-region cost estimate against that cap is a required deliverable.
- **NFR-2 (region validation)** CI SHALL validate that every `BdoRegions`
  entry is a member of the marketQuery region enum, failing the build before
  deploy rather than creating a rule that feeds an out-of-enum `region`.
- **NFR-3 (runbook)** A region-activation-and-verification runbook (add region
  → deploy → confirm rules enabled → confirm ingestion via `/v1/regions` and
  the region-aware endpoints → reconcile spend; plus rollback) is a required
  deliverable in `docs/runbook.md`.
- **NFR-4 (IaC discipline)** Per-region rules SHALL be generated from the list
  declaratively (`Fn::ForEach` via `AWS::LanguageExtensions`), keeping the
  change within the trigger layer — no new deploy-time Lambda or macro, one
  SAM `template.yaml`, one CI workflow.

## Out of scope

- Actually ingesting a second region's data (readiness-first; activation is a
  separate gated step per the runbook).
- Per-region tracking, per-region `/v1/items`, or any DynamoDB/RDS schema
  change.
- Per-region schedule minute-staggering and region-dimensioned skip metrics
  (documented as deferred options in the design, not required here).
