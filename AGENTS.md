# AGENTS.md

Operating manual for AI coding agents working on this repository.
Read this **before** making changes. Updates require human approval.

## Project context

`bdo-market-insights` — serverless ETL + analytics for Black Desert
Online market data on AWS Lambda. v3 is **live on `main`** (the
`redesign-v3` cutover completed 2026-06-09; all phases done). The v3
design lives in `.kiro/specs/v3/` and remains the reference for how the
system is built; **new work should add its own spec** under
`.kiro/specs/<feature>/`.

History: this repo had a previous rewrite (`rewrite-project`, now
archived as `archive/rewrite-project`) that was over-engineered, broke
the core BDO domain logic, and was shelved. v3's guiding principle is
**"boring + correct"** — learn from those failures (see "Anti-patterns"
below).

## Where to look first

| File | Purpose |
|---|---|
| `.kiro/steering/product.md` | What this project is; BDO domain primer |
| `.kiro/steering/tech.md` | Locked tech stack; forbidden patterns |
| `.kiro/steering/structure.md` | Repository layout |
| `.kiro/specs/v3/requirements.md` | Active functional + non-functional reqs |
| `.kiro/specs/v3/design.md` | Architecture, schema, ADR pointers |
| `.kiro/specs/v3/tasks.md` | Phased task list with checkboxes |
| `docs/adr/` | One markdown per architectural decision |
| `log.md` | Append-only session log |

Steering files use `inclusion: always` so Kiro auto-loads them every
session. Other agents (Claude, Cursor, …) read this file plus the
steering files manually.

## Workflow rules

- Work on feature branches off `main` (v3 is now `main`). Never push to
  `main` directly; open a PR — branch protection requires the CI checks
  to pass first.
- For spec-driven work, one commit per task in the active feature's
  `.kiro/specs/<feature>/tasks.md`; tick the checkbox in the same commit.
  (The v3 build is complete; its `tasks.md` is historical.)
- Conventional Commits: `<type>(<scope>): <imperative subject>`. Body
  explains *why*, not *what*.
- Push via `github_push_to_remote`, never raw `git push`.
- At session end, append an entry to `log.md` per its template if the
  session was non-trivial.

## Specs and ADRs

- The active feature spec under `.kiro/specs/` is the source of truth
  (`.kiro/specs/v3/` documents the now-shipped v3 build). Modify specs
  **before** code; do not introduce design decisions only in code.
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

Kiro offers three working modes the owner uses deliberately:

- **Spec** mode — structured iteration on
  `.kiro/specs/<feature>/{requirements,design,tasks}.md`. Used for
  new features and re-architectures. Specs come *before* code; do
  not introduce design decisions only in code.
- **Vibe** mode — collaborative chat for design discussion or
  targeted edits.
- **Autonomous** mode — Kiro executes a well-defined task with
  minimal interruption.

Behavioral rules across modes:

- In **Spec** and **Vibe**, surface trade-offs explicitly with options
  (A/B/C) and a recommendation. Do not pretend certainty.
- In **Autonomous**, prefer "boring + correct". Flag scope creep
  proactively; do not silently expand a task.
- Destructive operations (force push, branch rename, force-replacing
  `main`, deleting AWS resources) require **explicit approval each
  time**, in any mode.
- When inheriting from a previous session, read `.kiro/steering/`,
  the active spec under `.kiro/specs/`, the latest entries in
  `log.md`, and the current `tasks.md` checkbox state before acting.

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

## Phase completion check

When closing out a phase (all `tasks.md` checkboxes in the phase
ticked), ask whether any procedure recurred enough across the phase
tasks to be worth a workspace-level skill in `.kiro/skills/<name>/`.
If yes, write the skill before starting the next phase. The intent
is to prevent premature skill files while making sure earned ones
get captured.

## Tools available in this sandbox

| Need | Tool |
|---|---|
| Read/write workspace files | `read_files`, `fs_write`, `str_replace` |
| Search | `grep_search`, `file_search` |
| Run commands | `execute_bash` (use `cwd`; never `cd`) |
| Push branches | `github_push_to_remote` (never raw `git push`) |
| Create PRs | `github_create_pull_request` |
| Investigate unfamiliar code | `invoke_sub_agent` with `context-gatherer` |
