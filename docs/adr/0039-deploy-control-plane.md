# ADR-0039: The deploy wizard is a thin control plane, packaged as a console entry point

## Status

Accepted. Implements Requirement 2.3 of `.kiro/specs/deploy-control-plane/`.

## Context

Deploying this repo already had a working surface: `samconfig.toml` environments
own the CloudFormation parameter set, the SAM CLI applies it, `deploy.yml` runs
the environment-gated CD job, and a `vX.Y.Z` tag triggers a release. What was
missing was not capability but a *front door* — one place an operator or an agent
could express "deploy dev", "open a config PR", "cut v1.4.0" without knowing
which of those tools to reach for.

Three shapes were considered, in sequence:

1. **Orchestrate the existing `Makefile`.** Rejected: the targets are themselves
   a thin layer over SAM, so a wizard on top would be a third layer whose job is
   to compose the second layer's strings. Two levels of indirection to reach one
   `sam deploy`, and the wizard's error surface becomes Make's.
2. **The wizard owns the deploy logic in typed Python.** Rejected as the more
   dangerous option, because it looks like the better one: parameter assembly,
   stack ordering and rollout sequencing in Pydantic models are pleasant to write
   and to test. But CI must deploy too, so the logic would exist twice — once in
   the wizard, once in `deploy.yml` — and the copies drift silently, the failure
   mode that sank the previous rewrite.
3. **A thin control plane.** Accepted.

An ops folder of bespoke shell scripts was never a candidate: it is a named
anti-pattern in `AGENTS.md`.

## Decision

The wizard **reimplements no deploy logic**. It translates intent into a typed
`Command`, plans it, and dispatches to sanctioned executors — the SAM CLI,
GitHub Actions via `gh`, and `git` — which do the work. `samconfig.toml` owns the
parameter set: `SamExecutor` selects only `--config-env <stage>` and never
composes `--parameter-overrides`, so there is no second place a parameter can be
defined.

**Planning is pure and carries structured intent.** `Dispatcher.plan()` resolves a
`Command` into a `Plan` of `PlanStep`s and routes it to exactly one executor. Each
step carries `op` (a closed `Op` vocabulary) plus typed `params`, alongside a
`command` string that is a *display rendering only* — no executor parses it.
Routing is therefore decided once, and the plan an operator previews is the same
value that later executes, so a `--dry-run` preview cannot drift from what runs.

**Packaging is a console entry point, not a script folder.** `pyproject.toml`
declares `bdo-deploy = "bdo_deploy.cli:main"`, and the package lives at
`src/tools/bdo_deploy/`, deliberately **outside `src/layer/python/`**. That
placement is the enforcement mechanism, not a convention: the `bdo-common` layer
is built by globbing `src/layer/python/`, so the wizard's ops-only dependencies
(Typer, Textual) cannot reach a Lambda runtime however carelessly they are
imported. The wheel packages both directories so the console script resolves
after `uv sync`; only the layer path is what SAM builds.

**No `scripts/` ops folder is added.** The distinction is precise and worth
stating, because `scripts/` exists and is staying: it holds genuine build and
validation utilities that CI invokes as first-class steps —
`validate_regions.py`, `export_openapi.py`, `build_market_catalog.py`,
`samconfig_regions.py` — each one authoritative logic with a single call site
family. What this decision forgoes is a folder of bespoke *deploy/ops* shell
scripts standing in for the sanctioned tools. Nothing here removes or deprecates
the existing directory.

## Consequences

- (+) There is exactly one deploy implementation. A change to the parameter set
  is a `samconfig.toml` edit that both the wizard and CI pick up; neither can
  drift from the other, because neither holds a copy.
- (+) The wizard structurally cannot run a production deploy — it has no prod
  `sam deploy` path at all, only a dispatch of the environment-protected CI job.
  That falls out of being thin rather than being separately enforced.
- (+) Distributing it as `bdo-deploy` on `PATH` gives agents a discoverable,
  `--json`-speaking entry point, with no ops directory to grow shell scripts in.
- (+) Ops-only dependencies are kept out of the Lambda layer by file placement,
  which is checkable at a glance and cannot be forgotten in a review.
- (−) The wizard is only as good as the tools it shells out to. It inherits their
  error messages verbatim — a confusing CloudFormation failure stays a confusing
  CloudFormation failure, surfaced rather than interpreted.
- (−) A `PlanStep`'s `command` display string and its `op`/`params` must be kept
  consistent **by construction**, since nothing derives one from the other. A
  step whose rendering says one thing while its params do another would make the
  preview lie; this is what to check when adding an `Op`.
- (−) Four executor adapters plus a composition root is more indirection than a
  shell script for anyone reading it cold. The payoff is the single
  implementation and the testable seam, not concision at the call site.
- (−) The tool can do nothing the sanctioned tools cannot. A capability that SAM,
  `gh` and `git` do not offer is out of reach until one of them offers it — by
  design, and the constraint to weigh before adding a capability.
