# ADR Format

ADRs live in `docs/adr/` in **Michael Nygard format**, one Markdown file per
decision, sequentially numbered: `0001-slug.md`, `0002-slug.md`, … Slugs are
short and hyphenated.

## Numbering

Scan `docs/adr/` for the highest existing number and increment by one,
zero-padded to four digits. The title carries the same number as the filename.

## Template

```md
# ADR-NNNN: {short title of the decision}

## Status

{Proposed | Accepted | Deprecated | Superseded by ADR-MMMM}

## Context

What is the situation, and what problem or force is pushing a decision? State
the options considered as a numbered list when there was a real choice; capture
constraints that are not visible in the code.

## Decision

What was decided, stated in the active voice ("Store the checksum in the items
table"). Include the specifics a future implementer needs.

## Consequences

The results of the decision, good and bad. Use `(+)` for benefits and `(−)`
for costs, one bullet each:

- (+) A positive consequence.
- (−) A cost or trade-off accepted.

## Notes

Optional. Cross-links to related ADRs (`ADR-NNNN`), the owning spec
(`.kiro/specs/<feature>/`), supersession relationships, and any one-time
migration note that must not live in permanent code.
```

## Conventions

- **`## Status`** always present. Use `Proposed` for design-only ADRs that are
  gated on an open decision; qualify it ("Proposed — design only; gated on …")
  when useful. Move to `Accepted` once shipped. Record supersession both ways:
  the new ADR gets `Superseded by` on the old one, and the old number in the
  new one's `## Notes`.
- **`## Consequences`** uses the `(+)` / `(−)` marker convention already in the
  repo's ADRs — do not drop it.
- **Cross-link, don't restate.** Reference other ADRs, the spec, and the
  `domain-model.md` by pointer; never copy their content in.
- **Keep it as short as the decision allows.** An ADR earns its length from the
  context and trade-off, not from filling out sections. Omit `## Notes` when
  there is nothing to link.
- **Verify every factual claim against the code before asserting it.** An ADR
  that describes the system wrongly is worse than no ADR, because it is
  trusted. Check the claim's source — that the validator really makes the state
  unconstructable, that the layer build really globs that one directory, that
  no long-lived key exists *anywhere*, that the `scripts/` directory you say is
  forgone is not the one CI invokes. Quote the mechanism you verified against
  (file, symbol, or config key) so the next reader can re-check it, and soften
  a claim you could not verify rather than rounding it up.
- **Record the rejected alternatives, and why.** Where the design arrived at
  its shape by elimination, name the iterations that were discarded on the way
  — they are the ADR's most valuable content, because they pre-empt "why
  didn't you just …". Keep each rejected option's genuine appeal visible;
  a strawman teaches the next reader nothing.
- **Resolve forward pointers and annotate the owning spec.** A spec that
  flagged the decision carries a placeholder — `ADR (task 7.2)`, "(flagged for
  an ADR)". Writing the ADR is not done until that pointer is replaced with
  the real `ADR-NNNN` and the entry in the spec's `design.md` "Planned ADRs"
  list is annotated `**ADR-NNNN (accepted)**`. Annotate `design.md`, not
  `requirements.md`, which references ADR numbers inline only. A placeholder
  left behind tells the next reader the decision may never have been made.

## When to offer an ADR

All three must be true, or skip it:

1. **Hard to reverse** — the cost of changing your mind later is meaningful.
2. **Surprising without context** — a future reader will look at the code and
   wonder "why on earth did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you
   picked one for specific reasons.

### What qualifies

- **Architectural shape** — nested-stack topology, single-AZ workload, the
  shared Lambda layer.
- **Technology choices that carry lock-in** — SAM over CDK, IAM database auth,
  the pricing-model registry.
- **Boundary and scope decisions** — what a stack owns, what stays out of the
  root template. The explicit no-s are as valuable as the yes-s.
- **Deliberate deviations from the obvious path** — anything a reasonable
  reader would assume the opposite of, so the next engineer does not "fix" a
  deliberate choice.
- **Constraints not visible in the code** — cost ceilings, the arsha.io usage
  plan, no-NAT networking.
