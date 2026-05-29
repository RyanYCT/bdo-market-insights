# AGENTS.md

Operating manual for AI coding agents working on this repository.
Read this **before** making changes. Updates require human approval.

## Project context

`bdo-market-insights` — serverless ETL + analytics for Black Desert
Online market data on AWS Lambda. The active design lives in
`.kiro/specs/v3/`. The current working branch is `redesign-v3`; `main`
will be force-replaced from it on cutover.

History: this repo had a previous rewrite (`rewrite-project`, soon to be
archived) that was over-engineered, broke the core BDO domain logic,
and was shelved. v3's guiding principle is **"boring + correct"** —
learn from those failures (see "Anti-patterns" below).

## Workflow rules

- Work on `redesign-v3` (or feature branches off it). Never push to
  `main` directly.
- One commit per task in `.kiro/specs/v3/tasks.md`. Tick the checkbox in
  the same commit.
- Conventional Commits: `<type>(<scope>): <imperative subject>`. Body
  explains *why*, not *what*.
- Push via `github_push_to_remote`, never raw `git push`.
- Destructive operations (force push, branch rename, force-replacing
  `main`, deleting AWS resources) require **explicit approval each
  time**, even if previously discussed.
- At session end, append an entry to `log.md` per its template if the
  session was non-trivial.

## Specs and ADRs

- `.kiro/specs/v3/` is the source of truth. Modify specs **before**
  code; do not introduce design decisions only in code.
- One Markdown ADR per non-obvious architectural decision, in
  `docs/adr/`, Michael Nygard format. Link from the relevant spec/code.
- Hard cap: each spec file ≤ ~150 lines. If it grows past that, split
  it or trim it. Documentation explosion sank the previous rewrite.

## Code

- Python 3.12 only. Type-annotate everything. `ruff` and `mypy` must
  pass before committing.
- Pydantic v2 at every I/O boundary (API request/response, arsha.io
  payloads, DynamoDB items, DB rows).
- AWS Lambda Powertools is mandatory for: structured logging, tracing,
  metrics, parameters/secrets caching, idempotency. **Do not hand-roll**
  retry, circuit breaker, rate limiter, or correlation propagation.
- DB access: `psycopg[binary]` v3, parameterized SQL only, repository
  pattern in `bdo_common/repositories.py`. No ORM.
- Exactly **one** CI workflow (`.github/workflows/ci.yml`).
- Exactly **one** root SAM `template.yaml` with nested stacks.

## Testing

- `pytest` + `moto` for AWS, ephemeral Postgres in CI for DB tests.
- No coverage gate. Coverage is a signal, not a wall.
- Property-based tests only when there is a clear invariant (the arsha
  response normalizer is one such place).

## Operations

- All AWS resources in IaC (SAM template). No console-clickers.
- Single-AZ workload by design (ADR-0011). Any Multi-AZ resource needs
  a new ADR.
- IAM database auth for Lambdas; never bypass it. The `dba` Postgres
  role exists only for human bastion access.
- No NAT (ADR-0006). If a future feature needs internet from a
  VPC-attached Lambda, write an ADR before adding NAT.

## Working with the human

- Owner uses **Vibe** mode for design, **Autonomous** mode for
  execution.
- In design discussion, surface trade-offs explicitly with options
  (A/B/C) and a recommendation. Do not pretend certainty.
- In execution, prefer "boring + correct". Flag scope creep
  proactively; do not silently expand a task.
- When inheriting from a previous session, read the latest entries in
  `log.md` and the current `tasks.md` checkbox state before acting.

## Anti-patterns we have already paid for

The previous rewrite failed by accumulating these. Do not reintroduce
them.

- Multiple CI workflows that drift out of sync.
- Hand-rolled circuit breakers, retry decorators, rate limiters,
  structured loggers.
- An ops folder full of bespoke shell scripts where SAM/Make would do.
- Per-Lambda `.env.example` files duplicating central config.
- Documentation files that exist only to index other documentation
  files.
- Property tests for completeness rather than for an invariant.
- A coverage gate the test suite cannot meet.
- Replacing domain-specific business logic with generic statistics.
- Big-bang rewrites in a single PR.

## Definition of done for a phase task

1. `ruff`, `mypy`, `pytest` pass locally and in CI.
2. The relevant `tasks.md` checkbox is ticked in the same commit.
3. The commit message follows Conventional Commits.
4. If a non-obvious decision was made, an ADR exists.
5. The change is small enough that a human can review in one sitting.

## Tools available in this sandbox

| Need | Tool |
|---|---|
| Read/write workspace files | `read_files`, `fs_write`, `str_replace` |
| Search | `grep_search`, `file_search` |
| Run commands | `execute_bash` (use `cwd`; never `cd`) |
| Push branches | `github_push_to_remote` (never raw `git push`) |
| Create PRs | `github_create_pull_request` |
| Investigate unfamiliar code | `invoke_sub_agent` with `context-gatherer` |
