# Implementation Plan: deploy-control-plane

## Overview

Python 3.12 + uv; Pydantic v2 at every boundary. Each box is one commit on a
feature branch off `main` (tick its checkbox in the same commit). The wizard is a
**thin control plane** — it dispatches to the SAM CLI, GitHub Actions (`gh`), and
`git`; it reimplements no deploy logic and adds no `scripts/` ops folder. Four
capabilities are built — **config**, **bootstrap**, **deploy**, **release** — each
mapped onto an executor. Runtime feature flags are **deferred to a follow-up
spec** and are not built here; when they are built, the flag store will be a
**DynamoDB** table read via Powertools over the existing free DynamoDB Gateway
endpoint — **not** AppConfig, which is rejected on cost because it would require
a paid PrivateLink interface endpoint under no-NAT (ADR-0006). This feature adds
**no new AWS infrastructure**: the only non-Python artefacts are the GitHub
Actions workflows and the shared composite action. Sub-tasks marked `*` are
optional tests and are not implemented by default. Phases end at checkpoints;
nothing ships half-built.

## Tasks

### Phase 1 — Package scaffolding & typed models

- [x] 1.1 Scaffold the control-plane package
  - Add a console entry point in `pyproject.toml` (wizard = front-end `main`)
  - Create the module layout: `cli.py`, `tui.py`, `core/dispatch.py`,
    `core/models.py`, `core/executors/{sam,github,git,config}.py`
  - Add dev/ops-only deps (Typer, Textual) in a dev/ops group only — kept out of
    the Lambda layer and `bdo_common`
  - _Requirements: 1.1, 2.3, 9.4_

- [x] 1.2 Define the Pydantic v2 command-core models
  - `Capability`, `Target`, `Command`, `Op`, `PlanStep`, `Plan`, `Result`,
    `ConfigDiff` per the design's Data Models (JSON-serializable for `--json`)
  - `PlanStep.executor` is `Literal["sam", "github", "git", "config"]` and
    `PlanStep.op` is the typed `Op` StrEnum (closed vocabulary, GitHub members
    `github.run_workflow` / `github.environment_set` / `github.secret_set`), so
    executors switching on `op` get exhaustiveness checking at type-check time
  - _Requirements: 1.4, 10.6, 10.7_

- [x] 1.3 Implement model validation and the exit-code contract
  - `stage` ∈ `samconfig.toml` envs; `version` matches `^v\d+\.\d+\.\d+$`;
    reject a `LOCAL` prod deploy (exit `2`); reject any non-repo-scoped SSM path
    (exit `2`); map outcomes to exit `{0,1,2,3}`
  - _Requirements: 5.2, 7.2, 9.1, 10.3, 10.7_

- [x]* 1.4 Unit tests for models and validation rules
  - Cover the version regex, LOCAL-prod rejection, SSM-path rejection, exit-code
    mapping, and round-trip JSON serialization
  - _Requirements: 5.2, 7.2, 9.1, 10.3, 10.7_

### Phase 2 — Dispatcher core

- [x] 2.1 Implement pure `Dispatcher.plan()`
  - Resolve every `Command` into a `Plan` routed to exactly one executor selected
    from `cmd.target`; planning is side-effect-free; a prod deploy only ever
    plans "trigger the protected CI job"
  - _Requirements: 2.1, 2.4, 6.1_

- [x] 2.2 Implement `Dispatcher.execute()` with the confirmation and dry-run contract
  - Raise/return exit `3` with the `Plan` when a mutating plan is unconfirmed;
    a `--dry-run` / preview plan performs no mutation of any kind
  - _Requirements: 2.1, 10.4, 10.5_

- [x]* 2.3 Plan unit tests per capability + target (executors mocked)
  - Assert exact command lines, effects, and `requires_confirmation` for each
    capability/target combination
  - _Requirements: 2.1, 2.4_

- [x] 2.4 Checkpoint — Ensure all tests pass, ask the user if questions arise.
  - Verifies the dispatcher core: pure planning, single-executor routing, the
    confirmation gate, and dry-run purity
  - _Requirements: 2.1, 2.4, 6.1, 10.4, 10.5_

### Phase 3 — Executors (adapters over sanctioned tools)

