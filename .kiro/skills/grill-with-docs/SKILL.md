---
name: grill-with-docs
description: A relentless interview to sharpen a plan or design that also records outcomes as docs (glossary terms and ADRs) as the conversation goes. Use when the user wants to stress-test their thinking on a change and capture the decisions, or uses a 'grill with docs' trigger.
---

# Grill With Docs

Interview the user relentlessly until you reach a shared understanding, and
record what crystallises **as you go** — sharpened terms into the glossary,
hard decisions into ADRs. It is the grilling loop plus the `domain-modeling`
discipline running together.

## The interview loop

Map the subject as a **design tree**: every decision branches into the
decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose
prerequisites are already settled: the questions you can ask *now* without
guessing at answers you have not heard yet. Ask the whole frontier in one
round; number each question and give your recommended answer. Then wait for the
user's answers before the next round.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, may be multiple paragraphs,
including multiple-choice options>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree: settled decisions push the
frontier outward and unblock questions that depended on them. Recompute the
frontier and ask the next round. A question whose answer depends on another
question still open in this round belongs to a *later* round, not this one.

Finding *facts* is your job, never the user's. When a frontier question needs a
fact from the environment (filesystem, tools, the code, the spec), go find it
yourself; do not ask the user for anything you could look up. Do not block on
it: only the questions downstream of that fact wait; ask the rest of the
frontier now. The *decisions* are the user's: put each to them and wait.

The session is done when the frontier is empty: every branch visited, nothing
left silently assumed. Do not act on the plan until the user confirms you have
reached a shared understanding.

## Record as you go

Apply the `domain-modeling` skill throughout — do not batch documentation to
the end:

- **When a term is coined or sharpened**, write it into the `## Language`
  section of `.kiro/steering/product.md` right then (canonical term, one or two
  sentence meaning, `_Avoid_` synonyms; no formulas or numbers).
- **When a decision is hard to reverse, surprising without context, and the
  result of a real trade-off**, offer an ADR under `docs/adr/NNNN-slug.md` in
  the Michael Nygard format (see the `domain-modeling` skill's
  `references/ADR-FORMAT.md`). If any of the three is missing, skip it.
- **Keep the boundary**: terms in the glossary, quantitative model in the
  active spec's `domain-model.md`. Never copy a formula into the glossary.

See the `domain-modeling` skill for the full doc-writing discipline and the ADR
template; this skill drives the interview and calls that discipline at each
point a term or decision settles.
