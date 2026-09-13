# ADR-0036: Region-list central toggle with per-region schedule fan-out

## Status

Accepted

## Context

The data plane is already region-partitioned and region-aware (v3): `region`
leads the composite primary keys of `item_sid`, `market_snapshot`,
`market_daily`, and `market_summary`; the read API takes a `region` query param
backed by a canonical enum; and the ETL/insights handlers thread `region` from
their execution input. The one place still pinned to a single region was the
**trigger layer**: `infra/etl.yaml` and `infra/insights.yaml` each declared
inline EventBridge schedules bound to a scalar `BdoRegion` CloudFormation
parameter (default `tw`). Activating another region meant hand-editing rules.

We want activating a region to be a declarative, central, one-line operation,
while keeping the default (`tw`) behaving exactly as before with zero new cost
(readiness-first). A few consumers legitimately need exactly one region and sit
outside the trigger layer: `itemRegistry` `POST /v1/items` validates ids against
a single region, and the icon-path builder uses a single `BDO_REGION`.

SAM/CloudFormation cannot loop a `AWS::Serverless::StateMachine` `Events` block
over a list, so generating one schedule per region needs a mechanism. Options
considered:

1. **Hand-write one schedule block per region.** Rejected: not declarative from
   a list; every activation is a manual template edit.
2. **A custom CloudFormation macro (Lambda).** Rejected: a deploy-time Lambda to
   own, secure, and keep available on every deploy — heavyweight for a loop.
3. **A `dict[str, bool]` / CloudFormation `Mappings` map-toggle** (unique by
   construction). Rejected: CloudFormation has no map parameter type, and it
   complicates deriving the `ACTIVE_REGIONS` CSV that `/v1/meta` needs.
4. **`Fn::ForEach` via `AWS::LanguageExtensions`.** A native intrinsic loop over
   a list parameter, expanded at transform time with no extra runtime.

A wave-0 feasibility spike confirmed option 4: layering `AWS::LanguageExtensions`
ahead of `AWS::Serverless-2016-10-31` in the `Transform` list expands
`Fn::ForEach` over a `CommaDelimitedList` and passes `cfn-lint`.

## Decision

Replace the scalar `BdoRegion` trigger parameter with a **`BdoRegions`
`CommaDelimitedList`** (default `tw`) that is the single active-region toggle,
and generate per-region schedules with `Fn::ForEach`.

- **Primary region = element 0** (`!Select [0, !Ref BdoRegions]`), threaded
  unchanged as a scalar to the `api`/`cdn`/`icons` stacks. Item identity is
  global, so validating a `POST` against the primary region is sufficient; with
  the default `[tw]` every scalar consumer behaves exactly as before.
- **Per-region fan-out.** `etl.yaml` generates one hourly rule per region
  (`cron(7 * * * ? *)`, input `{"region": "<region>"}`); `insights.yaml`
  generates one daily (`cron(0 1 * * ? *)`) and one weekly
  (`cron(15 1 ? * MON *)`) rule per region, each with `{"region", "period"}`.
  The inline state-machine schedules are removed, so each state machine stays a
  single resource. The generated logical id uses `&{RegionName}`
  (alphanumeric-stripped, so `console_eu` yields a valid id) while the rule name
  and input keep the exact region string.
- **Shared schedule role.** One EventBridge→`states:StartExecution` IAM role per
  stack, scoped to that stack's state-machine ARN, backs every generated rule
  (replacing SAM's auto-created per-schedule rule+role). The rule targets set no
  `RetryPolicy` and no `DeadLetterConfig`: an idempotent hourly job simply
  re-fires on the next window.
- **Single source of the toggle.** `samconfig.toml` holds `BdoRegions` for each
  stage. Because a CLI `--parameter-overrides` replaces the whole parameter set,
  both `make deploy` and the CI prod deploy re-read `BdoRegions` from
  `samconfig.toml` (via `scripts/samconfig_regions.py`) rather than hardcoding
  it — no inline region literal remains in `ci.yml`.
- **Uniqueness + enum membership are guard-enforced.** A `CommaDelimitedList`
  cannot carry `AllowedValues`, so a CI guard (`scripts/validate_regions.py`)
  fails the build before deploy if any configured region is a duplicate or not a
  member of the canonical marketQuery `Region` enum (the single source).

## Consequences

- (+) Activating or deactivating a region is a one-line `samconfig.toml` edit
  plus a deploy; CloudFormation creates or deletes exactly that region's rules
  and leaves the others intact.
- (+) The default `[tw]` produces byte-for-byte the schedules that existed
  before, adding zero new cost; the mechanism is provable without turning on any
  region.
- (+) The change stays inside the trigger layer: no new deploy-time Lambda or
  macro, still one root `template.yaml` and one CI workflow.
- (−) Adds the `AWS::LanguageExtensions` transform to two nested templates.
  CI does not lint nested templates via `sam validate`, so the expansion is
  covered by a pytest IaC test that drives the same transform and asserts the
  generated rule set.
- (−) All N region rules for a pipeline fire on the same cron minute, so peak
  concurrent ETL Lambdas ≈ `N × Map MaxConcurrency` and upstream (arsha) load is
  multiplied at one instant. Well within limits at the target region count, and
  the resilient fetch tolerates partial upstream failure. A per-region minute
  offset would mitigate it, but `Fn::ForEach` exposes no numeric index, so a
  staggered cron would need an explicit per-region minute map — deferred.

## Notes

- Per-region incremental cost is ≈ US$1–2/month (dominated by ETL Step Functions
  state transitions), comfortably inside the ≤ ~US$15/month cap for a handful of
  regions; RDS row growth is bounded by the 90-day purge. See the "Region
  activation" runbook for the estimate and reconciliation step.
- `ACTIVE_REGIONS` (the comma-joined toggle) is also injected into `marketQuery`
  so `GET /v1/meta` can report which regions are active (ADR-0037).
- Actually ingesting a second region's data is an explicit, gated operational
  step (the runbook), not part of this decision.