- [x] 3.1 Implement `SamExecutor`
  - `validate` / `build` / `deploy(config_env)` / `sync(config_env)`; select only
    `--config-env` (never compose `--parameter-overrides`); `deploy` refuses
    `config_env == "prod"`
  - _Requirements: 2.2, 5.1, 6.1_

- [x] 3.2 Implement `GitHubExecutor` (`core/executors/github.py`)
  - The single adapter over the GitHub CLI, covering three groups:
  - **Workflow dispatch** — `run_workflow` → `gh workflow run deploy.yml
    -f stage=… -f version=…`; returns the dispatched run ref
  - **Run status** — `watch` → `gh run watch`; `view` → `gh run view`
  - **Repository / environment administration** (used only by `bootstrap`) —
    `set_environment` creates or updates a GitHub Environment (e.g. `prod` with
    required reviewers); `set_environment_secret` sets an Environment secret,
    rendering only the secret's **name** in a plan — never its value, since the
    deploy role ARN is account-identifying
  - Steps arrive as typed `op`s from the `Op` StrEnum (`github.run_workflow`,
    `github.environment_set`, `github.secret_set`), so the executor's switch gets
    exhaustiveness checking; it never parses `PlanStep.command`
  - Covers the administration methods themselves; the `bootstrap` wiring that
    calls them stays in 4.2
  - _Requirements: 4.1, 4.4, 5.3, 6.2, 7.6, 8.3, 10.6_

- [x] 3.3 Implement `GitExecutor`
  - `release_preconditions` (clean tree, on `main`, tag absent locally + on origin);
    `tag_and_push` creates and pushes `vX.Y.Z`
  - _Requirements: 7.1, 7.3, 7.4_

- [x] 3.4 Implement `ConfigStore`
  - `read_merged` (samconfig + SSM) with secret masking (SecureString or a key
    name containing `secret` / `password` / `token` / `key`)
  - `open_config_pr` via `gh` for tracked files — including `BdoRegions`, the
    single active-region toggle in `samconfig.toml` (ADR-0036)
  - `put_ssm` with repo-scoped-path enforcement + audit record; a failed write
    leaves the prior value at the targeted path unchanged. The repo-scoped check
    now also requires the `<stage>` segment to be an environment defined in
    `samconfig.toml` — not merely any non-empty string
  - Strictly config-as-data over `samconfig.toml` + SSM: **no** GitHub
    Environment or Environment-secret work here — that moved to the
    `GitHubExecutor` (3.2)
  - Two sanctioned locations only (`samconfig.toml` via PR, SSM); no runtime
    flag store is introduced here
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 9.1, 9.2, 9.3_

- [x]* 3.5 Executor tests
  - `moto` for `ConfigStore` SSM reads/writes; recorded-command assertions for
    `sam` / `gh` / `git` invocations; verbatim executor output on failure (exit `1`,
    no traceback)
  - _Requirements: 3.4, 3.5, 9.1, 10.2_

- [x] 3.6 Checkpoint — Ensure all tests pass, ask the user if questions arise.
  - Verifies every executor adapter: `--config-env`-only SAM invocation, the
    `deploy.yml` dispatch, release preconditions, and config-as-data writes
  - _Requirements: 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 7.1, 9.1, 10.2_

### Phase 4 — Capabilities wired end-to-end

- [x] 4.1 Wire the **config** capability
  - `config show` renders the masked merged view; `config set` opens a PR for
    tracked files or writes SSM with audit
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6_

- [x] 4.2 Wire the **bootstrap** capability
  - One-time, clearly-labelled helper: the `SamExecutor` wraps `sam pipeline
    bootstrap` (OIDC deploy role + artifact bucket) while the `GitHubExecutor`
    creates/updates the GitHub Environments (`github.environment_set`) and sets
    the Environment secrets (`github.secret_set`)
  - Stop at the first failing step and preserve completed effects; plan only the
    secret's name, never its value
  - _Requirements: 4.1, 4.2, 4.3, 6.3_

