# ADR-0017: Hybrid narration — deterministic bullets, LLM writes only headline + overall

## Status

Accepted. Refines [ADR-0016](0016-deterministic-digest-llm-narration.md): the
LLM's output is narrowed from the full `Narrative` to a `NarrativeSummary`
(headline + overall); the per-category bullets are rendered deterministically.

## Context

ADR-0016 established the principle "deterministic code computes; the LLM only
narrates," with the model receiving the digest and returning the full
`Narrative` (headline, per-category bullets, overall) grounded in the digest's
figures, plus a deterministic fallback.

Evaluating the live narration on the dev stack (model `us.amazon.nova-lite-v1:0`)
showed the model still mis-stated figures it had been handed verbatim:

- mapped a per-tier `enhancement_cost_change` onto the wrong tier;
- a 1000x magnitude slip (rendered ~109m silver as "109k");
- a sign flip ("~2.5% cheaper" for a +2.5% rise);
- and an invented structural line ("tiers 1-2 of buffs held flat") for a
  category that has no enhancement tiers.

Tightening the prompt — precomputing an authoritative, correctly-labelled list
of enhancement-cost movers and instructing the model to copy it verbatim —
reduced the errors but did not eliminate them. Since ADR-0016's entire premise
is that the figures must be trustworthy, "mostly correct prose" is not enough: a
small model cannot be relied on to relay exact numbers even when given them.

## Decision

**Split narration by reliability. The model writes only the qualitative parts;
every figure is rendered deterministically and never passes through the model.**

1. The LLM is asked for only `{headline, overall}` (a `NarrativeSummary`) — the
   framing it is reliable at — anchored on the digest's precomputed `stats`
   (breadth, top mover/loser, most volatile/traded).
2. The per-category bullets — every exact price, percentage, enhancement-cost
   move, and anomaly flag — are produced by the deterministic `render_narrative`
   over the digest.
3. `insights_summarize` merges the model's headline/overall with the
   deterministic bullets into the stored `Narrative`. On an empty digest, or any
   Bedrock/parse/validation failure, it emits no model text and the full
   deterministic narrative (the ADR-0016 fallback) is used.
4. Supporting digest signals (`trend`, `anomaly`, breadth, top movers,
   enhancement-cost movers) are precomputed in `build_digest`/`stats`, so the
   headline/overall have grounded material without the model deriving anything.

## Consequences

- (+) Exact figures can no longer be corrupted by the model — they never pass
  through it, so the magnitude/sign/tier/fabrication errors above are
  structurally impossible.
- (+) Keeps the LLM's value (a natural headline and an analyst-style overall)
  where it is reliable.
- (+) The empty-digest path skips Bedrock entirely: no facts to narrate means no
  hallucination risk and no spend.
- (-) Bullets read more mechanically than free-form prose — acceptable, since
  correctness outranks fluency (consistent with ADR-0016).
- (-) The "richer interpretation" ADR-0016 deferred is partially recovered in the
  LLM `overall` (qualitative takeaways), but stays bounded: it must not assert
  any figure beyond the digest.

## Related

- ADR-0016 — deterministic digest; the LLM narrates, never computes. This ADR
  narrows the model's role to the headline and overall.
- ADR-0015 — provider and placement for the narration step.
