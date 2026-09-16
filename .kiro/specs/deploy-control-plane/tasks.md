# Implementation Plan: deploy-control-plane

## Overview

Python 3.12 + uv; Pydantic v2 at every boundary. Each box is one commit on a
feature branch off `main` (tick its checkbox in the same commit). The wizard is a
**thin control plane** — it dispatches to the SAM CLI, GitHub Actions (`gh`), and
`git`; it reimplements no deploy logic and adds no `scripts/` ops folder. Sub-tasks
marked `*` are optional tests and are not implemented by default. Phases end at
checkpoints; nothing ships half-built.

## Tasks

### Phase 1 — Package scaffolding & typed models

- [ ] 1.1 Scaffold the control-plane package
  - Add a console entry point in `pyproject.toml` (wizard = front-end `main`)
  - Create the module layout: `cli.py`, `tui.py`, `core/dispatch.py`,
    `core/models.py`, `core/executors/{sam,actions,git,config}.py`
  - Add dev/ops-only deps (Typer, Textual) in a dev/ops group only — kept out of
    the Lambda layer and `bdo_common`
  - _Requirements: 1.1, 2.3, 10.5_

- [ ] 1.2 Define the Pydantic v2 command-core models
  - `Capability`, `Target`, `Command`, `PlanStep`, `Plan`, `Result`, `ConfigDiff`
    per the design's Data Models (JSON-serializable for `--json`)
  - _Requirements: 1.4, 11.6, 11.7_

- [ ] 1.3 Implement model validation and the exit-code contract
  - `stage` ∈ `samconfig.toml` envs; `version` matches `^v\d+\.\d+\.\d+$`;
    reject a `LOCAL` prod deploy (exit `2`); reject any non-repo-scoped SSM path
    (exit `2`); map outcomes to exit `{0,1,2,3}`
  - _Requirements: 6.2, 8.2, 10.1, 11.3, 11.7_

- [ ]* 1.4 Unit tests for models and validation rules
  - Cover the version regex, LOCAL-prod rejection, SSM-path rejection, exit-code
    mapping, and round-trip JSON serialization
  - _Requirements: 6.2, 8.2, 10.1, 11.3, 11.7_

### Phase 2 — Dispatcher core

- [ ] 2.1 Implement pure `Dispatcher.plan()`
  - Resolve every `Command` into a `Plan` routed to exactly one executor selected
    from `cmd.target`; planning is side-effect-free; a prod deploy only ever
    plans "trigger the protected CI job"
  - _Requirements: 2.1, 2.4, 7.1_

- [ ] 2.2 Implement `Dispatcher.execute()` with the confirmation and dry-run contract
  - Raise/return exit `3` with the `Plan` when a mutating plan is unconfirmed;
    a `--dry-run` / preview plan performs no mutation of any kind
  - _Requirements: 2.1, 11.4, 11.5_

- [ ]* 2.3 Plan unit tests per capability + target (executors mocked)
  - Assert exact command lines, effects, and `requires_confirmation` for each
    capability/target combination
  - _Requirements: 2.1, 2.4_

- [ ] 3. Checkpoint — Ensure all tests pass, ask the user if questions arise.

### Phase 3 — Executors (adapters over sanctioned tools)

- [ ] 3.1 Implement `SamExecutor`
  - `validate` / `build` / `deploy(config_env)` / `sync(config_env)`; select only
    `--config-env` (never compose `--parameter-overrides`); `deploy` refuses
    `config_env == "prod"`
  - _Requirements: 2.2, 6.1, 7.1_

- [ ] 3.2 Implement `ActionsDispatcher`
  - `run_workflow` → `gh workflow run deploy.yml -f stage=… -f version=…`;
    `watch` → `gh run watch`; `view` → `gh run view`; return the dispatched run ref
  - _Requirements: 6.3, 7.2, 8.6, 9.3, 11.6_

