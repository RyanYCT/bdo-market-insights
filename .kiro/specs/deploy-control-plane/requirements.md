# Requirements Document

## Introduction

`deploy-control-plane` is a **thin control plane** over this repo's existing deployment
surface. It reimplements no deploy logic: it translates operator or agent intent
into a typed `Command`, plans it, and **dispatches to sanctioned executors** —
the SAM CLI, GitHub Actions (via `gh`), and `git` — which perform the actual
work. The wizard never assembles a CloudFormation parameter set itself
(`samconfig.toml` environments own it; the wizard only selects `--config-env`)
and has **no code path that runs a production deploy**: production is reached
only by triggering a platform-gated GitHub Actions job.

The tool ships **two thin front-ends over one shared command core**: a
non-interactive **CLI mode** for agents, automation, and CI (machine-readable
`--json`, `--yes`, `--dry-run`, never prompts) and an interactive **TUI mode**
for humans (guided flows, confirmations). Both build the same typed `Command`
and route it through the same `Dispatcher`, so the two modes are behaviourally
equivalent. Five capabilities are exposed — **config**, **toggle**, **setup**,
**deploy**, **trigger** — each mapped onto an executor rather than onto bespoke
Python. The wizard is packaged as a console entry point; there is no ops folder
of scripts, and its dev/ops-only dependencies stay out of the Lambda layer and
`bdo_common`.

Rationale is captured in ADRs rather than inline: **ADR-0006** (no NAT),
**ADR-0024** (SSM key paths, not values), **ADR-0025** (auto-migrations),
**ADR-0028** (bootstrap orchestrator auto-run), **ADR-0036** (BdoRegions single
toggle), **ADR-0037** (ApiVersion from release tag), **ADR-0038** (purpose-scoped
CI/CD workflows).

## Glossary

- **Wizard**: The `deploy-control-plane` tool, packaged as a console entry point in
  `pyproject.toml`. A thin control plane; it dispatches, it does not reimplement.
- **CLI_Mode**: The non-interactive front-end (Typer-style subcommands + flags)
  used by agents, automation, and CI. Supports `--json`, `--yes`, `--dry-run`;
  never prompts.
- **TUI_Mode**: The interactive full-screen front-end (Textual-style) used by
  humans, with guided flows and explicit confirmations. A convenience skin, not
  the system of record.
- **Command**: The typed request object (`capability`, `target`, `stage`,
  `version`, `args`, `dry_run`, `assume_yes`) that both front-ends build.
- **Dispatcher**: The single component that resolves a `Command` into a `Plan`
  and routes it to exactly one `Executor`. Planning is pure and side-effect-free.
- **Plan**: The resolved, side-effect-free preview of a `Command` — the ordered
  executor calls (exact command lines), the human-readable effects, and whether
  confirmation is required.
- **Result**: The typed outcome (`ok`, `exit_code`, `summary`, `changes`,
  `run_url`, `raw_output`) returned after execution.
- **Executor**: An adapter over one sanctioned tool. The wizard shells out to
  executors; it never performs deploy work directly.
- **SamExecutor**: The executor over the SAM CLI (`sam validate/build/deploy/
  sync`) for LOCAL, non-prod work. Selects `--config-env`; never composes
  `--parameter-overrides`.
- **ActionsDispatcher**: The executor over the GitHub CLI that triggers a CI run
  (`gh workflow run`) and surfaces its status (`gh run watch` / `gh run view`).
- **GitExecutor**: The executor over `git` that verifies release preconditions
  and creates/pushes the release tag.
- **ConfigStore**: The executor for config-as-data across the three sanctioned
  locations: `samconfig.toml` (changed via PR through `gh`), SSM Parameter Store
  (operational config, audited writes), and AWS AppConfig (runtime feature flags).
- **Target**: The execution target carried by a `Command`. `LOCAL` routes to the
  `SamExecutor` (dev/personal only); `CI` routes to the `ActionsDispatcher`
  (shared-env and prod).
- **Repo_Scoped_SSM_Path**: An SSM parameter name of the form
  `/bdo-market-insights/<stage>/<category>/<key>`. A bare `/bdo/...` path is not
  repo-scoped.
- **Release_Tag**: A git tag matching `^v\d+\.\d+\.\d+$`; its value becomes
  `ApiVersion` (ADR-0037) and drives the tag-triggered pipeline.
- **Feature_Flag**: A runtime flag stored in AWS AppConfig, flipped without a
  redeploy and read in Lambdas through the Powertools feature-flags provider.

