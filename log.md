# Session Log

Append-only chronological record of significant agent-driven work on
this repository. Newer entries at the bottom. Never edit historical
entries; correct mistakes by appending a follow-up entry.

One entry per Kiro session. Skip trivial sessions (single typo fix,
pure code review, exploration that didn't change anything).

Each entry uses the template below; aim for ≤ 200 words.

---

## Template

```
## YYYY-MM-DD — <session title>

**Agent:** <name>
**Mode:** <Spec | Vibe | Autonomous>
**Branch:** <branch name>
**Phase:** <phase from .kiro/specs/v3/tasks.md>
**Commits:** <SHA list or PR link>

### Done
- ...

### Decisions
- ... → ADR-NNNN (or "no ADR — local choice")

### Deferred / open questions
- ...
```

---

## 2026-05-29 — v3 design and initial specs

**Agent:** Kiro
**Mode:** Spec (started in Vibe; transitioned once we began writing `.kiro/specs/v3/`)
**Branch:** `redesign-v3` (orphan, created this session)
**Phase:** 0 — Branch & specs
**Commits:** `d2ad0fe`, `6d6bc38`, `e00386b`, `096ce4a`, `0e49f11`, plus this entry

### Done
- Audited existing `main` and `rewrite-project`; documented the
  rewrite's failure modes.
- Designed v3 from scratch: 8 Lambdas, Step Functions ETL, 4-table
  Postgres schema (`item`, `item_sid`, `market_snapshot`,
  `market_daily`), region-aware PK, DynamoDB-only item registry with
  lazy Postgres population, EICE bastion for pgAdmin, single-AZ
  workload.
- Wrote `.kiro/specs/v3/{requirements,design,tasks}.md`, `AGENTS.md`,
  three `.kiro/steering/*.md` files, and this `log.md`.
- Created the orphan `redesign-v3` branch.

### Decisions
- ADR-0001 SAM over CDK
- ADR-0002 RDS Proxy opt-in; module-global psycopg by default
- ADR-0003 Single shared Lambda Layer `bdo-common`
- ADR-0004 Step Functions ETL with per-stage retry
- ADR-0005 API key + usage plan; Cognito deferred
- ADR-0006 Mixed VPC placement; no NAT
- ADR-0007 AWS Lambda Powertools over hand-rolled utilities
- ADR-0008 IAM database auth for Lambdas
- ADR-0009 EICE bastion for human DBA access
- ADR-0010 Lazy `item` table population by ETL; Streams sync deferred
- ADR-0011 Single-AZ workload
- No SKILL.md / `.kiro/skills/` for now — defer until lived experience
  identifies a recurring procedure (no ADR; AGENTS.md "Phase
  completion check" enforces revisiting this each phase)

### Deferred / open questions
- Real BDO enhancement probabilities for accessories — placeholder
  values to land in `rates.json`; user to provide.
- Phase 7 cutover: archive `rewrite-project`, tag `archive/main-v1`,
  force-replace `main` — explicit approval required at that point.
- Phase 2: DynamoDB Streams-driven item sync remains deferred per
  ADR-0010.

---

## 2026-05-30 — Phase 1 scaffolding + Phase 2 infrastructure

**Agent:** Kiro
**Mode:** Autonomous (with Vibe acceptance reviews between phases)
**Branch:** `redesign-v3`
**Phase:** 1 — Project scaffolding; 2 — Network, data, bastion infrastructure
**Commits:** `2add18e`..`59663fb` (Phase 1 + cleanup), `a920446`..`84a2477` (Phase 2 + cleanup)

### Done
- Phase 1: `pyproject.toml` (uv), `Makefile`, single CI workflow,
  SAM `template.yaml` skeleton (4 params), `samconfig.toml`, 11 ADRs,
  runbook/SLO/architecture docs, license/gitignore/README.
- Phase 1 acceptance review → fixed: OIDC `id-token` perm, pip-audit +
  bandit jobs, dropped premature `sam validate`, dead Conditions,
  boto3→dev-deps, `BdoRegion` AllowedValues (corrected to the 13
  arsha.io regions).
- Phase 2: `infra/{network,data,bastion}.yaml`, 3 stub stacks, Alembic
  + `0001_initial` (4 tables) + `0002_bootstrap_roles`,
  `scripts/seed_items.py`, restored `sam validate`.
- Phase 2 acceptance review → fixed 4 blockers + majors: W3005/W3691
  (sam validate green), bastion port-22 ingress, removed unreachable
  CI migration step (bastion-tunnel flow instead), created the
  `lambda_rds_user`/`dba` roles, seed camelCase + sparse-GSI fixes.

### Decisions
- Migrations run via the bastion tunnel (`make migrate`), not CI —
  private RDS is unreachable from a GitHub runner. In-VPC migrator
  Lambda deferred to Phase 4. (no ADR — local choice, documented in
  runbook + tasks.md)
- RDS engine pinned to Postgres `16.8` (bare major `16` is deprecated).
- Forward-declared params (`UseRdsProxy`, stub-stack interfaces) kept
  with scoped cfn-lint suppressions rather than removed.

### Deferred / open questions
- `db-tunnel-up` resolves the RDS endpoint from a nested-stack output;
  if fragile in practice, promote `RdsEndpoint` to a root-stack output.
- Real BDO enhancement probabilities still needed for `rates.json`
  (Phase 3).
- Phase 3 `pricing.py`/`analytics.py` domain math to be designed before
  coding.


---

## 2026-05-31 — Phase 3 domain math: pricing + analytics

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 3 — Shared layer (`bdo-common`)
**Commits:** `d1fe1b0`, `e6cb9da`, `a02ed86`, `1de2cec`

### Done
- Recovered the v3 domain-model lock parked by a prior bash-outage
  session: re-applied and committed the six files (`domain-model.md`,
  `rates.json`, ADR-0012, `seed_items.py`, `design.md`, `tasks.md`).
- Built the pure domain math: `pricing.py` (`accessory_v1` model +
  `model_id`->factory registry, ADR-0012) and `analytics.py`
  (volatility/CV, liquidity, z-score anomaly). Unit tests assert the
  `domain-model.md` worked numbers. ruff + mypy(strict) + 32 pytest green.

### Decisions
- Kept direct-commit-to-`redesign-v3` flow (Option A); reworded
  `tasks.md` "each box is one PR" to match — no ADR (local choice;
  aligns with AGENTS.md).
- Probability at/above the soft-cap breakpoint uses `soft_cap_rate`
  (`2->3@44` = 0.40, not 0.405) per domain-model prose — no ADR.
- Cumulative `clean -> sid:N` cost uses a self-build recursion (no
  verified spec number) — no ADR; flagged for confirmation.

### Deferred / open questions
- Phase 3 remaining: `arsha_client` + normalizer, `models.py`, `db.py`,
  `dynamo.py`, `repositories.py`, `config.py`, normalizer/repo tests.
- Confirm the two interpretation calls above (breakpoint; cumulative).
- `rates.json` curves/cron counts still placeholders pending live-TW
  verification.

---

## 2025-07-14 -- Phase 3 shared layer: remaining modules

**Agent:** Kiro
**Mode:** Autonomous
**Branch:** `redesign-v3`
**Phase:** 3 -- Shared layer (`bdo-common`)
**Commits:** `a916a62`..`15d3835`

### Done
- Implemented all remaining Phase 3 modules: `models.py` (Pydantic v2
  schemas), `config.py` (env reader + Powertools parameters cache),
  `arsha_client.py` (HTTP client + normalizer for 5 arsha.io response
  shapes), `db.py` (psycopg3 module-global connection, IAM-auth aware),
  `dynamo.py` (typed DynamoDB wrappers), `repositories.py` (parameterized
  SQL repos for item/item_sid/snapshot/daily).
- Added unit tests for normalizer, models, config, dynamo, and
  repositories. All tests passing with ruff + mypy strict.

### Decisions
- Used stdlib `urllib.request` for arsha_client HTTP (no new dependency
  needed) -- no ADR (local choice).
- Repository methods accept `psycopg.Connection` as parameter for
  testability (dependency injection) -- no ADR (standard pattern).
- DynamoDB `tracked` stored as string "true"/"false" matching seed
  script convention -- no ADR (inherited from Phase 2).

### Deferred / open questions
- Property-based tests for normalizer deferred (hypothesis not in deps).
- Integration tests with real Postgres deferred to Phase 4 (CI
  ephemeral Postgres).


---

## 2026-06-01 — Fix arsha_client to parse real arsha.io v2 JSON

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 3 — Shared layer (`bdo-common`)
**Commits:** see `redesign-v3` (follow-up to `5bb2370`)

### Done
- Review of `redesign-v3` confirmed Phases 0–3 align with the spec and
  the local gate is green (ruff, mypy strict, pytest).
- Found and fixed a contract mismatch: `arsha_client.normalize_response`
  parsed the legacy pipe-delimited `resultCode`/`resultMsg` string (the
  raw official BDO API), but the spec targets `api.arsha.io/v2`, which
  returns JSON. Verified the real shape against the original `main`
  `fetchData`/`cleanData`/`storeData` (camelCase keys; list-of-lists).
- Rewrote the normalizer to recursively flatten arsha's polymorphic JSON
  (object / list / list-of-lists / mixed) into `Record`s.
- Extended `Record` with `name`, `max_enhance`, `price_min`, `price_max`
  so `storeData` (Phase 4) can populate the `item_sid` table.
- Rewrote `test_arsha_client.py` against genuine JSON shapes; updated
  `test_models.py`. 95 tests pass.

### Decisions
- Identify item dicts by presence of `id`+`sid`; skip non-item/malformed
  dicts with a warning — no ADR (local choice).

### Deferred / open questions
- Live-API verification still pending (sandbox network blocks arsha.io);
  covered by the Phase 4 integration test.


---

## 2026-06-01 — Phase 4 ETL pipeline: handlers + Step Functions

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 4 — ETL pipeline
**Commits:** `19f320e`, `3b28695`, `73775c0`, `1af8b28`

### Done
- Implemented all six ETL Lambda handlers under `src/functions/`:
  `retrieve_items` (DynamoDB scan of `tracked=true` + full metadata
  projection and a per-execution hour-truncated `snapshot_at`, FR-2),
  `fetch_data`, `clean_data`, `store_data` (single-transaction
  `item`/`item_sid` upsert + idempotent `market_snapshot` bulk-insert
  with rollback and `EtlFailedItems`/`EtlSuccessfulItems` metrics,
  FR-5/NFR-4), `rollup_daily` (server-side OHLC on `base_price`,
  idempotent), and `purge_old_snapshots`.
- Added `infra/etl.yaml` Step Functions ASL matching `design.md`:
  `RetrieveItems → Map(maxConc=5)[FetchData→CleanData→StoreData] →
  Choice(is_day_first_run) → RollupDaily`, hourly ETL cron with
  `{"region": ...}` input (FR-1) and a separate `purgeOldSnapshots`
  daily cron at 00:30 UTC (FR-7); wired into `template.yaml` as
  `EtlStack`.
- Unit tests for all handlers (`tests/unit/test_etl_*.py`). Local gate
  green: ruff, ruff format, mypy strict (28 files), 109 pytest.

### Decisions
- No ADR — handlers follow the layer/Powertools patterns already
  established in Phases 2–3 (local choices).

### Deferred / open questions
- Documentation drift corrected this session: Phase 4 checkboxes for
  the four completed items were ticked in `tasks.md` (they had been
  committed without ticking), and this entry was added retroactively.
- Phase 4 remaining: integration test (moto + dockerised Postgres),
  in-VPC migrator Lambda, and the `accessory_cron_v1` pricing model.


---

## 2026-06-01 — Phase 4 completion: integration test, migrator, cron model

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 4 — ETL pipeline (final 3 tasks)
**Commits:** `2d4a058`, `cae6f02`, `e297bec`, plus this entry

### Done
- Reviewed the branch and realigned drifted tracking docs (ticked the four
  already-implemented Phase 4 boxes; logged the prior ETL session).
- ETL integration suite (`tests/integration/`): end-to-end run of the real
  handlers against moto DynamoDB + a real Postgres (schema via Alembic
  `0001`), stubbing only arsha.io. Skips unless `TEST_DATABASE_URL` is set;
  CI runs it in a `postgres:16` service job.
- In-VPC migrator Lambda (`src/functions/migrator/` + `0003_migrator_role`):
  runs `alembic upgrade head` inside the VPC via IAM auth as `lambda_migrator`
  (no Secrets Manager / NAT). CI deploy invokes it; `make migrate-lambda` for
  dev. Chose a CI-invoke trigger over a CFN custom resource (no-NAT VPC can't
  signal cfnresponse to S3).
- `accessory_cron_v1` pricing model (Option B): cron Markov chain
  (retain 0.60 / drop 0.40), first-passage attempts, cost = clean fuel + cron
  stone, additive cumulative. Made the cron section of `domain-model.md`
  normative with a worked example on real Deboreka Ring data.
- Fixed two latent bugs found en route: Alembic needs `postgresql+psycopg://`
  (only psycopg v3 is installed), and three false-positive bandit findings
  now carry `# nosec` so the CI scan job is green.
- Gate: ruff, format, mypy(strict, 33 files), bandit, cfn-lint, 120 pytest
  pass + 4 integration skips.

### Decisions
- Migrator trigger = CI-invoke, not CFN custom resource — no ADR (local
  choice forced by ADR-0006 no-NAT placement).
- `accessory_cron_v1` uses the documented 60/40 Markov chain and the shared
  `accessory_v1` curve, not the captured `cron_counts` — real Deboreka prices
  showed the placeholder counts give absurd costs; counts retained for a
  future calibrated model. No ADR (model choice recorded in domain-model.md).

### Deferred / open questions
- `p(4->5)=0.005` (`default_stack` 0) is a placeholder; the cron model's PEN
  numbers are provisional until a real cron failstack is set.
- `cron_counts` (tables a/b) still unverified; revisit when live-TW data lands.
- This batch was committed together (deferred across the session), so the
  Phase 4 checkbox ticks landed in the docs commit rather than per-box.



---

## 2026-06-03 — Phase 5 APIs: itemRegistry + marketQuery + OpenAPI export

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 5 — APIs
**Commits:** see `redesign-v3` (this session; logical commits on top of `27f3831`)

### Done
- Reconstructed Phase 5 from a prior shell-outage handover (code was
  written but never run) and verified it end-to-end.
- `itemRegistry` (`/v1/items`, DynamoDB-only, not in VPC): Powertools
  REST resolver with list/get/create/patch/soft-delete; POST validates
  the id against arsha.io before writing (FR-8..12).
- `marketQuery` (`/v1/market`, RDS read-only, in VPC via IAM auth):
  snapshots, daily rollups, and a combined analysis (base-rate
  `accessory_v1` per-tier cost over a `{sid: base_price}` ladder +
  volatility/liquidity/anomaly); rolls back after each request
  (FR-13..15).
- `infra/api.yaml`: REST API GW with API key + PER_API usage plan
  (10 burst / 5 sustained, 1000/day; FR-16/17, ADR-0005), shares the
  ETL stack's `bdo-common` layer; wired all params via `template.yaml`.
- `scripts/export_openapi.py` + `make openapi` merge both resolvers'
  schemas into `infra/openapi.yaml`; new CI `openapi` job fails on
  drift. Added `pyyaml` dev dep (refreshed `uv.lock`).
- Gate green: ruff, format, mypy(strict, 37 files), bandit, cfn-lint,
  132 pytest + 4 integration skips.

### Decisions
- `APIGatewayRestResolver(enable_validation=True)` on both handlers to
  enable native OpenAPI export — no ADR (local choice).
- `marketQuery` stays RDS-only; analysis builds the price ladder from
  the latest snapshots instead of a DynamoDB cross-read for `model_id`
  — no ADR (local choice).
- cfn-lint: ignore W3005 in `api.yaml` (SAM transform auto-generates the
  ApiKey/UsagePlan DependsOn; not authored here) and exclude the
  generated `infra/openapi.yaml` via `.cfnlintrc.yaml` — no ADR.

### Deferred / open questions
- Fixed a latent loader bug: dynamic `importlib` loads (in the OpenAPI
  export script and `tests/conftest.py`) didn't register modules in
  `sys.modules`, so Pydantic couldn't resolve the new request-body
  models' PEP 563 annotations. Registered them before `exec_module`;
  Phase 5 is the first handler with API body models, so this never
  surfaced before.
- Query-string params (region/sid/limit/from/to/window_days) are read
  dynamically, so they don't appear in the OpenAPI spec — revisit if
  full request documentation is needed.



---

## 2026-06-03 — Phase 6 observability stack: dashboard, SLO alarms, log retention

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 6 — Observability
**Commits:** see `redesign-v3` (this session)

### Done
- Replaced the `infra/observability.yaml` placeholder with the real stack,
  grounded in `docs/slo.md` and the design's Observability section:
  - Three SLO alarms -> SNS topic `bdo-${Stage}-alarms` (Alarm + OK actions):
    API 5xx rate >1%/5min (metric math 5XXError/Count), API p95 `Latency`
    >500ms/5min, and ETL non-success in 24h.
  - CloudWatch dashboard `bdo-${Stage}` (12 widgets): API traffic/errors,
    latency p50/p95 (+500ms annotation), 5xx % (+1% annotation), API-Lambda
    invocations/errors/throttles/duration, Step Functions outcomes, ETL-Lambda
    errors, and the four `BdoMarket` domain metrics.
  - Log-group retention for all 9 Lambdas (`LogRetentionInDays`, default 30).
  - Optional `AlarmEmail` -> conditional SNS email subscription.
- Wired `EtlStateMachineArn` into `ObservabilityStack` (template.yaml) via
  `!GetAtt EtlStack.Outputs...`; trimmed its `DependsOn` to `ApiStack`.
- Gate: cfn-lint green on the full template set; dashboard JSON parses.

### Decisions
- ETL alarm sums Failed+Aborted+TimedOut, not just ABORTED as the spec text
  says — a retry-exhausted run ends FAILED (ABORTED is a manual stop), so the
  literal wording would miss real failures — no ADR (strengthening; flagged).
- 429s stay in the 5xx-rate denominator (no standalone API GW 429 metric), so
  the availability alarm is marginally conservative — no ADR (documented).
- Lambda log groups are owned by this stack (safe on fresh deploy); alarms
  publish to an SNS topic with no default subscription (email opt-in) — no ADR.

### Deferred / open questions
- Two Phase 6 boxes remain and need a live `dev` stack: verify the X-Ray
  service map end-to-end, and smoke-test the alarms (force an ETL failure and
  a 5xx). Tracked unticked in `tasks.md`.
- RDS dashboard widgets (CPU/connections/storage) left out to avoid wiring the
  DB identifier across stacks; add later if useful.
- Pre-existing log groups on an already-deployed stack would need CFN import;
  fine for greenfield v3 — worth a runbook note at cutover.



---

## 2026-06-03 — Fix: cross-platform migrator `sam build`

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 6 — Observability (incidental build fix during dev-deploy validation)
**Commits:** see `redesign-v3` (this session)

### Done
- First real `sam build` (on a Windows host) failed in the migrator's custom
  makefile build: `mkdir -p`/`cp` are POSIX-only (Windows cmd.exe errored on the
  pre-existing ARTIFACTS_DIR), and `cp -R ../../../migrations` could never
  resolve because SAM runs the recipe from a scratch *copy* of the function dir.
  `sam build` had not run before (the tag-gated CI deploy job never fired), so
  the bug was latent since Phase 4.
- Replaced the shell recipe with `src/functions/migrator/build.py` (one-line
  Makefile delegates to it): pure `shutil`/`subprocess`, and it finds the
  repo-root `migrations/` by walking up from `ARTIFACTS_DIR` (always inside
  `.aws-sam/`) instead of a CWD-relative path. Verified end-to-end on Linux
  (copies app.py + migrations/, installs the Alembic engine).
- Gate stays green: ruff, format, mypy(strict, 38 files), bandit (nosec B404/B603
  on the build-time pip call), cfn-lint.

### Decisions
- Build logic in Python, not portable shell — the only reliable way to run the
  same recipe on Windows/macOS/Linux and to survive SAM's scratch-dir copy. No
  ADR (local build-tooling choice).

### Deferred / open questions
- Still pending a live `dev` stack: X-Ray service-map check and alarm
  smoke-tests (the two open Phase 6 boxes).



---

## 2026-06-03 — Fix: bundle bdo_common into the shared layer

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 6 — Observability (incidental fix during dev-deploy validation)
**Commits:** see `redesign-v3` (this session)

### Done
- First live invocation (itemRegistry POST) returned API GW 500; CloudWatch
  showed `Runtime.ImportModuleError: No module named 'bdo_common'`. Root cause:
  the layer used `BuildMethod: python3.12`, which installs only requirements.txt
  into `python/` and drops the hand-authored `bdo_common` package — so the layer
  shipped pydantic/powertools/psycopg but not `bdo_common`. This broke *every*
  function at init, not just the API.
- Converted the layer to `BuildMethod: makefile` with `src/layer/build_layer.py`
  (mirrors the migrator pattern): copies `bdo_common` and pip-installs
  requirements.txt, both under `python/`. Verified locally that the artifact
  contains `python/bdo_common/` plus the deps.
- Gate green: ruff, format, mypy(strict, 39 files), bandit, cfn-lint.

### Decisions
- Makefile build for the layer over restructuring as a pip-installable package —
  explicit, version-independent, and consistent with the migrator. No ADR.

### Deferred / open questions
- Requires `make build` + `make deploy-dev` (new layer version + all functions
  rebound) before APIs/ETL work. The DB bootstrap (bastion) and the two Phase 6
  validation boxes (X-Ray map, alarm smoke-test) are still pending.



---

## 2026-06-03 — First dev deploy + runtime/packaging fixes

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 6 — Observability (dev-deploy validation)
**Commits:** see `redesign-v3` (this session): region `us-east-1`, CAPABILITY_NAMED_IAM,
migrator/layer makefile builds, Linux-target wheels, and powertools[tracer]

### Done
- Stood up the `dev` stack in `us-east-1` for the first time and worked through
  the first-deploy failures end to end:
  - `samconfig` granted only `CAPABILITY_IAM`; DataStack's named roles need
    `CAPABILITY_NAMED_IAM` (deploy rolled back). Fixed dev + prod.
  - Corrected the AWS region from `ap-northeast-1` to `us-east-1` everywhere
    (samconfig, CI, Makefile, IAM-auth fallbacks, tests).
  - First real `sam build` exposed packaging bugs (never exercised before — the
    tag-gated deploy job hadn't run): the layer omitted `bdo_common`
    (`BuildMethod: python3.12` installs only requirements), and Windows builds
    shipped host-native wheels. Switched the layer + migrator to makefile builds
    that bundle the code and pin pip to the Lambda target
    (`manylinux2014_x86_64`, cp312).
  - `aws-xray-sdk` was missing (Tracer needs the powertools `[tracer]` extra);
    added it to the layer requirements + `pyproject.toml`, refreshed `uv.lock`.
- itemRegistry API verified working (GET/POST against `/v1/items`).

### Decisions
- Makefile builds for the layer and migrator, with pip pinned to the Lambda
  runtime target, so `make build` works on any host OS without Docker — no ADR
  (build-tooling choice).

### Deferred / open questions
- DB schema/roles not yet bootstrapped: needs the bastion (`EnableBastion=true`)
  for the one-time `make migrate` as master user; until then ETL `storeData`
  and marketQuery stay non-functional.
- Phase 6 boxes still open: verify the X-Ray service map end-to-end and
  smoke-test the alarms.



---

## 2026-06-08 — DB bootstrap via bastion: `make migrate` end-to-end

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 6 — Observability (dev-deploy validation; the deferred DB bootstrap)
**Commits:** `0742593`, `349469f`, `6b4a12c`, `5254a66`, `aa827c5`, `9b13783`, `6403197`, `49e986e`, `44b0a20`, `cd68d30`, plus this entry

### Done
- Closed the DB-bootstrap item deferred at the first dev deploy: ran the
  one-time `make migrate` (`0001`→`0003`) against the private RDS through the
  EICE bastion tunnel as the master user, then worked through every failure:
  - **Tunnel/Make:** fixed `db-tunnel-up` stack prefix + EICE port forwarding
    and switched `RdsEndpoint` lookup to a working JMESPath query.
  - **Network:** added the bastion's SSH self-egress so the EICE tunnel can
    establish.
  - **Alembic:** resolved `script_location` via `%(here)s` (CWD-independent),
    and injected the DB URL past ConfigParser's `%`-interpolation.
  - **Roles/grants (ADR-0008):** set default privileges as `lambda_migrator`
    via `SET ROLE`, kept the RDS master out of `rds_iam` (revoked auto
    membership), dropped the explicit master GRANT in `0003` (single revocable
    edge), and granted the migrator DML on `alembic_version`.
- Rewrote the runbook bootstrap into copy-pasteable steps with EICE/`rds_iam`
  caveats. Local gate stays green.

### Decisions
- Migrator owns object default privileges via `SET ROLE lambda_migrator` rather
  than granting per-object after the fact — keeps the master's role-graph edges
  minimal and revocable. No ADR (refines ADR-0008's IAM-auth role model).

### Deferred / open questions
- Two Phase 6 boxes still need the live stack post-bootstrap: verify the X-Ray
  service map end-to-end and smoke-test the SLO alarms (force an ETL failure
  and a 5xx).
- CI `audit` job now fails on a newly-disclosed pip self-vuln (PYSEC-2026-196,
  fix 26.1.2) — unrelated to project deps; decide bump-vs-ignore before cutover.
- Phase 7 cutover (soak, archive `main`, force-replace) remains, with explicit
  approval required.



---

## 2026-06-09 — Phase 6 close-out: live dev validation + CI audit fix

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 6 — Observability (final two boxes)
**Commits:** `69134a2`, `da0c13f`, plus this entry

### Done
- Reviewed `redesign-v3`; reconciled a disjoint local history against the
  true remote tip (`cd68d30`) and rebuilt the branch cleanly so changes
  fast-forward (no force-push).
- Recorded the previously un-logged 2026-06-08 DB-bootstrap session (`make
  migrate` end-to-end via the EICE bastion; 10 commits).
- Fixed the CI `audit` job: pinned `pip>=26.1.2` (PyPI `uv.lock` refresh) to
  clear PYSEC-2026-196 in pip itself; `pip-audit` now exits 0. Local gate
  green (ruff, format, mypy strict 39 files, 132 pytest + 4 integration skip).
- Closed the two open Phase 6 boxes against the live `bdo-market-dev` stack
  (us-east-1), confirmed by the operator:
  - X-Ray service map connected end-to-end (API GW → Lambda → RDS/DynamoDB).
  - SLO alarms transition to ALARM and SNS fires on a forced ETL failure and
    a forced API 5xx.

### Decisions
- Pin pip as an explicit dev dependency rather than `--ignore-vuln` — keeps
  the audit honest and self-heals once the runner image ships 26.1.2. No ADR.

### Deferred / open questions
- Phase 6 is now complete. Phase 7 cutover remains (soak dev 24h, deploy
  prod, archive `main`/`rewrite-project`, force-replace `main`,
  `docs/cleanup-tasks.md`) — destructive, explicit approval required.



---

## 2026-06-09 — Phase 7 prep: stage-scope the DynamoDB table

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 7 — Cutover (pre-prod-deploy fix)
**Commits:** `5953a42` (cleanup-doc discovery review), `cc234c1`, plus this entry

### Done
- Reviewed the discovery commands in `docs/cleanup-tasks.md` and hardened them:
  multi-region sweep (v1 likely in `ap-northeast-1`, per `1c5f554`),
  history-expansion-safe Lambda query (grep not JMESPath `!`), plus Aurora,
  HTTP-API (apigatewayv2), and custom EventBridge-bus coverage.
- Answered a pre-prod question ("do dev/prod share data stores?") by auditing
  `infra/data.yaml`: RDS is per-stack (auto-named, isolated, prod deletion
  protection) — but the DynamoDB table name was hard-coded `bdo-v3-items`, the
  lone resource not stage-scoped. A prod deploy would have collided with dev's
  existing table (CREATE_FAILED) — and otherwise prod/dev would share one item
  registry.
- Fixed: `TableName: !Sub 'bdo-${Stage}-items'`. The name already propagates
  dynamically (`DynamoDbTableName` output → `DYNAMODB_TABLE` env), so only the
  one line plus name-default fallbacks (`dynamo.py`, `config.py`, seed script)
  and descriptive docs/tests changed. Gate green: ruff, format, mypy(39),
  132 pytest + 4 skip, cfn-lint.

### Decisions
- Stage-scope the items table to match every other `bdo-${Stage}-*` resource —
  no ADR (corrects an oversight; consistent with the per-stage isolation in
  `data.yaml`).

### Deferred / open questions
- **Action on next dev deploy:** the live dev table is still literally
  `bdo-v3-items`; the rename is a CFN replacement (creates `bdo-dev-items`,
  deletes the old one). Re-seed dev: `python scripts/seed_items.py
  --target-table bdo-dev-items`. Prod is a clean first create.
- Remaining Phase 7: deploy prod, archive `main`/`rewrite-project`,
  force-replace `main` — destructive, explicit approval required.



---

## 2026-06-09 — Phase 7 cutover: v3 is now `main`

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3` → `main` (cutover); follow-up on `main`
**Phase:** 7 — Cutover (complete)
**Commits:** PR #2 `6f8e69b` (CI retarget), plus this entry

### Done
- **Cutover executed by the operator** (destructive git ops run locally with
  approval): tagged `archive/main-v1` at the old `origin/main`, renamed remote
  `rewrite-project` → `archive/rewrite-project`, and force-replaced `main` with
  `redesign-v3`. `main` now carries the v3 implementation (Phases 0–6).
- Redeployed `dev` to pick up the DynamoDB stage-scope rename
  (`bdo-v3-items` → `bdo-dev-items`, a CFN table replacement) and re-seeded the
  dev registry; `prod` stood up and bootstrapped ahead of cutover.
- Fixed a post-cutover CI gap (PR #2): the workflow still triggered on
  `redesign-v3`, so `main` would have run no gates. Pointed `push`/
  `pull_request` at `main` (tag-based `v*` deploy unchanged). Merged via
  squash; this was the first CI run to register the job names on `main`.
- Ticked the five Phase 7 boxes in `tasks.md`. All seven phases now complete.

### Decisions
- Standardise future merges into `main` on squash (clean, linear history after
  the cutover rewrite) — no ADR (workflow choice).

### Deferred / open questions
- Enable `main` branch protection with the now-registered required status
  checks (`lint`/`typecheck`/`test`/`integration`/`audit`/`scan`/`validate`/
  `openapi`); re-enable PR-required + no-force-push/delete.
- Run the legacy decommission (`docs/cleanup-tasks.md`) — discovery first
  (sweep `us-east-1` **and** `ap-northeast-1`), then per-item sign-off.
- Delete the merged `redesign-v3` branch once `main` is confirmed stable.



---

## 2026-06-09 — Legacy v1 decommission completed

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `main` (post-cutover)
**Phase:** Post-cutover cleanup
**Commits:** this entry

### Done
- Completed the legacy v1 resource decommission planned in
  `docs/cleanup-tasks.md`. Discovery ran read-only across both regions; each
  candidate was confirmed not owned by a `bdo-market-*` (v3) stack before
  deletion, and data stores were backed up first.
- Migrated the still-useful legacy item metadata into the v3 per-stage
  registries (`bdo-dev-items` / `bdo-prod-items`) ahead of dropping the source
  tables, then removed the obsolete v1 compute, data, IAM, storage, and log
  resources plus an orphaned empty stack in the secondary region.
- Verified post-deletion: only the v3 resources remain (e.g. `bdo-dev-items`
  and `bdo-prod-items` present; no legacy tables/functions left).

### Decisions
- Resource-level specifics (account id, names/ARNs, per-item sign-off) are kept
  out of the repo and tracked privately — no concrete inventory committed to
  GitHub (security preference). `docs/cleanup-tasks.md` remains the generic
  template only.

### Deferred / open questions
- Drop the predeletion DynamoDB backups after a short retention window.
- v3 is live on `main` with all phases complete; no further v1 footprint
  outstanding.
