# Requirements Document

## Introduction

`deploy-workflow` moves deployment out of the validation workflow into a
dedicated `.github/workflows/deploy.yml`. The new workflow runs the full CI suite
before every deploy. It gates prod on a GitHub Environment with a required
reviewer, and it assumes a per-stage AWS role through OIDC. One script,
`scripts/deploy_params.py`, prints the complete SAM parameter set for both
`make deploy` and CI, so the two paths cannot differ. Operators can redeploy or
roll back prod from an existing `v*` tag, and deploy or create dev from any
branch, without a new tag. Dev is short-lived: there is no dev-to-prod
promotion, and a tag deploys prod only.

This spec replaces `.kiro/specs/deploy-control-plane/`. That spec is not
implemented. The rationale is recorded in ADR-0039 (authored as a task). It
builds on ADR-0024, ADR-0025, ADR-0028, ADR-0029, ADR-0036, ADR-0037, and
ADR-0038.

## Glossary

- **Validation workflow**: `.github/workflows/ci.yml`, the branch-protection
  gate. It is also callable through `workflow_call`.
- **Deploy workflow**: `.github/workflows/deploy.yml`.
- **Release tag**: A git tag that matches `v<digit>*` on a commit that is an
  ancestor of `origin/main`.
- **Parameter set**: The complete `--parameter-overrides` value for one
  `sam deploy`. It has one entry for each key in `template.yaml` `Parameters`.
- **Bring-up**: The creation of a stack that does not exist. It has two
  `sam deploy` phases with the DB role bootstrap between them (ADR-0025).
- **Deploy role**: The per-stage IAM role that a Deploy workflow job assumes
  through GitHub OIDC.
- **Environment subject**: The OIDC `sub` claim
  `repo:<owner>/<repo>:environment:<stage>`.

## Requirements

### Requirement 1: One source for the parameter set

**User Story:** As a maintainer, I want one script to build the parameter set,
so that `make deploy` and CI always pass the same complete set.

#### Acceptance Criteria

1. THE system SHALL provide `scripts/deploy_params.py --stage <stage> --version
   <version>` that prints the Parameter set on one line to stdout.
2. THE Parameter set SHALL contain exactly the keys of `template.yaml`
   `Parameters`. A unit test SHALL compare the two key sets and fail on any
   difference.
3. THE script SHALL read static values from the stage's
   `parameter_overrides` in `samconfig.toml`. It SHALL compute `ApiVersion`,
   `MigrationsFingerprint`, and the four SSM key paths
   (`/bdo-market-insights/<stage>/<category>/<key>`). EACH static value SHALL
   be in a form that SAM CLI reads with the same value as the script.
4. THE script SHALL own the `MigrationsFingerprint` calculation. The Makefile
   and CI SHALL NOT calculate it.
5. IF any input is missing or not valid THEN the script SHALL exit non-zero,
   write the cause to stderr, and write nothing to stdout.
6. WHEN `make deploy` or CI calls the script and the script fails THEN the
   deploy SHALL stop before `sam deploy` starts.

### Requirement 2: Validation gates every deploy

**User Story:** As a maintainer, I want the full CI suite to pass on the exact
commit before any deploy, so that no path deploys code that CI did not check.

#### Acceptance Criteria

1. THE Validation workflow SHALL accept `workflow_call`. It SHALL NOT trigger on
   tags and SHALL NOT contain a deploy job.
2. THE Deploy workflow SHALL run the Validation workflow as a job named
   `validate`.
3. THE `deploy` job SHALL need both `validate` and `guard`. IF either fails THEN
   the system SHALL NOT start the `deploy` job.
4. A unit test SHALL parse `deploy.yml` and fail if the `deploy` job does not
   need `validate` and `guard`.
5. IF `make deploy` is called with `STAGE=prod` THEN it SHALL exit non-zero
   before `sam build` and name `deploy.yml`.

### Requirement 3: Prod release gate

**User Story:** As the release owner, I want prod deploys to need a release tag
and my approval, so that no unapproved ref reaches prod.

#### Acceptance Criteria

1. WHEN a `v*` tag is pushed THEN the Deploy workflow SHALL deploy stage `prod`.
2. WHEN the stage is `prod` AND the ref does not match `refs/tags/v<digit>*`
   THEN the `guard` job SHALL fail with a job-log message that names the ref.
3. WHEN the stage is `prod` AND the commit is not an ancestor of `origin/main`
   THEN the `guard` job SHALL fail with a job-log message that names the
   commit SHA.
4. THE `deploy` job SHALL use the GitHub Environment named after the stage.
5. THE `prod` Environment SHALL have a required reviewer and a deployment policy
   that allows `v*` tags only. "Prevent self-review" SHALL stay off, because one
   maintainer approves own releases.
6. A tag ruleset SHALL let only repository admins create, update, or delete
   `v*` tags.
7. THE guard checks SHALL be in `scripts/deploy_guard.py`. Unit tests SHALL
   cover a tag on `main`, a tag off `main`, a prod dispatch from a branch, the
   ref `refs/tags/vfoo`, and a dev dispatch.