- [ ] 3.3 Implement `GitExecutor`
  - `release_preconditions` (clean tree, on `main`, tag absent locally + on origin);
    `tag_and_push` creates and pushes `vX.Y.Z`
  - _Requirements: 8.1, 8.3, 8.4_

- [ ] 3.4 Implement `ConfigStore`
  - `read_merged` (samconfig + SSM + AppConfig) with secret masking; `open_config_pr`
    via `gh` (tracked files, incl. `BdoRegions`); `put_ssm` with repo-scoped-path
    enforcement + audit + failure leaves prior value unchanged; `set_flag` flips an
    AppConfig feature flag
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 10.1, 10.2_

- [ ]* 3.5 Executor tests
  - `moto` for `ConfigStore` SSM/AppConfig writes; recorded-command assertions for
    `sam` / `gh` / `git` invocations; verbatim executor output on failure (exit `1`,
    no traceback)
  - _Requirements: 3.4, 3.5, 10.1, 11.2_

- [ ] 3.6 Checkpoint — Ensure all tests pass, ask the user if questions arise.

### Phase 4 — Capabilities wired end-to-end

- [ ] 4.1 Wire the **config** capability
  - `config show` renders the masked merged view; `config set` opens a PR for
    tracked files or writes SSM with audit
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6_

- [ ] 4.2 Wire the **flag** capability
  - Flip a runtime feature flag in AppConfig without redeploy; confined to
    AppConfig (no fourth location)
  - _Requirements: 4.1, 4.3_

- [ ] 4.3 Wire the **bootstrap** capability
  - One-time, clearly-labelled helper wrapping `sam pipeline bootstrap` (OIDC
    deploy role + artifact bucket) and configuring GitHub Environments/secrets;
    stop at the first failing step and preserve completed effects
  - _Requirements: 5.1, 5.2, 5.3, 7.3_

- [ ] 4.4 Wire the **deploy** capability
  - `target=LOCAL` dev/personal → `sam deploy --config-env <stage>` / `sam sync`;
    `target=CI` shared/prod → trigger the CI job; LOCAL prod rejected (exit `2`);
    fresh env reaches target state via a single declarative deploy
  - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [ ] 4.5 Wire the **release** capability
  - Sole prod initiator: verify preconditions, tag + push (`push: tags: v*`) or
    `gh workflow run` dispatch; surface the dispatched run URL/status
  - _Requirements: 7.2, 8.1, 8.4, 8.5, 8.6_

- [ ]* 4.6 Capability plan/unit tests per capability + target
  - Executors mocked; assert routing, effects, and rejection paths for each capability
  - _Requirements: 2.1, 5.3, 6.2, 8.3_

### Phase 5 — Front-ends over one shared core

- [ ] 5.1 Implement the Typer CLI front-end
  - One subcommand per capability; `--json` (sole `Result` on stdout, logs to
    stderr), `--yes`, `--dry-run`; never prompts; deterministic exit codes
  - _Requirements: 1.1, 1.2, 11.1, 11.2, 11.7_

- [ ] 5.2 Implement the Textual TUI front-end
  - Guided flows that render the `Plan` at an explicit confirmation step before any
    mutating execution (convenience skin, not system of record)
  - _Requirements: 1.3_

- [ ] 5.3 Wire both front-ends to the shared `Dispatcher`
  - Each front-end only collects intent and renders the `Result`; both build the
    same typed `Command` and route through the same `Dispatcher`
  - _Requirements: 1.4_

- [ ]* 5.4 Front-end equivalence test
  - Same intent through CLI and TUI produces byte-for-byte identical serialized `Plan`s
  - _Requirements: 1.5_

- [ ] 5.5 Checkpoint — Ensure all tests pass, ask the user if questions arise.

### Phase 6 — CI/CD & AppConfig infrastructure

- [ ] 6.1 Add the reusable composite action `.github/actions/setup/`
  - Factor checkout → `setup-python` → `uv sync` so workflows cannot drift (ADR-0038)
  - _Requirements: 9.4_

