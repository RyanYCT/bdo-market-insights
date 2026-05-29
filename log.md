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
