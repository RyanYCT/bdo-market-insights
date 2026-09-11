---
name: domain-modeling
description: Build and sharpen this repo's domain model. Use when discussing BDO market terminology, editing the Language glossary in .kiro/steering/product.md, or recording an architectural decision as an ADR under docs/adr/.
---

# Domain Modeling

Actively build and sharpen the project's domain model as you design. This is
the *active* discipline: challenging terms, inventing edge-case scenarios, and
writing the glossary and decisions down the moment they crystallise. (Merely
*reading* the glossary for vocabulary is not this skill: that is a one-line
habit any skill can do. This skill is for when you are changing the model, not
just consuming it.)

## Where things live in this repo

This repo has no `CONTEXT.md`. The domain is split across two homes, and this
skill only ever writes to the first:

- **Vocabulary → the `## Language` section of `.kiro/steering/product.md`.**
  Canonical term + a one or two sentence meaning + an `_Avoid_` list of
  rejected synonyms. `product.md` is `inclusion: always`, so the glossary is
  auto-loaded every session.
- **Quantitative model → the active spec's `domain-model.md`** (e.g.
  `.kiro/specs/v3/domain-model.md`): formulas, probabilities, and worked
  numbers that the unit tests assert. This is *not* this skill's file.

**Boundary rule.** Terms go in `## Language`; numbers, formulas, and worked
examples go in the spec's `domain-model.md`. Never copy a formula or a worked
number into the glossary — reference the concept by name. The glossary defines
what a word *means*, never how a value is *computed*.

- **ADRs → `docs/adr/NNNN-slug.md`** (Michael Nygard format). Use the template
  in [references/ADR-FORMAT.md](references/ADR-FORMAT.md).

## During the session

### Challenge against the glossary
When the user uses a term that conflicts with the existing `## Language`
entries, call it out immediately. "The glossary defines `base_price` as the
canonical pricing basis, but you seem to mean `last_sold_price`. Which is it?"

### Sharpen fuzzy language
When the user uses a vague or overloaded term, propose a precise canonical one.
"You are saying 'price': do you mean `base_price` or `last_sold_price`? Those
are different things here."

### Discuss concrete scenarios
When domain relationships are in play, stress-test them with specific scenarios
(a one-sided order book, a `clean` copy destroyed on a failed attempt, a region
with no snapshots yet). Force the boundaries between concepts to be precise.

### Cross-reference with code
When the user states how something works, check whether the code and the
spec's `domain-model.md` agree. If you find a contradiction, surface it rather
than paper over it.

### Update the glossary inline
When a term is resolved, edit the `## Language` section of
`.kiro/steering/product.md` right there — do not batch them up. Keep entries
tight (one or two sentences, what it IS not what it does), opinionated (pick
one canonical word, list the rest under `_Avoid_`), and free of implementation
detail. Only add terms specific to this project's domain, not general
programming concepts.

### Offer ADRs sparingly
Only offer to create an ADR when all three are true:

1. **Hard to reverse** — the cost of changing your mind later is meaningful.
2. **Surprising without context** — a future reader will wonder "why this way?"
3. **The result of a real trade-off** — there were genuine alternatives.

If any of the three is missing, skip it. Use the format in
[references/ADR-FORMAT.md](references/ADR-FORMAT.md).

## Repo integration

Keep these light; `AGENTS.md` is the full workflow reference.

- **Spec first.** Design decisions belong in the active spec under
  `.kiro/specs/<feature>/{requirements,design,tasks}.md` *before* code, never
  only in code. Link any new ADR from the relevant spec.
- **Spec cap.** Each spec file is capped at ~150 lines; split or trim rather
  than let one balloon.
- **Session log.** If the session was non-trivial, append an entry to `log.md`
  per its template at the end.