- [ ] 6.2 Add `.github/workflows/deploy.yml`
  - Triggers on `push: tags: v*` and `workflow_dispatch` with typed inputs (`stage`,
    `version`, toggles); environment-gated deploy jobs (`environment: prod` with the
    protection); OIDC keyless deploy (`id-token: write`); consumes the composite action.
    Its `workflow_dispatch` inputs are a superset of what the wizard sends
  - _Requirements: 7.3, 9.1, 9.3_

- [ ] 6.3 Refactor `ci.yml` to consume the composite action
  - Validation behavior unchanged — it remains the branch-protection gate; only the
    shared setup steps are swapped for the composite action (ADR-0038)
  - _Requirements: 9.2, 9.4, 9.5_

- [ ] 6.4 Add AppConfig + Powertools feature-flag wiring in the SAM template
  - AppConfig application/profile in the template; an AppConfig/AppConfigData VPC
    endpoint for in-VPC Lambdas under no-NAT (ADR-0006); Powertools feature-flags
    provider wiring
  - _Requirements: 4.2, 10.4_

- [ ] 6.5 Checkpoint — Ensure `sam validate --lint` / `cfn-lint` pass, ask the user if questions arise.

### Phase 7 — ADRs

- [ ] 7.1 ADR: wizard as a thin control plane, console entry point (no `scripts/` folder)
  - _Requirements: 2.3_

- [ ] 7.2 ADR: prod gating via GitHub Environments (required reviewers) + OIDC keyless deploy
  - _Requirements: 7.3_

- [ ] 7.3 ADR: runtime feature flags via AppConfig + Powertools, incl. the no-NAT VPC endpoint
  - _Requirements: 4.2, 10.4_

- [ ] 7.4 ADR: CLI framework (Typer) and TUI framework (Textual) choices
  - _Requirements: 1.1, 1.3_

### Phase 8 — Property-based testing

One executable property test per stated invariant (invariants only, not for
completeness — AGENTS.md). Each cites the requirement(s) it defends.

- [ ]* 8.1 Property test — Front-end equivalence
  - **Property 1: Front-end equivalence**
  - **Validates: Requirements 1.5**

- [ ]* 8.2 Property test — No first-party prod deploy
  - **Property 2: No first-party prod deploy**
  - **Validates: Requirements 7.1, 7.2**

- [ ]* 8.3 Property test — Config-as-data
  - **Property 3: Config-as-data**
  - **Validates: Requirements 3.3, 3.4, 4.1, 4.3**

- [ ]* 8.4 Property test — Dispatch fidelity
  - **Property 4: Dispatch fidelity**
  - **Validates: Requirements 9.3**

- [ ]* 8.5 Property test — Dry-run purity
  - **Property 5: Dry-run purity**
  - **Validates: Requirements 11.5**

- [ ] 8.6 Final checkpoint — Ensure `ruff` / `mypy` / `pytest` pass, ask the user if questions arise.

## Notes

- Tasks marked `*` are optional test sub-tasks and can be skipped for a faster MVP.
- Each task references specific requirements for traceability; property tests also
  cite the design property they implement.
- The wizard dispatches only — no deploy logic is reimplemented and no `scripts/`
  ops folder is added.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "6.1", "6.4", "7.1", "7.2", "7.3", "7.4"] },
    { "id": 2, "tasks": ["1.3", "6.2", "6.3"] },
    { "id": 3, "tasks": ["1.4", "2.1"] },
    { "id": 4, "tasks": ["2.2", "3.1", "3.2", "3.3", "3.4"] },
    { "id": 5, "tasks": ["2.3", "3.5", "4.1", "4.2", "4.3", "4.4", "4.5"] },
    { "id": 6, "tasks": ["4.6", "5.1", "5.2"] },
    { "id": 7, "tasks": ["5.3"] },
    { "id": 8, "tasks": ["5.4", "8.1", "8.2", "8.3", "8.4", "8.5"] }
  ]
}
```
