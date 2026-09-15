# 0038. Purpose-scoped GitHub Actions workflows

Date: 2026-09-14

## Status

Accepted. Amends the "exactly one CI workflow" rule in `AGENTS.md` and the
"single GitHub Actions workflow" rule in `.kiro/steering/tech.md`.

## Context

The repository standardised on exactly one CI workflow after an earlier rewrite
accumulated multiple workflows that duplicated setup steps and drifted out of
sync. That rule conflates two separate concerns:

1. Keeping one authoritative validation gate for branch protection — sound.
2. Forbidding any other workflow file — too broad.

As the deployment surface formalises (a deploy control plane that triggers an
environment-gated deploy, plus security scanning and a scheduled production
canary), a single workflow is forced to carry unrelated triggers and permission
scopes in one file: a deploy job needs `id-token: write` and a protected
Environment, a scanning job needs `security-events: write`, and validation
should need neither. Collapsing these into one file violates least privilege and
couples unrelated lifecycles. The drift that motivated the original rule came
from duplicated, un-factored steps, not from the number of workflow files.

## Decision

Repeal the literal "exactly one CI workflow" constraint. Adopt a governance
principle instead:

- One authoritative *validation* workflow, `.github/workflows/ci.yml`, is the
  branch-protection gate.
- An additional workflow is allowed only when justified by a distinct trigger
  (`schedule`, `workflow_dispatch`, `push: tags`) or a distinct permission scope
  (e.g. `id-token: write`, `security-events: write`). A change that is "just more
  PR checks" is added as a job in `ci.yml`, not a new file.
- Shared setup (checkout, Python setup, `uv sync`) is factored into a reusable
  workflow (`workflow_call`) or a composite action under `.github/actions/` and
  never copy-pasted, so workflows cannot drift.
- Each workflow's purpose and trigger is recorded in the runbook or an ADR.

The intended workflow set is `ci.yml` (validation gate), `deploy.yml` (CD on
`push: tags` + `workflow_dispatch`, environment-gated, OIDC), `codeql.yml` (SAST
on pull request + schedule), and `smoke.yml` (scheduled production canary).
`AGENTS.md` and `.kiro/steering/tech.md` are amended accordingly.

## Consequences

- Each workflow scopes only the permissions it needs; branch protection maps to
  a focused validation workflow; the deploy control plane can target a dedicated
  `deploy.yml`.
- The original drift risk is mitigated structurally by the shared reusable
  workflow / composite action requirement rather than by limiting the file count.
- There are more workflow files to keep in mind, and the "distinct trigger or
  permission scope" bar must be applied when adding one; otherwise a check
  belongs as a job in `ci.yml`.
- If the shared-setup discipline lapses, duplicated steps could drift again; the
  composite action requirement exists to prevent that.
