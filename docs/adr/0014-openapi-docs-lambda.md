# ADR-0014: Serve OpenAPI spec and Swagger UI from a docs Lambda

## Status

Accepted

## Context

The REST API's contract is generated as an OpenAPI 3.1 document
(`infra/openapi.yaml`, produced by `scripts/export_openapi.py` from the
Powertools resolvers and drift-checked in CI, ADR-0005 / Phase 5). That spec
was only available inside the repo. Consumers had no way to discover the API —
its routes, parameters, and schemas — without cloning the project, and there
was no interactive way to exercise endpoints against a live stage.

Options considered for publishing the contract:

1. **Static hosting on S3 + CloudFront** — host `openapi.yaml` and a Swagger UI
   bundle as static assets. Adds a bucket, an OAI/OAC, and (for a branded URL) a
   CloudFront distribution — new infrastructure and cost, and a second origin
   to keep in sync with the API's own stage/custom domain.
2. **API Gateway "native" export** — rely on the console/CLI export. Not a
   browsable, always-on endpoint; not discoverable by an external consumer.
3. **Don't ship docs** — keep the spec repo-only. Cheapest, but defeats the
   point of generating a contract.
4. **A small Lambda behind the existing API Gateway** — serve the spec and
   Swagger UI from the same gateway the API already uses.

The API already fronts everything with API Gateway and a shared `bdo-common`
layer (ADR-0003), and supports an optional custom domain (ADR-0013). Adding a
route to that same gateway reuses all of it.

## Decision

Add a dedicated **`docs` Lambda** (`src/functions/docs/`, `bdo-${Stage}-docs`)
wired into the API stack with two **key-less** routes — `Auth.ApiKeyRequired:
false` overrides the API-wide key requirement so the contract is discoverable
without a key:

- `GET /v1/openapi.json` — the OpenAPI document.
- `GET /v1/docs` — Swagger UI.

Implementation details that fall out of this choice:

- **Spec is the single source of truth, bundled at build time.** The makefile
  build (`src/functions/docs/build.py`, mirroring the migrator/layer pattern
  from the cross-platform build fixes) copies the merged `infra/openapi.yaml`
  in alongside the handler. There is no second, hand-maintained copy; CI's
  OpenAPI drift check still guards the one spec.
- **`servers` rewritten to the live base URL.** `openapi.json` sets the spec's
  `servers` to the request's own base URL (reconstructed from `Host` /
  `X-Forwarded-Proto` and the path prefix in front of `/v1`), so Swagger UI
  "Try it out" targets the correct stage or custom domain instead of a
  hard-coded host.
- **Swagger UI assets load from a CDN** (jsDelivr, `swagger-ui-dist@5`) rather
  than being vendored, keeping the function package tiny — the handler ships
  only a small HTML shell.
- **PyYAML is bundled per-function**, not added to the shared layer, because it
  is only needed here (ADR-0003 keeps the shared layer to broadly-used deps).
  The spec is parsed once per execution environment (`lru_cache`).

## Consequences

- (+) The API contract is discoverable and interactively explorable directly
  from the same gateway/host as the API — no extra origin, bucket, or CDN
  distribution to provision or secure.
- (+) Reuses the existing API Gateway, custom domain (ADR-0013), throttling,
  logging, and tracing; the docs URL tracks whatever host the API is served on.
- (+) One spec, generated and drift-checked in CI, serves both the committed
  contract and the live docs — no duplication.
- (+) Key-less discovery routes don't weaken the API: all `/v1/*` data routes
  still require `x-api-key` (ADR-0005); only the contract and UI are public.
- (-) The docs routes are unauthenticated and unmetered (no usage-plan key), so
  they are a small public surface; mitigated by API Gateway throttling and the
  handler doing no backend work.
- (-) Swagger UI depends on a third-party CDN at view time; if jsDelivr is
  unreachable the UI won't render (the raw `openapi.json` still serves).
- (-) Adds a tenth Lambda and a per-function PyYAML dependency to maintain.

## Related

- ADR-0003 — single shared Lambda layer (why PyYAML is bundled per-function).
- ADR-0005 — API key + usage plan (the key requirement these routes opt out of).
- ADR-0013 — optional custom domain (the host the rewritten `servers` follows).