- [x] 4.3 Wire the **deploy** capability
  - `target=LOCAL` dev/personal → `sam deploy --config-env <stage>` / `sam sync`;
    `target=CI` shared/prod → trigger the CI job; LOCAL prod rejected (exit `2`);
    fresh env reaches target state via a single declarative deploy
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 4.4 Wire the **release** capability
  - Verify preconditions, then tag + push (`push: tags: v*`) or `gh workflow run`
    dispatch; surface the dispatched run URL/status
  - Production is initiated only by the sanctioned pipeline triggers (a pushed
    release tag, or an authorised `workflow_dispatch` of `deploy.yml`); no LOCAL
    path initiates a production deploy
  - The Release_Tag is the source of `ApiVersion` (ADR-0037)
  - _Requirements: 6.2, 7.1, 7.4, 7.5, 7.6, 9.3_

- [x]* 4.5 Capability plan/unit tests per capability + target
  - Executors mocked; assert routing, effects, and rejection paths for each capability
  - _Requirements: 2.1, 4.3, 5.2, 7.3_

### Phase 5 — Front-ends over one shared core

- [x] 5.1 Implement the Typer CLI front-end
  - One subcommand per capability; `--json` (sole `Result` on stdout, logs to
    stderr), `--yes`, `--dry-run`; never prompts; deterministic exit codes
  - Catch `ConfirmationRequired` from the core and render a `Result` carrying the
    refused `Plan` (`Result.plan`), exiting `3` — this is how Requirement 10.4 is
    satisfied at the CLI_Mode boundary it describes
  - _Requirements: 1.1, 1.2, 10.1, 10.2, 10.4, 10.7_

- [x] 5.2 Implement the Textual TUI front-end
  - Guided flows that render the `Plan` at an explicit confirmation step before any
    mutating execution (convenience skin, not system of record)
  - _Requirements: 1.3_

- [x] 5.3 Wire both front-ends to the shared `Dispatcher`
  - Each front-end only collects intent and renders the `Result`; both build the
    same typed `Command` and route through the same `Dispatcher`
  - _Requirements: 1.4_

- [x] 5.4 Wire run-following into both front-ends
  - Add `Result.run: RunRef | None` alongside the display-only `run_url`, so the
    dispatched run has one identity rather than one parsed back out of a URL
  - Add `ControlPlane` (`Dispatcher` + `GitHubExecutor`) and
    `build_control_plane()` to `core/assembly.py` beside the unchanged
    `build_dispatcher()`, exposing the executor for following without a plan —
    no `Op` routes to `watch`/`view`
  - Add the shared `follow_run()` helper to `presentation.py` and call it from
    **both** front-ends, so CLI and TUI share one notion of "did the run pass"
  - Following is default-on for human output; under `--json` it requires the new
    `--watch` flag (a blocking watch cannot coexist with the sole-`Result`-on-stdout
    contract). The run URL is surfaced either way
  - _Requirements: 7.6, 10.6_

- [x]* 5.5 Front-end equivalence test
  - Same intent through CLI and TUI produces byte-for-byte identical serialized `Plan`s
  - _Requirements: 1.5_

- [x] 5.6 Checkpoint — Ensure all tests pass, ask the user if questions arise.
  - Verifies both front-ends over one shared core: non-interactive CLI contract,
    TUI confirmation step, and front-end equivalence
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 10.1, 10.2, 10.7_

### Phase 6 — CI/CD workflows

- [x] 6.1 Add the reusable composite action `.github/actions/setup/`
  - Factor checkout → `setup-python` → `uv sync` so workflows cannot drift (ADR-0038)
  - _Requirements: 8.4_

- [x] 6.2 Add `.github/workflows/deploy.yml`
  - Triggers on `push: tags: v*` and `workflow_dispatch` with typed inputs (`stage`,
    `version`, toggles); environment-gated deploy jobs (`environment: prod` with the
    protection); OIDC keyless deploy (`id-token: write`); consumes the composite action.
    Its `workflow_dispatch` inputs are a superset of what the wizard sends
  - Cutover, not an addition: also **removes the superseded tag-gated `deploy` job from
    `ci.yml`**, whose deployment capability `deploy.yml` carries forward. Leaving both
    would run two concurrent prod deploys per pushed tag, and would keep
    `id-token: write` inside the validation workflow (ADR-0038)
  - _Requirements: 6.3, 8.1, 8.3_