### Requirement 4: Deploy without a new tag

**User Story:** As a maintainer, I want to start a deploy by hand, so that I can
redeploy or roll back prod and test a branch on dev without a new tag.

#### Acceptance Criteria

1. THE Deploy workflow SHALL accept `workflow_dispatch` with a required input
   `stage` of type choice (`dev`, `prod`) and an input `bring_up` of type
   boolean (default `false`).
2. WHEN dispatched with `stage=dev` from a branch or tag whose name matches
   `^[A-Za-z0-9._/@+-]{1,128}$` THEN the system SHALL deploy that ref to dev.
3. WHEN dispatched with `stage=prod` from an existing release tag THEN the
   system SHALL deploy that tag to prod after approval.
4. WHEN the deployed template is the same as the live stack THEN the deploy
   SHALL succeed with no change.
5. THE runbook SHALL state that rollback is code-only. Migrations are
   forward-only under expand/contract (ADR-0025), so a rollback does not revert
   the schema. A rollback SHALL NOT change `MigrationsFingerprint` (see 4.8).
6. IF the dispatched tag does not contain `.github/workflows/deploy.yml` THEN
   the system cannot deploy it. The runbook SHALL state the rollback floor: the
   oldest valid target is the first release tag that contains `deploy.yml`. The
   runbook SHALL name the forward fix: revert on `main`, then push a new patch
   tag.
7. WHILE the legacy subject is accepted, THE runbook "Rollback" section SHALL
   forbid a push of an old tag again. Such a push runs that tag's `ci.yml`
   deploy job and deploys prod with no approval.
8. THE runbook "Rollback" section SHALL allow a dispatch rollback only to a
   release tag with the same `migrations/versions` content as the live release.
   For any other target, THE runbook SHALL name the forward fix.
9. IF `validate` fails on a redeploy or rollback for a cause outside the tag
   (for example a new `pip-audit` advisory) THEN the `deploy` job SHALL NOT
   run. THE runbook SHALL name the forward fix. THE system SHALL NOT provide a
   path that skips `validate`.
10. IF the ref name does not match the pattern in 4.2 THEN the `deploy` job
    SHALL fail before `sam deploy` and name the ref.

### Requirement 5: Least-privilege credentials

**User Story:** As the account owner, I want each deploy to get only the
credentials for its stage, so that a dev run cannot act as prod.

#### Acceptance Criteria

1. THE Validation workflow and the Deploy workflow SHALL set workflow-level
   `permissions: contents: read`.
2. ONLY the `deploy` job SHALL have `id-token: write`.
3. THE system SHALL define one Deploy role for each stage in IaC. Each trust
   policy SHALL accept only its Environment subject.
4. THE `deploy` job SHALL read the role ARN from the Environment variable
   `AWS_DEPLOY_ROLE_ARN`.
5. WHILE the transition is open, THE prod Deploy role SHALL also accept the
   legacy subject `repo:<owner>/<repo>:ref:refs/tags/v*`. WHEN the first
   Environment-gated prod deploy succeeds THEN the operator SHALL remove the
   legacy subject.
6. THE dev Deploy role SHALL have an explicit `Deny` on each prod resource
   pattern in the design table "Dev role Deny patterns". It SHALL also deny
   `rds:*` on resources with the tag `Name` that matches `bdo-prod-*`. A unit
   test SHALL check the list and the tag statement.
7. EACH Deploy role SHALL have an explicit `Deny` on `iam:*` for
   `role/bdo-github-deploy*` and on `cloudformation:*` for the roles stack.
   A unit test SHALL check both statements.
8. EACH Deploy role SHALL set `MaxSessionDuration: 7200`. THE `deploy` job
   SHALL request a 7200-second session. A unit test SHALL check that the
   session is at least as long as the job timeout.
9. THE GitHub OIDC provider SHALL be defined in the same IaC template as the
   Deploy roles.

### Requirement 6: Short-lived dev lifecycle

**User Story:** As a maintainer, I want to create and delete dev through safe
commands, so that dev is created and deleted often with no risk to prod.

#### Acceptance Criteria

1. WHEN dispatched with `bring_up=false` AND the target stack does not exist
   THEN the `deploy` job SHALL fail before `make build`. The message SHALL name
   `bring_up=true`.
2. WHEN the target stack is in a status that cannot accept an update THEN the
   `deploy` job SHALL fail and name the status.
3. IF a required SSM key for the stage is missing THEN the `deploy` job SHALL
   fail before `make build`, list all missing names, and name
   `make seed-config STAGE=<stage>`.
4. WHEN dispatched with `bring_up=true` AND the stack does not exist THEN the
   system SHALL create the stack in two phases, run the DB bootstrap, and run
   `make verify`.
5. IF `bring_up=true` AND the stack exists THEN the `deploy` job SHALL fail
   before `make build` and name the stack.
6. IF `bring_up=true` AND any log group with the prefix
   `/aws/lambda/bdo-<stage>-` exists THEN the `deploy` job SHALL fail before
   `make build`, list the groups, and name the runbook section "Recreating a
   stack from scratch".
