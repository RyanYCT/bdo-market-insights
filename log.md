# Session Log

Append-only chronological record of significant agent-driven work on
this repository. Newer entries at the bottom. Never edit historical
entries; correct mistakes by appending a follow-up entry.

One entry per Kiro session. Skip trivial sessions (single typo fix,
pure code review, exploration that didn't change anything).

Each entry uses the template below; aim for ≤ 200 words.

---

## Template

```
## YYYY-MM-DD — <session title>

**Agent:** <name>
**Mode:** <Spec | Vibe | Autonomous>
**Branch:** <branch name>
**Phase:** <phase from .kiro/specs/v3/tasks.md>
**Commits:** <SHA list or PR link>

### Done
- ...

### Decisions
- ... → ADR-NNNN (or "no ADR — local choice")

### Deferred / open questions
- ...
```

---

## 2026-05-29 — v3 design and initial specs

**Agent:** Kiro
**Mode:** Spec (started in Vibe; transitioned once we began writing `.kiro/specs/v3/`)
**Branch:** `redesign-v3` (orphan, created this session)
**Phase:** 0 — Branch & specs
**Commits:** `d2ad0fe`, `6d6bc38`, `e00386b`, `096ce4a`, `0e49f11`, plus this entry

### Done
- Audited existing `main` and `rewrite-project`; documented the
  rewrite's failure modes.
- Designed v3 from scratch: 8 Lambdas, Step Functions ETL, 4-table
  Postgres schema (`item`, `item_sid`, `market_snapshot`,
  `market_daily`), region-aware PK, DynamoDB-only item registry with
  lazy Postgres population, EICE bastion for pgAdmin, single-AZ
  workload.
- Wrote `.kiro/specs/v3/{requirements,design,tasks}.md`, `AGENTS.md`,
  three `.kiro/steering/*.md` files, and this `log.md`.
- Created the orphan `redesign-v3` branch.

### Decisions
- ADR-0001 SAM over CDK
- ADR-0002 RDS Proxy opt-in; module-global psycopg by default
- ADR-0003 Single shared Lambda Layer `bdo-common`
- ADR-0004 Step Functions ETL with per-stage retry
- ADR-0005 API key + usage plan; Cognito deferred
- ADR-0006 Mixed VPC placement; no NAT
- ADR-0007 AWS Lambda Powertools over hand-rolled utilities
- ADR-0008 IAM database auth for Lambdas
- ADR-0009 EICE bastion for human DBA access
- ADR-0010 Lazy `item` table population by ETL; Streams sync deferred
- ADR-0011 Single-AZ workload
- No SKILL.md / `.kiro/skills/` for now — defer until lived experience
  identifies a recurring procedure (no ADR; AGENTS.md "Phase
  completion check" enforces revisiting this each phase)

### Deferred / open questions
- Real BDO enhancement probabilities for accessories — placeholder
  values to land in `rates.json`; user to provide.
- Phase 7 cutover: archive `rewrite-project`, tag `archive/main-v1`,
  force-replace `main` — explicit approval required at that point.
- Phase 2: DynamoDB Streams-driven item sync remains deferred per
  ADR-0010.

---

## 2026-05-30 — Phase 1 scaffolding + Phase 2 infrastructure

**Agent:** Kiro
**Mode:** Autonomous (with Vibe acceptance reviews between phases)
**Branch:** `redesign-v3`
**Phase:** 1 — Project scaffolding; 2 — Network, data, bastion infrastructure
**Commits:** `2add18e`..`59663fb` (Phase 1 + cleanup), `a920446`..`84a2477` (Phase 2 + cleanup)

### Done
- Phase 1: `pyproject.toml` (uv), `Makefile`, single CI workflow,
  SAM `template.yaml` skeleton (4 params), `samconfig.toml`, 11 ADRs,
  runbook/SLO/architecture docs, license/gitignore/README.
- Phase 1 acceptance review → fixed: OIDC `id-token` perm, pip-audit +
  bandit jobs, dropped premature `sam validate`, dead Conditions,
  boto3→dev-deps, `BdoRegion` AllowedValues (corrected to the 13
  arsha.io regions).
- Phase 2: `infra/{network,data,bastion}.yaml`, 3 stub stacks, Alembic
  + `0001_initial` (4 tables) + `0002_bootstrap_roles`,
  `scripts/seed_items.py`, restored `sam validate`.
- Phase 2 acceptance review → fixed 4 blockers + majors: W3005/W3691
  (sam validate green), bastion port-22 ingress, removed unreachable
  CI migration step (bastion-tunnel flow instead), created the
  `lambda_rds_user`/`dba` roles, seed camelCase + sparse-GSI fixes.

### Decisions
- Migrations run via the bastion tunnel (`make migrate`), not CI —
  private RDS is unreachable from a GitHub runner. In-VPC migrator
  Lambda deferred to Phase 4. (no ADR — local choice, documented in
  runbook + tasks.md)
- RDS engine pinned to Postgres `16.8` (bare major `16` is deprecated).
- Forward-declared params (`UseRdsProxy`, stub-stack interfaces) kept
  with scoped cfn-lint suppressions rather than removed.

### Deferred / open questions
- `db-tunnel-up` resolves the RDS endpoint from a nested-stack output;
  if fragile in practice, promote `RdsEndpoint` to a root-stack output.
- Real BDO enhancement probabilities still needed for `rates.json`
  (Phase 3).
- Phase 3 `pricing.py`/`analytics.py` domain math to be designed before
  coding.


---

## 2026-05-31 — Phase 3 domain math: pricing + analytics

**Agent:** Kiro
**Mode:** Vibe
**Branch:** `redesign-v3`
**Phase:** 3 — Shared layer (`bdo-common`)
**Commits:** `d1fe1b0`, `e6cb9da`, `a02ed86`, `1de2cec`

### Done
- Recovered the v3 domain-model lock parked by a prior bash-outage
  session: re-applied and committed the six files (`domain-model.md`,
  `rates.json`, ADR-0012, `seed_items.py`, `design.md`, `tasks.md`).
- Built the pure domain math: `pricing.py` (`accessory_v1` model +
  `model_id`->factory registry, ADR-0012) and `analytics.py`
  (volatility/CV, liquidity, z-score anomaly). Unit tests assert the
  `domain-model.md` worked numbers. ruff + mypy(strict) + 32 pytest green.

### Decisions
- Kept direct-commit-to-`redesign-v3` flow (Option A); reworded
  `tasks.md` "each box is one PR" to match — no ADR (local choice;
  aligns with AGENTS.md).
- Probability at/above the soft-cap breakpoint uses `soft_cap_rate`
  (`2->3@44` = 0.40, not 0.405) per domain-model prose — no ADR.
- Cumulative `clean -> sid:N` cost uses a self-build recursion (no
  verified spec number) — no ADR; flagged for confirmation.

### Deferred / open questions
- Phase 3 remaining: `arsha_client` + normalizer, `models.py`, `db.py`,
  `dynamo.py`, `repositories.py`, `config.py`, normalizer/repo tests.
- Confirm the two interpretation calls above (breakpoint; cumulative).
- `rates.json` curves/cron counts still placeholders pending live-TW
  verification.