- [x] 6.3 Refactor `ci.yml` to consume the composite action
  - Validation behavior unchanged — it remains the branch-protection gate; only the
    shared setup steps are swapped for the composite action (ADR-0038)
  - _Requirements: 8.2, 8.4, 8.5_

- [x] 6.4 Checkpoint — Ensure the workflows parse, both consume the composite
      action, and all tests pass; ask the user if questions arise.
  - Verifies the purpose-scoped workflow split, the prod environment gate, and
    the factored shared setup (no new AWS infrastructure is added)
  - _Requirements: 6.3, 8.1, 8.2, 8.3, 8.4, 8.5_

### Phase 7 — ADRs

- [x] 7.1 ADR: wizard as a thin control plane, console entry point (no `scripts/` folder)
  - _Requirements: 2.3_

- [x] 7.2 ADR: prod gating via GitHub Environments (required reviewers) + OIDC keyless deploy
  - _Requirements: 6.3_

- [x] 7.3 ADR: CLI framework (Typer) and TUI framework (Textual) choices
  - _Requirements: 1.1, 1.3_

### Phase 8 — Property-based testing

One executable property test per stated invariant (invariants only, not for
completeness — AGENTS.md). Each cites the requirement(s) it defends.

- [x]* 8.1 Property test — Front-end equivalence
  - **Property 1: Front-end equivalence**
  - **Validates: Requirements 1.5**

- [x]* 8.2 Property test — No first-party prod deploy
  - **Property 2: No first-party prod deploy**
  - **Validates: Requirements 6.1, 6.2**

- [x]* 8.3 Property test — Config-as-data
  - **Property 3: Config-as-data**
  - **Validates: Requirements 3.3, 3.4, 3.6**

- [x]* 8.4 Property test — Dispatch fidelity
  - **Property 4: Dispatch fidelity**
  - **Validates: Requirements 8.3**

- [x]* 8.5 Property test — Dry-run purity
  - **Property 5: Dry-run purity**
  - **Validates: Requirements 10.5**

- [x] 8.6 Final checkpoint — Ensure `ruff` / `mypy` / `pytest` pass, ask the user if questions arise.
  - Verifies every stated invariant holds end-to-end across the four capabilities
  - _Requirements: 1.5, 3.3, 3.4, 3.6, 6.1, 6.2, 8.3, 10.5_

### Phase 9 — Dispatch-ref hardening

Closes the arbitrary-ref dispatch hole in two layers: the GitHub Environment
deployment branch/tag policy is the boundary; the `deploy.yml` guard is
defence-in-depth (ADR-0040, amended).

- [x] 9.1 Extend `GitHubExecutor.set_environment` with a deployment branch/tag policy
  - Add `allowed_refs: list[str] | None = None` (entries `branch:main` / `tag:v*`,
    mirroring the `<Type>:<id>` spelling `reviewers` already uses); `None` leaves
    an existing policy untouched, as an absent `reviewers` does
  - Send `deployment_branch_policy` with `custom_branch_policies: true` on the
    environment, then one `deployment-branch-policies` entry per pattern with
    `type: branch` or `type: tag`
  - Read the list out of `step.params` in `run_step`; no secret enters argv
  - _Requirements: 6.5_

- [x] 9.2 Plan the policy in the bootstrap planner
  - Have the `github.environment_set` step for `prod` carry `allowed_refs` of
    `tag:v*` + `branch:main` in `params`, and name the admitted refs in the
    step description and in `Plan.effects` (a ref pattern is not secret, so
    masking is unchanged)
  - _Requirements: 4.1, 6.5_

- [x]* 9.3 Executor tests for the policy
  - Assert the `gh api` argv and the JSON body for the environment and for each
    per-pattern policy entry; assert an absent `allowed_refs` sends no policy
  - _Requirements: 6.5_

- [x]* 9.4 Planner tests for the policy
  - Assert the planned `params` and `effects` for a `prod` bootstrap, and that a
    non-prod bootstrap plans no policy
  - _Requirements: 4.1, 6.5_

- [x] 9.5 Add the ref guard step to `deploy.yml`
  - Refuse a prod run whose ref is neither a `v*` tag nor `main`, naming the
    rejected ref; place it before `configure-aws-credentials` so it costs seconds
    and leaves AWS untouched
  - Comment it as defence-in-depth behind the Environment policy, not the
    boundary — it is editable on the dispatched ref
  - _Requirements: 6.6_

