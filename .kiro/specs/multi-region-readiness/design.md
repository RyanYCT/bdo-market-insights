# Multi-region readiness — Design

> Design-first spec. Proposes ADR-0036 (region-list central toggle) and
> ADR-0037 (`/v1/regions` discovery endpoint) — authored separately under
> `docs/adr/`; the authoritative IaC detail will live in ADR-0036 and the code.

## Overview

Most of the data plane is **already region-ready**; this feature does not
rebuild it. Grounding facts, confirmed against the code:

- **Schema is region-partitioned.** `region` is the leading column of the
  composite primary keys of `item_sid`, `market_snapshot`, `market_daily`
  (`0001_initial.py`) and `market_summary` (`0004_market_summary.py`); read
  indexes are `(region, item_id, …)` / `(region, period, summary_date)`. No
  schema change is needed.
- **The read API is already region-aware.** `market_query/app.py` exposes a
  `region` query param on the market and `/v1/insights` routes, backed by a
  13-value enum (`tw, na, eu, sea, mena, kr, ru, jp, th, sa, console_eu,
  console_na, console_asia`), default `tw`, `400` on an unknown value, already
  in `infra/openapi.yaml`. Regions with no data return empty, not error.
- **The ETL is already region-parameterised.** `retrieve_items`, `fetch_data`,
  `clean_data`, `store_data`, `rollup_daily` thread `region` from the execution
  input; `ArshaClient(region=…)` fetches per region.
- **Tracking is global by design** and stays that way: `Item.tracked` is one
  boolean; `bdo_common/dynamo.py` uses a single sparse `tracked-index` GSI
  (`t=1`); `retrieve_items` reads that one global set for whatever `region` the
  schedule passes.

The **only** single-region pin is the **trigger layer**: `infra/etl.yaml` and
`infra/insights.yaml` each define one inline EventBridge schedule with input
`{"region": "${BdoRegion}"}`, bound to the scalar CloudFormation parameter
`BdoRegion` (default `tw`). Today, activating another region is a manual rule
edit. This feature makes activation **declarative and central** and adds a
truthful **data-availability discovery** endpoint.

### Goals

1. Replace the scalar `BdoRegion` trigger toggle with a **region list** that
   generates **one schedule per region across every pipeline** — hourly ETL
   (`etl.yaml`) and daily + weekly insights (`insights.yaml`) — with no manual
   per-region steps.
2. Add `GET /v1/regions` so the frontend can discover **active regions and
   per-region data presence** instead of guessing.
3. **Readiness-first:** provable without turning on any new region (default
   stays `[tw]`), within the **≤ ~US$15/month** cost cap. Activating a real
   second region is an explicit, gated final step.

### Non-goals

- No change to tracking (stays global), to `/v1/items` (region-agnostic — item
  identity is global), to the DynamoDB model, or to the RDS schema.
- Not required for "done": actually ingesting a second region's data.

---

## Architecture

Only the **trigger layer** changes shape; compute, data stores, and the read
API are unchanged. The central `BdoRegions` list fans EventBridge out to one
schedule per region per pipeline; each schedule starts an independent execution
carrying its own `region`.

```mermaid
flowchart LR
    subgraph toggle["Central toggle (IaC)"]
        list["BdoRegions<br/>CommaDelimitedList<br/>default [tw]"]
    end

    subgraph eb["EventBridge (one rule per region, generated)"]
        etlR["ETL rules<br/>cron :07 hourly × region"]
        dailyR["Insights daily × region"]
        weeklyR["Insights weekly × region"]
    end

    subgraph pipe["Pipelines (unchanged code)"]
        etlSM["ETL state machine<br/>input {region}"]
        insSM["Insights state machine<br/>input {region, period}"]
    end

    rds[("RDS Postgres<br/>region-partitioned")]
    client(["Frontend"])
    mq["marketQuery (in-VPC)"]

    list --> etlR --> etlSM
    list --> dailyR --> insSM
    list --> weeklyR --> insSM
    etlSM --> rds
    insSM --> rds
    client -->|"GET /v1/regions"| mq
    client -->|"GET /v1/market?region=…"| mq
    mq -->|"per-region presence"| rds
    list -.->|"ACTIVE_REGIONS env"| mq
```