## Requirements

### Requirement 1: Two thin front-ends over one shared command core

**User Story:** As an operator and as an automation agent, I want one wizard with an interactive human mode and a non-interactive machine mode over a single command core, so that humans and agents drive identical behaviour without a second implementation to reconcile.

#### Acceptance Criteria

1. THE Wizard SHALL provide CLI_Mode as a non-interactive front-end that accepts one subcommand per capability (`config`, `toggle`, `setup`, `deploy`, `trigger`) and the flags `--json`, `--yes`, and `--dry-run`, and SHALL NOT prompt for any input.
2. WHERE `--json` is set, THE Wizard SHALL emit exactly one serialized `Result` object as the sole content on stdout and write all human-readable log lines to stderr.
3. THE Wizard SHALL provide TUI_Mode as an interactive front-end that presents guided flows and renders the `Plan` at an explicit confirmation step before any mutating execution.
4. THE Wizard SHALL have both CLI_Mode and TUI_Mode build the same typed `Command` and route it through the same `Dispatcher`, with each front-end limited to collecting intent and rendering the `Result`.
5. WHEN the same intent is submitted through CLI_Mode and through TUI_Mode, THE Dispatcher SHALL produce byte-for-byte identical serialized `Plan` objects.

### Requirement 2: Thin control plane over sanctioned executors

**User Story:** As a maintainer, I want the wizard to only translate intent and dispatch to sanctioned tools, so that there is no second copy of deploy logic to drift and no bespoke ops scripting.

#### Acceptance Criteria

1. THE Dispatcher SHALL resolve every `Command` into a `Plan` and route that `Plan` to exactly one `Executor` (`SamExecutor`, `ActionsDispatcher`, `GitExecutor`, or `ConfigStore`).
2. THE Wizard SHALL NOT assemble a CloudFormation parameter set; for a `SamExecutor` deploy it SHALL select only `--config-env <stage>` and SHALL NOT compose `--parameter-overrides`.
3. THE Wizard SHALL be packaged as a console entry point in `pyproject.toml` and SHALL NOT introduce an ops folder of scripts.
4. WHEN the Dispatcher plans a `Command`, THE Dispatcher SHALL perform planning as a pure, side-effect-free operation.

### Requirement 3: Config capability

**User Story:** As an operator, I want to view merged configuration and change it through the correct sanctioned location, so that I can inspect and update config without editing scattered sources by hand.

#### Acceptance Criteria

1. WHEN `config show` is invoked for a stage, THE ConfigStore SHALL render a merged read of `samconfig.toml`, SSM, and AppConfig for that stage and SHALL NOT mutate any source.
2. WHEN `config show` renders a value whose backing SSM parameter is a SecureString, or whose key name contains any of the substrings `secret`, `password`, `token`, or `key` (case-insensitive), THE Wizard SHALL mask that value before rendering it.
3. WHEN `config set` changes deploy-time configuration held in a tracked file, THE ConfigStore SHALL open a pull request against that file via `gh` and SHALL NOT mutate the tracked file on the working branch directly.
4. WHEN `config set` changes operational configuration, THE ConfigStore SHALL write the value to its Repo_Scoped_SSM_Path with an audit record and SHALL NOT write it to any tracked file.
5. IF a `ConfigStore` write of an operational value fails, THEN THE Wizard SHALL return an error identifying the failed write and SHALL leave the prior value at the targeted Repo_Scoped_SSM_Path unchanged.

### Requirement 4: Toggle capability

**User Story:** As an operator, I want deploy-time toggles and runtime feature flags handled through the right location, so that a config change either flows through review or flips live, and never lands in a fourth place.

#### Acceptance Criteria

1. WHEN a deploy-time toggle (for example `BdoRegions`) is changed, THE ConfigStore SHALL apply the change to `samconfig.toml` by opening a pull request via `gh` and SHALL NOT flip it in-place at runtime.
2. WHEN a runtime Feature_Flag is changed, THE ConfigStore SHALL flip the flag in AWS AppConfig without requiring a redeploy.
3. THE Wizard SHALL confine runtime Feature_Flag storage to AWS AppConfig, read in Lambdas through the Powertools feature-flags provider, and SHALL NOT introduce a fourth configuration location for any toggle or flag.

### Requirement 5: Setup capability (one-time bootstrap helper)

**User Story:** As an operator standing up a new environment, I want a clearly-labelled one-time helper for the platform bootstrap that a routine deploy cannot self-apply, so that I complete initial setup without turning it into a bespoke imperative orchestration on the routine path.