- [x]* 9.6 Structural test for the guard and the dispatch ref
  - Parse `deploy.yml` and assert the guard step exists, precedes the
    credential step, and covers the `v*`-tag / `main` cases
  - Assert `run_workflow`'s argv carries no `--ref`, so a dispatch runs the
    default branch
  - _Requirements: 6.6, 6.7_

- [x] 9.7 Checkpoint — Ensure `ruff` / `mypy` / `pytest` pass and the workflow parses; ask the user if questions arise.
  - Verifies both layers: the platform-enforced Environment policy and the
    in-workflow guard, with the control plane dispatching no explicit ref
  - _Requirements: 6.5, 6.6, 6.7_

### Phase 10 — Review follow-ups

- [x] 10.1 Refuse secret-shaped keys on the deploy-time `config set` path
  - In `core/dispatch.py::_plan_config_set`, reject a `config set` whose target is
    not a `Repo_Scoped_SSM_Path` and whose key name matches
    `SECRET_NAME_SUBSTRINGS` (reuse the predicate from
    `core/executors/config.py`; do not clone it): exit `2` before any executor
    call, no PR, error naming the key and directing the operator to an SSM path
  - _Requirements: 3.7, 3.8_

- [x]* 10.2 Update the property suite that currently asserts the leak
  - `tests/unit/test_deploy_properties.py::test_the_ssm_write_masks_its_value_and_the_pull_request_does_not`
    asserts `value in step.command` on the deploy-time branch; re-assert refusal
    (exit `2`, no PR, value absent from `command`/`effects`/PR title)
  - Filter `_param_names` so generated non-SSM keys exclude the secret substrings,
    and add a generator covering secret-shaped names for the refusal case
  - _Requirements: 3.7, 3.8_

- [x] 10.3 Move the prod ref check into a pre-gate job
  - In `.github/workflows/deploy.yml`, hoist the ref guard out of the `deploy` job
    into a job declaring no `environment:`; have `deploy` declare `needs:` on it,
    so a disallowed ref is named before the approval wait
  - Keep the guard's comment: defence-in-depth behind the Environment policy, not
    the boundary — it is editable on the dispatched ref
  - _Requirements: 6.6_

- [x]* 10.4 Update the workflow structural tests for the new job boundary
  - `tests/unit/test_deploy_workflow.py` asserts the guard's position *within* the
    deploy job; assert instead that the guard job exists, references no
    `environment:`, is listed in the deploy job's `needs:`, and covers the
    `v*`-tag / `main` cases
  - _Requirements: 6.6_

- [x] 10.5 Pydantic model at the `gh` boundary
  - Replace `_json_object` / `_str_field` / `_id_field` hand-parsing in
    `core/executors/github.py` with a Pydantic model of a `gh run` view validated
    via `model_validate`; on validation failure return `ok=False` carrying `gh`'s
    own output — never a guessed status
  - _Requirements: 10.2_

- [x] 10.6 Pydantic model at the SSM boundary
  - Replace the `dict.get` + `isinstance` parsing of boto3 SSM responses in
    `core/executors/config.py` with a Pydantic parameter model validated via
    `model_validate`, preserving the same tolerant failure: `ok=False` with the
    underlying output, never a guessed value
  - _Requirements: 10.2_

- [x]* 10.7 Boundary-model tests
  - Feed malformed/partial `gh --json` and SSM payloads and assert `ok=False` with
    the tool's output surfaced verbatim and no status or value invented
  - _Requirements: 10.2_

- [x] 10.8 Open pull requests via `gh api`
  - In `core/executors/config.py`, replace `gh pr create` with
    `gh api --method POST repos/{owner}/{repo}/pulls` and read `html_url` from the
    JSON response; delete the URL-scraping regex
  - Update the planned `PlanStep.command` rendering for `samconfig.pr` to match
  - _Requirements: 3.3_

- [x] 10.9 Fix the vacuous Property 4 assertion
  - `tests/unit/test_deploy_properties.py::test_no_input_the_workflow_insists_on_is_left_unsent`
    admits it asserts nothing; assert the real superset relation — every input the
    control plane sends is declared in `deploy.yml`'s `workflow_dispatch` inputs,
    and every required declared input is sent
  - _Requirements: 1.4, 1.5_