Each region gets an **independent** execution, so per-region behaviour already
works: `retrieve_items` computes `is_day_first_run` from its own execution's
`snapshot_at` hour and `rollup_daily` is keyed by `region`, so N regions ⇒ N
independent day-first rollups. `purge_old_snapshots` runs on its own daily
schedule and deletes by age across all regions — region-agnostic, no fan-out.

---

## Components and Interfaces

| Component | Change | Kind |
|-----------|--------|------|
| `template.yaml` | `BdoRegion` scalar → `BdoRegions` list (the one toggle); primary = element 0 | IaC |
| `infra/etl.yaml` | One hourly ETL schedule **per region** from the list | IaC |
| `infra/insights.yaml` | One daily + one weekly schedule **per region** from the same list | IaC |
| `marketQuery` (`market_query/app.py`) | New `GET /v1/regions` route + models; reads `ACTIVE_REGIONS` env | Code |
| `bdo_common` repositories | New `RegionRepo.region_availability(conn)` (read-only aggregate) | Code |
| `infra/openapi.yaml` | Regenerated to include `/v1/regions` (CI drift-checked) | Generated |
| `api.yaml`, `cdn.yaml`, `icons.yaml` | Unchanged shape — keep receiving a **scalar** primary region | IaC |

### Primary region vs. the region list

Some consumers legitimately need **one** region and are out of the trigger
layer: `itemRegistry` `POST /v1/items` validates ids against a single
`settings.region`; `iconSync`/`cdn` build the Pearl Abyss icon path from a
single `BDO_REGION`. To keep **one** central toggle, the **primary region is
element 0 of `BdoRegions`** (`Fn::Select [0, !Ref BdoRegions]`). With the
default `[tw]` the primary is `tw`, so the scalar `BDO_REGION` env and every
existing consumer behave exactly as today. Item identity is global, so
validating a `POST` against the primary region is sufficient (the item is then
polled in every active region by the global tracked set).

### Data-availability discovery: `GET /v1/regions`

**Endpoint choice — top-level `/v1/regions`, not `/v1/market/regions`.** Region
availability is a cross-cutting discovery resource, not a sub-resource of one
item's time series. `marketQuery` already owns top-level routes beyond
`/v1/market/*` (it serves `/v1/insights`) and is the **only in-VPC RDS reader**,
so hosting `/v1/regions` there adds **no new VPC Lambda**; `itemRegistry` runs
outside the VPC and cannot read RDS. It answers *"what can I show for region
X?"* by combining two truths: **configured-active** (in the deployed
`BdoRegions` list, surfaced via `ACTIVE_REGIONS`) and **has data** (does RDS hold
market/insights rows, and how fresh). A region can be active with no data yet, or
have historical data but no longer be active; reporting both is the truthful
answer.

### IaC: region list → N schedules

**Parameter shape.** `template.yaml` declares `BdoRegions` as a
`CommaDelimitedList` (default `tw`); each entry must be a marketQuery enum value
(validated in CI). The scalar `BdoRegion` input is removed and derived where a
single region is still needed:

```yaml
BdoRegions: {Type: CommaDelimitedList, Default: tw}   # element 0 = primary
BdoRegion:  !Select [0, !Ref BdoRegions]              # -> api/cdn/icons (scalar)
# nested-stack list params cross the boundary as a comma-joined string:
BdoRegions: !Join [',', !Ref BdoRegions]              # child re-declares list
```

`samconfig.toml` keeps a one-line toggle
(`BdoRegions=tw`; a second region is `BdoRegions=tw,na`).

**Generating N schedules — mechanism choice.** SAM/CloudFormation cannot loop a
`AWS::Serverless::StateMachine` `Events` block over a list. Options:

