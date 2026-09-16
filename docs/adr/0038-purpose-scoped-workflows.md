# ADR-0038: Purpose-scoped GitHub Actions workflows

## Status

Accepted. Amends the single-CI-workflow rule in `AGENTS.md` and
`.kiro/steering/tech.md`.

## Context

The repository standardised on exactly one CI workflow after an earlier rewrite
accumulated multiple workflows that duplicated setup steps and drifted out of
sync. That rule conflates two separate concerns:

1. Keeping one authoritative validation gate for branch protection — sound.
2. Forbidding any other workflow file — too broad.

As the deployment surface formalises, a single workflow is forced to carry
unrelated triggers and permission scopes in one file: a deploy job needs
`id-token: write` and a protected Environment, while validation should need
neither. Collapsing them violates least privilege and couples unrelated
lifecycles. The drift that motivated the original rule came from duplicated,
un-factored steps — not from the number of workflow files.

## Decision

Replace the literal "exactly one CI workflow" constraint with a governance rule:

- **One authoritative validation workflow.** `.github/workflows/ci.yml` is the
  branch-protection gate.
- **A separate workflow needs a distinct justification** — either a distinct
  trigger (`schedule`, `workflow_dispatch`, `push: tags`) or a distinct
  permission scope (e.g. `id-token: write` to deploy, `security-events: write`
  to scan). A change that is only "more PR checks" is added as a job in
  `ci.yml`, not as a new file.
- **Shared setup is factored, never copy-pasted.** The common checkout →
  Python setup → `uv sync` sequence lives in a reusable workflow
  (`workflow_call`) or a composite action under `.github/actions/`, so the
  workflows cannot drift.

The current set is `ci.yml` (validation) and `deploy.yml` (CD on `push: tags`
and `workflow_dispatch`, environment-gated, OIDC). Any further purpose-scoped
workflow would clear the bar above on its own merits and needs no amendment to
this ADR; none is committed to here.

`AGENTS.md` and `.kiro/steering/tech.md` are amended to state the rule.

## Consequences

- (+) Each workflow scopes only the permissions it needs; a deploy workflow's
  `id-token: write` and protected Environment stay out of every PR run.
- (+) Branch protection maps to a focused validation workflow, and the deploy
  control plane can dispatch a dedicated `deploy.yml`.
- (+) The original drift risk is mitigated structurally by the shared
  reusable-workflow / composite-action requirement rather than by capping the
  file count.
- (−) There are more workflow files to hold in mind, and the "distinct trigger
  or permission scope" bar must be applied deliberately when adding one;
  otherwise a check belongs as a job in `ci.yml`.
- (−) If the shared-setup discipline lapses, duplicated steps could drift again.
  The composite-action requirement exists to prevent that, and is what to check
  in review.
