# Requirements Document

## Introduction

Multi-region readiness makes activating an additional server region a
declarative, central operation: editing one `BdoRegions` list and deploying
fans out per-region EventBridge schedules across every pipeline, and a new
`GET /v1/regions` endpoint lets the frontend discover which regions are active
and what data exists for each. The data plane is already region-partitioned and
region-aware (v3); this feature changes only the trigger layer and adds a
discovery endpoint. Readiness-first: the default stays `[tw]` and must behave
exactly as today with zero new cost, so the mechanism is provable without
turning on any new region. Each activated region must stay within the hard
≤ ~US$15/month incremental cost cap.

## Glossary

- **BdoRegions**: The `CommaDelimitedList` SAM parameter (default `tw`) that is
  the single active-region toggle.
- **Primary region**: Element 0 of `BdoRegions`; the scalar region for consumers
  needing exactly one (POST validation, icon path).
- **Configured-active**: A region present in the deployed `BdoRegions` list.
- **Data-bearing**: A region for which RDS holds market/insights rows.

## Requirements

### Requirement 1: Central region-list toggle

**User Story:** As a platform operator, I want a single central list toggle for
active regions, so that I can control region activation from one declarative
input without changing code or schema.

#### Acceptance Criteria

1. THE system SHALL replace the scalar `BdoRegion` parameter in `template.yaml`
   with a `BdoRegions` CommaDelimitedList parameter (default `tw`) that is the
   single toggle for active regions.
2. THE system SHALL derive the primary region as element 0 of `BdoRegions`
   (`Fn::Select`) and thread it, unchanged as a scalar, to `itemRegistry`
   `POST /v1/items` validation and the icon-path builder (`api.yaml`,
   `cdn.yaml`, `icons.yaml`).
3. WHEN deployed with the default `BdoRegions=tw` THEN the system SHALL produce
   exactly the schedules that exist today (one hourly ETL, one daily and one
   weekly insights, all `region=tw`) and no others, adding zero new cost.
4. WHERE the primary region is `tw` THEN every scalar `BDO_REGION` consumer
   SHALL behave identically to the pre-feature behaviour.

### Requirement 2: Per-region schedule fan-out

**User Story:** As a platform operator, I want per-region schedules generated
from the region list across all pipelines, so that adding or removing a region
activates or deactivates its full pipeline set declaratively.

#### Acceptance Criteria

1. THE system SHALL generate from `BdoRegions` exactly one hourly ETL
   EventBridge rule per region in `infra/etl.yaml`, each starting the ETL state
   machine with input `{"region": "<region>"}`.
2. THE system SHALL generate from `BdoRegions` exactly one daily and one weekly
   insights rule per region in `infra/insights.yaml`, each with input
   `{"region": "<region>", "period": "<daily|weekly>"}`.
3. WHEN a region is added to `BdoRegions` and the stack is deployed THEN the
   system SHALL activate all three pipelines for that region with no manual
   EventBridge step.
4. WHEN a region is removed from `BdoRegions` and the stack is deployed THEN the
   system SHALL delete only that region's rules and leave other regions' rules
   intact.
5. THE system SHALL keep per-region ETL executions independent, such that each
   execution rolls up only its own region's previous UTC day.

### Requirement 3: Region discovery endpoint

**User Story:** As a frontend developer, I want a region discovery endpoint, so
that I can truthfully determine which regions are active and what data is
available for each instead of guessing.

#### Acceptance Criteria

1. THE `marketQuery` service SHALL expose `GET /v1/regions` returning the union
   of configured-active and data-bearing regions, with a top-level `count` and
   per-region `active`, `item_count`, `latest_snapshot_at`, `latest_daily_date`,
   and `has_insights` fields.
2. THE system SHALL set `active` true if and only if the region is in the
   deployed `BdoRegions` list (via the `ACTIVE_REGIONS` env var).
3. THE system SHALL set the freshness fields non-null if and only if RDS holds
   the corresponding rows, so a configured-active region with no data appears
   with `active: true` and null freshness, and a data-bearing region no longer
   configured appears with `active: false`.
4. IF `GET /v1/regions` is called with a `region` filter that is not a member of
   the region enum THEN the system SHALL respond `400`, consistent with the
   existing market endpoints.
5. THE system SHALL include `/v1/regions` in the generated `infra/openapi.yaml`
   and guard it with the existing CI OpenAPI drift check.

### Requirement 4: Preserved guardrails

**User Story:** As a reviewer, I want the existing guardrails preserved, so that
this feature stays scoped to the trigger layer and discovery without disturbing
tracking, item identity, or storage models.

#### Acceptance Criteria

1. THE system SHALL keep tracking global, using the single `tracked` boolean and
   one `tracked-index` GSI.
2. THE system SHALL keep `/v1/items` region-agnostic.
3. THE system SHALL leave the DynamoDB model and the RDS schema unchanged.

### Requirement 5: Cost discipline

**User Story:** As a platform operator, I want cost discipline enforced per
region, so that activating regions stays within the incremental cost cap and
invalid regions are rejected before deploy.

#### Acceptance Criteria

1. THE system SHALL keep each added region within the ≤ ~US$15/month incremental
   cost cap, with the default `[tw]` adding zero new cost.
2. THE spec SHALL include a per-region cost estimate against that cap as a
   required deliverable.
3. THE CI pipeline SHALL validate that every `BdoRegions` entry is a member of
   the `marketQuery` region enum, failing the build before deploy rather than
   creating a rule that feeds an out-of-enum `region`.

### Requirement 6: Operational readiness

**User Story:** As a platform maintainer, I want operational readiness captured
declaratively, so that a region can be activated and verified through a
documented procedure using pure IaC discipline.

#### Acceptance Criteria

1. THE system SHALL include a region-activation-and-verification runbook in
   `docs/runbook.md` (add region → deploy → confirm rules enabled → confirm
   ingestion via `/v1/regions` and the region-aware endpoints → reconcile spend,
   plus rollback) as a required deliverable.
2. THE system SHALL generate per-region rules declaratively via `Fn::ForEach`
   (`AWS::LanguageExtensions`), keeping the change within the trigger layer.
3. THE system SHALL introduce no new deploy-time Lambda or macro, and SHALL keep
   exactly one SAM `template.yaml` and one CI workflow.

## Out of Scope

- Actually ingesting a second region's data (readiness-first; activation is a
  separate gated step per the runbook).
- Per-region tracking, per-region `/v1/items`, or any DynamoDB/RDS schema change.
- Per-region schedule minute-staggering and region-dimensioned skip metrics
  (documented as deferred options in the design, not required here).