| Option | Verdict |
|--------|---------|
| Hand-write one schedule block per region | Rejected — not declarative from a list. |
| Custom CloudFormation macro (Lambda) | Rejected — heavyweight deploy-time Lambda to own/secure. |
| **`Fn::ForEach` via `AWS::LanguageExtensions`** | **Recommended** — native intrinsic looping over a list param, no extra runtime, pure IaC. |

**Recommendation: `Fn::ForEach`.** Add `AWS::LanguageExtensions` to each
template's `Transform` list and generate one `AWS::Events::Rule` per region
targeting the existing state machine. The state machine stays a single resource
(its inline `Schedule` event is removed); generating **rules** rather than
looping SAM `Events` isolates the change to the trigger layer. `etl.yaml`
produces one hourly rule per region (`cron(7 * * * ? *)`, input
`{"region": "<RegionName>"}`); `insights.yaml` applies the same pattern twice —
a daily loop (`cron(0 1 * * ? *)`, `period=daily`) and a weekly loop
(`cron(15 1 ? * MON *)`, `period=weekly`). A single shared
EventBridge→StartExecution IAM role per stack (scoped to that stack's
state-machine ARN) backs all generated rules. Replacing the auto-created SAM
schedule rule+role with explicit resources is deliberate so the set can be
generated from the list; cron and input are preserved per region.

**Concurrency & cost of N parallel executions.** All N region rules for a
pipeline fire on the **same** cron minute, so up to N executions start together.
Step Functions Standard concurrency is far above any plausible N; the Map
state's `MaxConcurrency: 5` bounds Lambda fan-out **per execution**, so peak
concurrent ETL Lambdas ≈ `N × 5` — well under the default 1000. **arsha
contention:** firing every region at `:07` multiplies upstream load at one
instant, but the resilient fetch tolerates partial failure. Mitigation if needed
is a per-region minute offset, but `Fn::ForEach` exposes no numeric index so a
staggered cron would need an explicit per-region minute map — deferred (not
needed at the target region count), documented as a conscious choice.

### Endpoint contract: `GET /v1/regions`

Added to `market_query/app.py` (in-VPC, reads RDS via IAM auth using the
existing `_reading()` rollback context). No required params; an optional
`region` filter narrows to one row, `400` on an unknown value (matching the
existing enum contract).

```jsonc
// GET /v1/regions
{ "count": 2, "regions": [
  { "region": "tw", "active": true, "item_count": 312,
    "latest_snapshot_at": "2025-01-01T12:00:00Z",
    "latest_daily_date": "2024-12-31", "has_insights": true },
  { "region": "na", "active": true, "item_count": 0,
    "latest_snapshot_at": null, "latest_daily_date": null,
    "has_insights": false }        // configured-active, no data ingested yet
]}
```

`active` is computed from the `ACTIVE_REGIONS` env var (comma-joined
`BdoRegions`, injected for the `marketQuery` function). The row set is the
**union** of configured-active regions and regions with any RDS data, so both
"activated but empty" and "has history but deactivated" are visible. Because the
tables are region-partitioned with `(region, …)` leading indexes, the
`GROUP BY region` aggregates are cheap.

### Files touched

```
template.yaml                    # BdoRegion scalar -> BdoRegions list; derive primary via Fn::Select
infra/etl.yaml                   # + AWS::LanguageExtensions; Fn::ForEach hourly rules; shared schedule role
infra/insights.yaml              # + AWS::LanguageExtensions; Fn::ForEach daily+weekly rules; shared role
infra/api.yaml                   # marketQuery: + ACTIVE_REGIONS env (still receives scalar BdoRegion=primary)
src/functions/market_query/app.py            # + get_regions route + RegionAvailability/RegionsResponse
src/layer/python/bdo_common/repositories.py  # + RegionRepo.region_availability
infra/openapi.yaml               # regenerated (scripts/export_openapi.py); CI drift check
docs/adr/0036-*.md, docs/adr/0037-*.md       # authored in a later phase
```

