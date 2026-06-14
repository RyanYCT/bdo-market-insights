# ADR-0016: Deterministic digest; the LLM narrates, never computes

## Status

Accepted

## Context

The market-insights feature puts an LLM in front of financial-style data.
The failure mode that matters most is **hallucinated numbers** — a summary that
states a price move, enhancement cost, or anomaly that the data does not
support. For a market-data product whose entire value is trustworthy figures,
that is unacceptable, and it echoes a documented past failure (the
`rewrite-project` rewrite that "replaced BDO-specific business logic with
generic statistics" — see `.kiro/steering/tech.md` Forbidden list).

The platform already computes the authoritative numbers deterministically in
`bdo_common.analytics` (volatility, liquidity, z-score anomaly) and
`bdo_common.pricing` (per-tier expected enhancement cost). The open question is
how much to trust the LLM with.

## Decision

**Deterministic code computes; the LLM only narrates.**

1. A pure digest builder (`bdo_common.insights.build_digest`) produces a
   structured `MarketDigest` — top-N movers per category, anomalies, volatility/
   liquidity, and (for accessories) enhancement-cost movement — entirely from
   `bdo_common` analytics/pricing over `market_daily`. This object is the single
   source of numeric truth.
2. The LLM receives the digest as the *complete set of facts* and is instructed
   to write a concise, trader-style English summary **using only those figures**,
   inventing or recomputing nothing, and to return **only** a fixed JSON schema
   (`Narrative`: headline, per-category bullets, overall note).
3. The model output is parsed and validated against the `Narrative` Pydantic
   schema. On any parse/validation failure — or a Bedrock error after retries —
   a **deterministic template renderer** produces the narrative from the same
   digest, so a summary is always produced.
4. The API returns the structured `digest` **alongside** the prose. Consumers
   can always display authoritative numbers regardless of the narrative, and the
   stored row keeps both.

## Consequences

- (+) No hallucinated figure can reach a consumer as authoritative: numbers come
  from the digest, which the API always returns; the prose is commentary over
  those same numbers.
- (+) The feature degrades gracefully and stays non-critical (NFR-3): the LLM is
  a presentation layer, not a dependency for producing a summary.
- (+) Provider/model swaps and prompt changes can't alter the figures, only the
  wording — and the guardrails (schema + fallback) are unit-testable with a
  stubbed client.
- (+) Reuses the existing domain math rather than re-deriving it, preserving the
  "BDO-specific logic, not generic stats" principle.
- (-) The narrative may occasionally read as templated when the fallback fires;
  acceptable, since correctness outranks fluency here.
- (-) Strong grounding constrains how "insightful" the prose can be; richer
  interpretation (e.g. patch-note-driven speculation) is deferred to a future,
  explicitly-scoped context block rather than left to the model to imagine.

## Related

- ADR-0012 — pricing-model registry (source of enhancement-cost figures; the
  category registry mirrors its shape).
- ADR-0015 — provider and placement for the narration step.
- `.kiro/specs/v3/domain-model.md` — normative analytics/pricing definitions.