- [x] 10.10 Resolve `build_dispatcher()` and the front-end docstrings
  - `build_dispatcher()` in `core/assembly.py` is production-dead while `cli.py`
    and `tui.py` docstrings still claim both front-ends call it; either route both
    front-ends through it or remove it and correct the docstrings to name
    `build_control_plane()`
  - _Requirements: 1.4_

- [x] 10.11 De-duplicate the cloned constants
  - `RELEASE_BASE_BRANCH` / `GIT_REMOTE` (`core/dispatch.py` and
    `core/executors/git.py`), `DEPLOY_WORKFLOW` vs `DEFAULT_WORKFLOW`
    (`core/dispatch.py` / `core/executors/github.py`), and `MASK` are cloned to
    dodge an import cycle; give them one home the core and the executors both
    import (e.g. a leaf constants module) and delete the copies
  - _Requirements: 1.4, 1.5_

- [x] 10.12 Checkpoint — Ensure `ruff` / `mypy` / `pytest` pass and `deploy.yml` parses; ask the user if questions arise.
  - Verifies the refusal path, the pre-gate ref guard, both boundary models, the
    `gh api` PR path, and the three corrections
  - _Requirements: 1.4, 1.5, 3.3, 3.7, 3.8, 6.6, 10.2_

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["1.3"] },
    { "id": 3, "tasks": ["1.4"] },
    { "id": 4, "tasks": ["2.1"] },
    { "id": 5, "tasks": ["2.2"] },
    { "id": 6, "tasks": ["2.3"] },
    { "id": 7, "tasks": ["3.1", "3.2", "3.3", "3.4"] },
    { "id": 8, "tasks": ["3.5"] },
    { "id": 9, "tasks": ["4.1", "4.2", "4.3", "4.4"] },
    { "id": 10, "tasks": ["4.5"] },
    { "id": 11, "tasks": ["5.1", "5.2"] },
    { "id": 12, "tasks": ["5.3"] },
    { "id": 13, "tasks": ["5.4"] },
    { "id": 14, "tasks": ["5.5"] },
    { "id": 15, "tasks": ["6.1"] },
    { "id": 16, "tasks": ["6.2", "6.3"] },
    { "id": 17, "tasks": ["7.1", "7.2", "7.3"] },
    { "id": 18, "tasks": ["8.1", "8.2", "8.3", "8.4", "8.5"] },
    { "id": 19, "tasks": ["9.1"] },
    { "id": 20, "tasks": ["9.2", "9.5"] },
    { "id": 21, "tasks": ["9.3", "9.4", "9.6"] },
    { "id": 22, "tasks": ["10.1", "10.3", "10.5", "10.6"] },
    { "id": 23, "tasks": ["10.2", "10.4", "10.8", "10.10"] },
    { "id": 24, "tasks": ["10.7", "10.9", "10.11"] }
  ]
}
```

## Notes

- Four capabilities are in scope: **config**, **bootstrap**, **deploy**,
  **release**. This spec builds no runtime feature-flag capability; runtime flags
  are deferred to a follow-up spec, whose sanctioned store will be a DynamoDB
  table read via Powertools over the existing free DynamoDB Gateway endpoint.
  AppConfig is not the store — it is rejected on cost, since it would require a
  paid PrivateLink interface endpoint under no-NAT (ADR-0006).
- This feature adds **no new AWS infrastructure**: no new stacks, resources, or
  VPC endpoints. The only infrastructure-adjacent artefacts are the GitHub
  Actions workflows (`deploy.yml`, refactored `ci.yml`) and the shared composite
  action.
- Tasks marked `*` are optional test sub-tasks and can be skipped for a faster MVP.
- Each task — checkpoints included — references the specific requirements it
  satisfies or verifies; property tests also cite the design property they implement.
- The wizard dispatches only — no deploy logic is reimplemented and no `scripts/`
  ops folder is added.
- Waves respect phase order and the checkpoints: no wave schedules work from a
  later phase before that phase's predecessor checkpoint, tests follow the code
  they exercise, and the Phase 6 workflow tasks follow the `GitHubExecutor`
  (3.2) whose dispatch contract they encode.
