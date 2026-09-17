# ADR-0040: Prod is gated by GitHub Environments, deployed keylessly via OIDC

## Status

Accepted. Implements Requirement 6.3 of `.kiro/specs/deploy-control-plane/`.

## Context

The control plane (ADR-0039) can trigger a production deploy but must never
perform one. That leaves three questions the spec deliberately does not answer
inline: what actually holds a prod deploy back, what credentials the deploy
runs with, and what must be true of the commit before it deploys.

The tempting answer to the first is a runtime check in the wizard — refuse a
prod deploy unless `in_ci`. It is one line, and it is the wrong control:
whoever can run the deploy can also edit the line out, so the guard sits inside
the blast radius it is supposed to bound. A gate is only a gate if it is
enforced somewhere the deploying party cannot rewrite.

## Decision

**1. The platform holds the gate; the code has no prod path to guard.** GitHub
Environments `dev` and `prod` exist, with **required reviewers on `prod`**.
`deploy.yml`'s job declares `environment:` following the selected stage, so
every route into it — tag push and `workflow_dispatch` alike — is held by
GitHub before any step runs, including checkout.

The application-code half is not a check but an absence: a
`Command(capability=DEPLOY, target=LOCAL, stage="prod")` is
**unconstructable**, refused by a Pydantic model validator with
`validate_assignment=True`, so the only prod plan the planner can produce is
"dispatch the environment-protected `deploy.yml`". An unconstructable model
beats an `in_ci` check in two ways: it fails at construction rather than
mid-plan, and it is not the thing being relied on for safety. The *safety*
claim rests entirely on the Environment, which GitHub enforces from outside the
repository — unreachable by a commit, a rebase, or a `--no-verify` push.

**2. OIDC keyless deploy, with the role ARN as an Environment secret.**
`deploy.yml` grants `id-token: write` on the deploy job only and assumes a role
via `aws-actions/configure-aws-credentials`. The role ARN comes from the
`AWS_DEPLOY_ROLE_ARN` **Environment** secret.

Two precise points. An ARN is an *identifier*, not a credential — it is secret
only in the sense that it names an account, and possessing it grants nothing.
The value of OIDC is the other side: **no long-lived AWS key exists anywhere**
— not in the repository, not in GitHub, not on an operator's laptop — so there
is no key to leak, rotate, or scope. And storing the ARN as an *Environment*
secret rather than a repo secret is what binds it to the protected
environment: a job that has not passed `prod`'s reviewers cannot read `prod`'s
secret, so the credential path and the approval path are the same path.

Bootstrap cannot undo this: a `prod` bootstrap that names no required reviewer
is refused by the same model validator that refuses the LOCAL prod deploy, so
prod cannot be created into an unprotected state by the one code path that
creates it.

**3. The deploy is deliberately not gated on the full validation suite.**
Actions cannot express a cross-workflow `needs`, and rather than fake one, the
coupling is dropped on the strength of what is already true: a tagged commit
reached `main` under branch protection and is therefore already validated, and
`release_preconditions` refuses to tag anything but a clean checkout of `main`
whose tag does not yet exist locally or on the origin. The one deploy-affecting
check `sam build` cannot cover — `scripts/validate_regions.py`, guarding
`samconfig.toml`'s `BdoRegions` — is invoked by `deploy.yml` itself, scoped to
the target stage. That is a second *call site* of one authoritative script,
which is the sharing ADR-0038 asks for, not the copy-pasted step logic it
forbids.

Two alternatives were weighed and rejected:

- **Give `ci.yml` a `workflow_call` trigger and have `deploy.yml` `needs` it.**
  Rejected: it puts full-suite latency (including the Postgres integration job)
  in front of every release for validation that has already run, and it would
  have required changing `ci.yml`'s triggers — the branch-protection gate — to
  serve the deploy path.
- **A separate narrow reusable pre-deploy workflow.** Rejected: a whole
  workflow file to invoke a single script clears no bar in ADR-0038, and is one
  more file to keep in sync with `ci.yml`.

## Consequences

- (+) The prod gate is enforced by GitHub, outside the repository. No commit,
  and no edit to this codebase, can remove it — the property a code-level
  `in_ci` check cannot offer at any level of care.
- (+) There is no long-lived AWS credential to leak or rotate, and reading the
  role ARN requires having already cleared the environment's protection.
- (+) Releases are not slowed by re-validating a commit `main` already
  validated, and the single check `sam build` misses still runs, stage-scoped,
  before any AWS call.
- (−) A pushed tag starts the deploy while `ci.yml` validates the same commit
  in parallel. A commit that somehow reached `main` unvalidated — an admin
  bypass of branch protection, or a squash-merge commit whose own `push: main`
  run has not finished — can begin deploying before validation completes. For
  prod the reviewer gate is the human backstop; **dev has none**.
- (−) `workflow_dispatch` accepts an arbitrary ref, so "deploy this branch to
  dev" is expressible, not an accident of configuration. Prod still requires
  reviewers whatever ref is dispatched.
- (−) The reviewer gate lives in repository settings, outside the repo, so it
  is **not captured in IaC** and must be verified in the GitHub UI (or set via
  the `bootstrap` capability). A repo restored from source alone would have no
  gate until it is bootstrapped — the one place this decision trades
  "everything in IaC" for "enforced outside the code".
- (−) Required reviewers mean prod deploys are **not unattended**: a release
  waits for a human, so a fully automated release train is out of reach while
  this gate stands.
