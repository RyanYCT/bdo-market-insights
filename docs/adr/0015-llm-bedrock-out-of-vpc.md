# ADR-0015: LLM summaries via Amazon Bedrock, summarised outside the VPC

## Status

Accepted

## Context

The LLM market-insights feature (`.kiro/specs/llm-insights/`) generates daily
and weekly natural-language summaries. Two questions need deciding: **which LLM
provider**, and **where the model call runs**, given the platform's two hard
constraints — the no-NAT VPC (ADR-0006) and the ≤ US$15/month cost target
(NFR-13 of the v3 spec).

Provider options:

1. **Amazon Bedrock** — IAM-authenticated, in-account, no API key to store or
   rotate, billed per token, models available in `us-east-1`.
2. **External API** (OpenAI / Anthropic-direct) — requires an API key in
   Secrets Manager and outbound internet from wherever the call runs.

Placement options (the data lives in RDS, reachable only in-VPC):

- **A. Single in-VPC Lambda** that reads RDS and calls the model. With the
  no-NAT VPC, reaching *any* model endpoint from in-VPC needs a **Bedrock VPC
  interface endpoint** (PrivateLink, ~US$7–8/month) or a NAT gateway
  (~US$32/month) — both meaningful against the budget.
- **B. Split across the VPC boundary** with Step Functions (ADR-0004): an
  in-VPC step computes the digest from RDS and an **out-of-VPC** step calls the
  model. Lambdas not attached to a VPC have AWS-managed egress, so they reach
  Bedrock (and the Discord webhook) with no NAT and no VPC endpoint.

An external provider from in-VPC would *also* need NAT/endpoint egress, so it
compounds both the cost and the secret-management concerns.

## Decision

Use **Amazon Bedrock**, and run summarisation **outside the VPC** via a Step
Functions split (placement B):

```
ComputeDigest (in-VPC, RDS) -> Summarize (out-of-VPC, Bedrock)
  -> StoreSummary (in-VPC, RDS) -> SNS:Publish (native)
```

- The chosen provider is a **first-class Bedrock** one — **Amazon Nova** or
  **Anthropic Claude**. `summarize` uses the model-agnostic **Converse API**, so
  switching between them is a `BedrockModelId` parameter change with no request
  reshaping. (Google/Gemini's mainstream home is Vertex AI — an external API
  needing a key and its own integration — so it is **out of scope** for this
  decision; revisiting it would amend this ADR.)
- `summarize` runs as a **Lambda initially** for testable guardrails (prompt
  assembly, schema validation, deterministic fallback — ADR-0016). Once the
  prompt and output schema are stable, it collapses into a **native
  `bedrock:invokeModel` Step Functions task**; the swap is localised because
  prompt-building (`insights/prompt.py`) and parsing/fallback
  (`insights/narrative.py`) live in the shared layer and are reused unchanged.
- Bedrock access is IAM, least-privilege to the specific model ARN; no static
  credentials anywhere — consistent with the passwordless posture of ADR-0008.
- The model id is a SAM parameter (`BedrockModelId`), defaulting to a low-cost
  model; at ~2 small calls/day with top-N-bounded prompts, token spend is
  cents/month.
- The out-of-VPC `summarize` and `discordNotifier` Lambdas reach their external
  endpoints over AWS-managed egress — **no NAT, no VPC interface endpoint**, so
  networking adds US$0.

## Consequences

- (+) Stays within budget: no NAT (~$32/mo) and no Bedrock PrivateLink endpoint
  (~$7–8/mo); Bedrock tokens are negligible at this volume.
- (+) No secret to manage for the LLM — IAM only; data stays in-account.
- (+) Reuses the existing Step Functions + mixed-VPC-placement patterns
  (ADR-0004, ADR-0006); the in/out-of-VPC boundary is explicit per step.
- (+) Provider is swappable: `summarize` is the only Bedrock-aware unit.
- (-) More moving parts than a single Lambda (a multi-state machine crossing the
  VPC boundary), and the digest must pass through Step Functions state
  (256 KB limit — fine for a top-N digest).
- (-) Bedrock model **enablement** is a one-time per-account/region prerequisite
  (documented in the runbook).
- (-) If a fully-private data path to the model were ever mandated, this would
  need revisiting (placement A with a PrivateLink endpoint, accepting its cost).

## Related

- ADR-0004 — Step Functions for orchestration (the split mechanism).
- ADR-0006 — no-NAT mixed VPC placement (the constraint that forces the split).
- ADR-0008 — IAM auth / no static secrets (the posture Bedrock IAM continues).
- ADR-0016 — how the model output is constrained and validated.