7. THE system SHALL provide `make destroy STAGE=dev CONFIRM=bdo-market-dev`. It
   SHALL delete the dev stack and the dev Lambda log groups, print the dev RDS
   snapshot IDs, and keep the dev SSM keys.
8. IF `STAGE` is not `dev`, or `CONFIRM` is not `bdo-market-dev`, THEN
   `make destroy` SHALL exit non-zero and delete nothing.
9. THE Deploy workflow SHALL have no delete or destroy path.
10. IF `bring_up=true` AND an S3 bucket with the prefix `bdo-<stage>-cdn-`
    exists THEN the `deploy` job SHALL fail before `make build`, name the
    bucket, and name the runbook section "Recreating a stack from scratch".
11. IF the stack `bdo-market-dev-break-glass` exists THEN `make destroy` SHALL
    exit non-zero before any delete and name
    `make break-glass-down STAGE=dev`.

### Requirement 7: Shared setup and pinned actions

**User Story:** As a maintainer, I want one setup definition and pinned
third-party actions, so that the workflow setup cannot differ and a moved
upstream tag cannot change CI.

#### Acceptance Criteria

1. THE system SHALL provide `.github/actions/setup` as a composite action that
   installs Python 3.12, uv, the SAM CLI (optional), and runs `uv sync`. THE
   action SHALL install a fixed SAM CLI version (input `sam-version`, default
   at least `1.160.0`), so a later run of the same commit uses the same SAM CLI.
2. EVERY job that needs Python SHALL use the composite action. No workflow
   SHALL call `actions/setup-python` or `astral-sh/setup-uv` directly.
3. EVERY third-party action reference SHALL use a full commit SHA with a
   version comment.
4. Dependabot SHALL propose updates for the pinned actions.
5. THE Validation workflow and the Deploy workflow SHALL set
   `defaults.run.shell: bash`, so that each `run` step uses `pipefail`.

### Requirement 8: Post-deploy checks and serialization

**User Story:** As a maintainer, I want each deploy to confirm the stack serves
and to run one at a time for each stage, so that "deploy passed" means
"stack works".

#### Acceptance Criteria

1. WHEN `sam deploy` succeeds THEN the `deploy` job SHALL run
   `make verify STAGE=<stage>` (ADR-0029). IF it fails THEN the job SHALL fail.
2. THE `deploy` job SHALL use `concurrency` group `deploy-<stage>` with
   `cancel-in-progress: false`.
3. THE `deploy` job SHALL run `make build`, so the verify-layer guard runs
   before `sam deploy`.
4. A unit test SHALL check the `deploy` step order: preflight, `make build`,
   `sam deploy`, then `make verify`.

### Requirement 9: Records and operations documents

**User Story:** As a future maintainer, I want the decisions and the manual
settings written down, so that I can rebuild the setup from the repo.

#### Acceptance Criteria

1. THE system SHALL add ADR-0039 for the Deploy workflow and the per-stage
   roles, and add amendment notes to ADR-0038, ADR-0029, and ADR-0025.
2. THE runbook SHALL contain the GitHub settings procedure (Environments,
   reviewer, deployment policy, tag ruleset, variables, OIDC subject check) as
   `gh api` commands with placeholders.
3. THE runbook SHALL describe the prod release, redeploy, rollback, bring-up,
   dev deploy, and dev teardown through the new paths.
4. THE `deploy-control-plane` spec SHALL carry a "Superseded by
   deploy-workflow" status note.

### Requirement 10: Optional operator helpers

**User Story:** As a maintainer, I want short commands for a release and a dev
CI deploy, so that I do not type the same `git` and `gh` steps each time.

#### Acceptance Criteria

1. WHERE implemented, `make release VERSION=vX.Y.Z` SHALL fail unless the tree
   is clean and `HEAD` equals `origin/main`. Then it SHALL tag and push.
2. WHERE implemented, `make deploy-ci STAGE=dev` SHALL run
   `gh workflow run deploy.yml -f stage=dev` and then `gh run watch`.

## Out of Scope

- A CLI or TUI deploy tool (the `deploy-control-plane` design).
- A dev-to-prod promotion pipeline. Dev is short-lived.
- Schema rollback. Migrations stay forward-only.
- A narrower IAM permission set than today's `PowerUserAccess` plus scoped IAM.
- Full isolation between the dev and prod roles. In one account, the dev role
  can create a `bdo-*` role with more permissions and use it. It can also read
  the prod RDS master secret, which has no `bdo-` name. Resources with no
  `bdo-prod-` name or tag, such as the API Gateway REST API and the CloudFront
  distribution, are not covered. The dev subject accepts each workflow on each
  branch, so push access gives the dev role. Separate AWS accounts remove these
  paths.
- Blocking a direct `sam deploy` with administrator credentials. It stays the
  break-glass path.
- A custom OIDC subject that includes `job_workflow_ref`. ADR-0039 records the
  remaining risk.