No change to `dynamo.py`, the migrations, `item_registry/app.py`, or the ETL
function handlers.

### Cross-region item availability

The **global** tracked set is polled in **every** active region, but the upstream
may not carry (or may block) some items in some regions. Already handled — nothing
new in the data path: `fetch_data` uses `ArshaClient.fetch_raw_resilient`
(adaptive bisect) — individually blocked items are **dropped** into `failed_ids`
and the rest stored, so a dropped item simply has **no snapshot** for that hour in
that region (exactly the "not available here" signal, reflected as absent data by
`/v1/regions` and the region-aware endpoints). Drop count is emitted as
`MarketItemsSkipped` (sustained drops trip the existing skip alarm; total upstream
failure fails the stage loud via `ExecutionsFailed`).

**Optional observability enhancement (recommended, low-cost):**
`MarketItemsSkipped` is not currently dimensioned by region; adding a `region`
dimension would distinguish per-region skip rates on the dashboard once multiple
regions are active. Additive; can be scoped into Tasks or deferred.

### Cost model (hard cap ≤ ~US$15/month)

The RDS instance is the fixed budget anchor and runs regardless of region count;
**per-region incremental** cost is what matters. Assumptions (round, not
measured): ~300 tracked items, ~3 sids average (~900 series/region), batch size
50 ⇒ ~18 batches ⇒ ~56 Step Functions state transitions/ETL run, 720 ETL
runs/month plus ~34 insights runs.

**Conclusion:** per added region ≈ **$1–2/month**, dominated by ETL Step
Functions transitions (~$1.0; ~40k transitions/mo at Standard ~$0.025/1k after
the free tier). ETL Lambda, insights compute, and Bedrock Nova Lite are each
sub-$0.30; arsha calls carry no AWS cost. RDS row growth is ~$0.03/mo and
**bounded by the 90-day purge** so it never compounds.

**Headroom.** With RDS + shared platform as the fixed base, activating a handful
of regions (e.g. 2–4) stays comfortably inside the ≤ ~US$15/month cap. The
default `[tw]` adds **zero** new cost. Reconcile actuals against Cost Explorer
after a real region is activated (see runbook).

### Runbook: activate one region + verify

New "Region activation" procedure (authored in full in `docs/runbook.md` during
Tasks; outline here):

1. Add the region to `BdoRegions` and deploy (`make deploy STAGE=prod`,
   `BdoRegions=tw,na`) — CloudFormation creates the per-region rules; no manual
   EventBridge edits.
2. Confirm generated rules exist and are `ENABLED` (`bdo-<stage>-etl-na`,
   `…-insights-daily-na`, `…-weekly-na`).
3. After the next `:07` window, confirm one execution ran and check
   `MarketItemsSkipped`.
4. Verify data: `GET /v1/regions` shows `na` `active:true` with non-null
   `latest_snapshot_at`; `…/snapshots?region=na` returns rows.
5. After ~24h confirm `rollup_daily` produced `market_daily`; after the daily
   insights window confirm `has_insights:true`.
6. Reconcile incremental spend against the cost model.

**Rollback:** remove the region from `BdoRegions` and redeploy — per-region rules
are deleted; historical rows remain (queryable, `active:false`) until the 90-day
purge ages them out.

---

## Data Models

Response models (Powertools/pydantic, so they land in `infra/openapi.yaml`):

```python
class RegionAvailability(BaseModel):
    region: str                       # enum member
    active: bool                      # in the deployed BdoRegions list
    item_count: int                   # distinct item_ids with any snapshot
    latest_snapshot_at: datetime | None
    latest_daily_date: date | None
    has_insights: bool                # any market_summary row exists

class RegionsResponse(BaseModel):
    regions: list[RegionAvailability]  # union of configured + data-bearing
    count: int
```

New read-only aggregate in `bdo_common.repositories` (co-located with
`SnapshotRepo`/`DailyRepo`), keeping SQL out of the handler:

```python
class RegionRepo:
    @staticmethod
    def region_availability(conn) -> dict[str, RegionPresence]:
        """Per-region presence across snapshot/daily/summary, keyed by region.

        One pass per table, merged in Python (regions are few):
          market_snapshot -> COUNT(DISTINCT item_id), MAX(snapshot_at)
          market_daily    -> MAX(trade_date)
          market_summary  -> bool(any row)
        Read-only; the caller's _reading() context rolls back.
        """
```

The handler merges `region_availability` with the `ACTIVE_REGIONS` set to
produce `RegionsResponse`.

---

## Correctness properties

- **P1 (readiness / default-off):** deploying with the default `BdoRegions=tw`
  produces exactly the schedules that exist today (one hourly ETL, one daily +
  one weekly insights, all `region=tw`) and no others ⇒ zero new cost.
- **P2 (fan-out):** for a list of N distinct regions, the template generates
  exactly N hourly ETL rules, N daily insights rules, and N weekly insights
  rules, each with `Input` region equal to its list entry.
- **P3 (activation is declarative):** adding a region to `BdoRegions` and
  deploying activates all three pipelines for it with no manual step; removing
  it deletes only that region's rules.
- **P4 (primary invariance):** with element 0 = `tw`, every scalar `BDO_REGION`
  consumer (itemRegistry POST validation, icon path) is byte-for-byte unchanged
  from pre-feature behaviour.
- **P5 (discovery truthfulness):** `GET /v1/regions` reports `active=true` iff
  the region is in the deployed list, and non-null freshness fields iff RDS holds
  the corresponding rows; a region with data but not in the list appears with
  `active=false`.
- **P6 (unknown region contract):** `GET /v1/regions?region=<not-in-enum>`
  returns `400`, consistent with the existing market endpoints.
- **P7 (rollup independence):** each region's ETL execution rolls up only its own
  region's previous UTC day.

## Error handling

- **Bad region in the list at deploy:** entries are validated against the enum in
  CI before deploy (see Testing); an unknown value fails the build rather than
  creating a rule that feeds an out-of-enum `region` downstream.
- **`/v1/regions` DB unavailable:** same failure mode as the other in-VPC read
  routes (5xx counted by the API SLO alarm); no partial/stale cache is invented.
- **Per-region upstream blocks:** handled by the resilient fetch; never fails a
  whole region's run for individually blocked items.

## Testing strategy

- **Unit:** `RegionRepo.region_availability` merge logic (data-bearing vs.
  configured-active union); `get_regions` handler mapping incl. the `400` path;
  `active` derivation from `ACTIVE_REGIONS`.
- **IaC:** `cfn-lint` over the `Fn::ForEach`-expanded templates; a test that the
  expansion yields N rules for a sample multi-region list and exactly the baseline
  set for `[tw]` (P1/P2); a CI check that every `BdoRegions` entry is a member of
  the marketQuery region enum.
- **Contract:** `scripts/export_openapi.py` regenerates `infra/openapi.yaml` with
  `/v1/regions`; the existing CI drift check guards it.
- **Property-based** (repo convention): P2 over random distinct-region lists —
  generated rule count and per-rule input region match the list exactly.

## ADRs (proposed — authored in a later phase)

Following the existing sequence (highest is `0035`):

- **ADR-0036 — Region-list central toggle.** Records replacing the scalar
  `BdoRegion` with a `BdoRegions` list and generating per-region schedules via
  `Fn::ForEach` (`AWS::LanguageExtensions`) across `etl.yaml` and
  `insights.yaml`; primary region = element 0 for scalar consumers; default
  `[tw]`. Alternatives (hand-written blocks, custom macro) and the same-minute
  concurrency trade-off captured. Holds the authoritative IaC detail.
- **ADR-0037 — `/v1/regions` data-availability discovery endpoint.** Records
  hosting region discovery on the in-VPC `marketQuery` (owner of the only RDS
  read path and of `/v1/insights`) as a top-level resource, reporting
  configured-active ∪ data-bearing regions with per-region freshness.
