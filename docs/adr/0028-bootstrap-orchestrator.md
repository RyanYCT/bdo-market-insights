# ADR-0028: Bootstrap orchestrator (first-create data seeding)

## Status

Accepted (implements ADR-0024 item 4)

## Context

Populating a fresh environment with data was a manual, ordered chain: catalog
sync (arsha `util/db` -> items table), then the curated tracked-set seed, then
icon materialization. Only the catalog and icon steps had Lambdas (on weekly /
daily EventBridge schedules); the tracked-set seed was **script-only**
(`scripts/seed_items.py`, run locally against DynamoDB). So bringing up an
environment meant running `make seed-data` from a laptop and waiting for (or
manually invoking) the icon sync — a human checklist ADR-0024 set out to remove.

The steps have a real order dependency: the tracked seed derives categories from
the committed catalog snapshot and marks items tracked, and icon sync only
processes tracked items — so it must run after the seed.

## Decision

Add a **`bootstrap` Step Functions** state machine that runs the chain, auto-runs
once on a fresh environment, and is re-runnable on demand. All steps are
idempotent.

**1. State machine (`bdo-<stage>-bootstrap`).** `CatalogSync -> SeedTracked ->
IconSync`, each a Task with the standard Lambda retry block. It reuses the
existing `catalogSync` / `iconSync` Lambdas (invoked cross-stack by ARN).

**2. New `seedTracked` Lambda.** The tracked-set seed was script-only; a Step
Functions task needs a Lambda. `seedTracked` reuses the pure logic in
`bdo_common.tracking` + `dynamo.bulk_update_items` and **bundles the committed
data files** (`scripts/data/*.json`) into its package at build time — same
pattern as the migrator bundling `migrations/`. The tracked set is
reviewed, git-versioned configuration that should change only via a PR +
redeploy, so bundling (atomic with code, no drift, no extra store) is preferred
over S3. `scripts/seed_items.py` stays for local authoring.

**3. First-create auto-run, fire-and-forget.** A `BootstrapTrigger` custom
resource (backed by a small Lambda), gated by an `AutoBootstrap` parameter
(default `true`), starts the state machine **on stack Create only**, and only if
the items table is empty (a `Scan(Limit=1)` guard — the direct truth of "is
there data?", not a proxy). It calls `StartExecution` and signals CloudFormation
success **immediately** — it does **not** wait for seeding to finish. Data
bootstrap is not gating for infra correctness (the API serves empty results
meanwhile, and the schedules would populate eventually), so waiting inside a
custom resource — with its ~1h timeout — would be the wrong trade. `Update` and
`Delete` are no-ops; the trigger always signals CloudFormation so the stack
can't hang.

**4. On-demand re-run.** `make bootstrap STAGE=<env>` starts the same state
machine. Idempotent, so it is safe to re-run (e.g. after editing the tracked
set and redeploying `seedTracked`).

**5. Schedules unchanged.** The weekly catalog and daily icon EventBridge
schedules keep data fresh; the bootstrap just runs those same Lambdas
immediately on first create and on demand. The tracked seed intentionally has
**no schedule** — the curated set changes only by deliberate edit, never on a
timer.

## Consequences

- (+) A fresh environment seeds itself on first deploy with no human checklist;
  re-seeding is one command.
- (+) The deploy never blocks on data population (fire-and-forget); a brand-new
  environment's `/v1/items` is simply empty for the minute or two the bootstrap
  runs.
- (+) The tracked set stays git-versioned and reviewed; applying it is a Lambda,
  not a laptop script.
- (-) A genuine failure to *start* the bootstrap (e.g. IAM) surfaces as a FAILED
  custom resource on the introducing deploy — deliberate, so misconfig is
  visible; the state machine's own run failures do not fail the deploy.
- (-) `full_items.json` (~1.1 MB) is bundled into `seedTracked`; well within
  Lambda limits, but changing the tracked set requires a redeploy (intended).
- (-) The first-create trigger is another Lambda-backed custom resource;
  introducing it to an existing environment should be treated with the same
  two-phase care as ADR-0025's, though the risk is lower (it fires async and
  signals immediately, and the function is new rather than a reverted version).

## Notes

Implements ADR-0024 item 4. Builds on ADR-0018 (DynamoDB item registry;
catalog-owned vs ETL-owned attributes), ADR-0025 (custom-resource + always-signal
pattern), and the existing catalog/icon sync Lambdas.