#### Acceptance Criteria

1. THE Wizard SHALL expose the setup capability as a one-time helper that wraps `sam pipeline bootstrap` (provisioning the OIDC deploy role and the artifact bucket) and configures the GitHub Environments and secrets.
2. THE Wizard SHALL label the setup capability in help output and menus as a one-time, out-of-band step so that it is not used on the routine deploy path.
3. IF a step of the setup helper fails, THEN THE Wizard SHALL stop at the first failing step, report which steps completed and which step failed, and preserve the effects of any completed step without rolling them back.

### Requirement 6: Deploy capability

**User Story:** As an operator and as CI, I want one deploy action that routes by target, so that dev deploys run locally through the SAM CLI while shared and prod deploys are executed by CI, and a fresh environment stands up declaratively.

#### Acceptance Criteria

1. WHEN `deploy` is invoked with `target=LOCAL` for a dev or personal stage, THE Wizard SHALL route to the SamExecutor and run `sam deploy --config-env <stage>` (or `sam sync` for the dev fast-loop).
2. IF `deploy` is invoked with `target=LOCAL` and `stage=prod`, THEN THE Wizard SHALL reject the request at validation, exit with code `2`, make no CloudFormation, file, SSM, AppConfig, git, or SAM state change, and return an error naming the offending field and directing the operator to `trigger release`.
3. WHEN `deploy` is invoked with `target=CI` for a shared-env or prod stage, THE Wizard SHALL route to the ActionsDispatcher and TRIGGER a GitHub Actions run, and SHALL NOT itself execute `sam deploy` for that stage.
4. WHEN a fresh environment is deployed, THE Wizard SHALL reach target state through a single declarative deploy that relies on the stack self-bootstrapping (auto-migrate custom resource per ADR-0025; bootstrap orchestrator auto-run per ADR-0028) and SHALL NOT require any imperative multi-step orchestration on the routine path.

### Requirement 7: Production is platform-gated, not code-gated

**User Story:** As a maintainer, I want production deploys gated by the platform rather than by application code, so that the wizard structurally cannot run a prod deploy and prod is protected by required reviewers and keyless trust.

#### Acceptance Criteria

1. THE Wizard SHALL have no code path that runs `sam deploy --config-env prod`; for all commands it can construct, no `Plan` SHALL execute a production `sam deploy` locally.
2. THE Wizard SHALL reach production only by dispatching an environment-protected GitHub Actions job through the ActionsDispatcher.
3. THE production GitHub Actions job SHALL run inside a GitHub Environment configured with required reviewers and OIDC keyless deploy (no static AWS credentials).

### Requirement 8: Trigger capability

**User Story:** As a release manager, I want to start a release through the wizard with the repo's guardrails applied, so that a tag-triggered prod pipeline runs and I can observe the dispatched run.

#### Acceptance Criteria

1. WHEN `trigger release` is invoked with a version matching the Release_Tag format `^v\d+\.\d+\.\d+$`, THE GitExecutor SHALL verify the release preconditions — working tree is clean, current branch is `main`, and the tag does not already exist locally or on the origin — before creating any tag.
2. IF `trigger release` is invoked with a version that does not match `^v\d+\.\d+\.\d+$`, THEN THE Wizard SHALL reject the request with exit code `2`, SHALL NOT verify preconditions, and SHALL NOT create or push a tag.
3. IF a release precondition is violated, THEN THE Wizard SHALL report which specific precondition failed, SHALL NOT create or push a tag, and SHALL leave the git working tree, current branch, and existing tags unchanged.
4. WHEN the release preconditions pass and the action is confirmed, THE GitExecutor SHALL create the `vX.Y.Z` tag and push it to the origin so the tag-triggered pipeline runs.
5. THE Wizard SHALL make `trigger release` the sole initiator of a production deploy, with `gh workflow run` manual dispatch as the alternative trigger, and SHALL NOT provide any other path that initiates a prod deploy.
6. WHEN a trigger dispatches a CI run, THE Wizard SHALL surface the dispatched run's URL and status (via `gh run watch` / `gh run view`).

### Requirement 9: Purpose-scoped CI/CD workflows

**User Story:** As a maintainer, I want deployment handled by a dedicated CD workflow separate from the validation workflow, so that each workflow scopes only the permissions it needs and the Actions UI and the wizard trigger the identical deploy job.

#### Acceptance Criteria

1. THE deploy workflow SHALL be a dedicated `.github/workflows/deploy.yml`, triggered on `push` of tags matching `v*` and on `workflow_dispatch` with typed inputs (`stage`, `version`, toggles), running environment-gated deploy jobs with OIDC keyless deploy.
2. THE validation workflow `.github/workflows/ci.yml` SHALL remain the branch-protection gate and SHALL be unchanged by this feature.
3. THE ActionsDispatcher SHALL target `deploy.yml` (`gh workflow run deploy.yml -f stage=... -f version=...`), and the `deploy.yml` `workflow_dispatch` typed inputs SHALL be a superset of the inputs the wizard sends, so that the GitHub Actions UI and the wizard dispatch the identical deploy job with identical inputs.
4. THE shared setup steps (checkout, Python setup, `uv sync`) SHALL be factored into a reusable composite action under `.github/actions/setup/` used by the workflows, so that the workflows cannot drift (ADR-0038).
5. WHERE a new check has neither a distinct trigger nor a distinct permission scope, THE check SHALL be added as a job in `ci.yml` rather than as a new workflow file (ADR-0038).

### Requirement 10: Naming, config-authority, and dependency constraints

**User Story:** As a maintainer, I want the repo's naming and config-authority conventions enforced, so that deploys stay boring and correct and no account-specific values leak into tracked files or the runtime layer.

#### Acceptance Criteria

1. IF an SSM parameter name to be written is not a Repo_Scoped_SSM_Path matching `/bdo-market-insights/<stage>/<category>/<key>` (including any bare `/bdo/...` path), THEN THE Wizard SHALL reject the write with exit code `2`, write no SSM parameter for that name, leave any existing value unchanged, and emit an error naming the offending name and the required format.
2. THE Wizard SHALL pass SSM key paths — never secret values — into CloudFormation, so CloudFormation resolves them at deploy time (ADR-0024).
3. THE Wizard SHALL treat `BdoRegions` in `samconfig.toml` as the single active-region toggle (ADR-0036) and SHALL derive `ApiVersion` from the Release_Tag (ADR-0037).
4. THE Wizard SHALL keep runtime Feature_Flag reads working under the no-NAT constraint (ADR-0006) by relying on an AppConfig VPC endpoint for in-VPC Lambdas.
5. THE Wizard SHALL keep its dev/ops-only dependencies out of the Lambda layer and `bdo_common`.

### Requirement 11: Error handling and exit codes

**User Story:** As an automation agent, I want a deterministic exit-code contract and verbatim executor output, so that I can react programmatically to success, validation errors, confirmation gates, and executor failures.

#### Acceptance Criteria

1. WHEN a `Command` completes successfully, THE Wizard SHALL exit with code `0` and return a `Result` with `ok=true`.
2. IF an executor (`sam`, `gh`, or `git`) fails, THEN THE Wizard SHALL exit with code `1`, surface the underlying executor output verbatim in the `Result`, and SHALL NOT emit a Python traceback.
3. IF a `Command` fails validation (unknown stage, malformed version, non-repo-scoped SSM path, or a LOCAL prod deploy), THEN THE Wizard SHALL exit with code `2` before any executor call, return a `Result` naming the offending field, and make no mutation to any external system so that all target state is preserved unchanged.
4. IF a mutating `Command` is invoked in CLI_Mode without `--yes`, THEN THE Wizard SHALL exit with code `3`, return a `Result` containing the `Plan` so the caller can inspect the effects and re-invoke with `--yes`, and make no mutation to any external system.
5. WHEN `--dry-run` is supplied in CLI_Mode or preview is chosen in TUI_Mode, THE Wizard SHALL render the `Plan` and perform no file write, PR, AWS API mutation, SSM or AppConfig write, git mutation, `sam deploy`, or workflow dispatch, leaving all state unchanged.
6. WHEN a `Command` has `target=CI`, THE Wizard SHALL report the dispatched CI run URL and treat the CI run itself as the authoritative pass or fail.
7. THE Wizard SHALL map every terminating outcome to exactly one exit code from the set {`0` = success, `1` = executor or action failed, `2` = usage/validation error before any executor call, `3` = confirmation required}.

## Out of Scope

- Reimplementing any deploy logic in the wizard; it dispatches to the SAM CLI,
  GitHub Actions, and `git`, which do the work.
- Adding new AWS infrastructure or new stacks; the wizard is a control surface
  over the existing deploy surface.
- The CLI framework (Typer vs argparse) and TUI framework (Textual vs
  questionary + rich) choices, captured as follow-up ADRs rather than functional
  requirements here.
- The existing non-deploy CI jobs, which remain unchanged.
