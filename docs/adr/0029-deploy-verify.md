# ADR-0029: Post-deploy verification (`make verify`)

## Status

Accepted (implements ADR-0024 item 5; completes the deploy-convergence work)

## Context

ADR-0024 set out for `make deploy` to converge an environment *and* confirm it
is actually serving, so "deploy succeeded" means more than "CloudFormation
returned OK". Migrations (ADR-0025) and the data bootstrap (ADR-0028) now run
inside the deploy; the missing piece is the confirmation step.

Three facts shape what a smoke test can check here:

- **No `/health` route.** The API is `x-api-key`-gated except `/v1/openapi.json`
  and `/v1/docs`, which are public.
- **A usable API key is conditional.** `DemoApiKey` exists only when
  `EnableDemoKey=true` (prod), not on dev by default — so key-authenticated API
  checks can't run uniformly across stages.
- **Market data isn't present on a fresh environment.** The bootstrap seeds the
  catalog / tracked set / icons (DynamoDB); RDS market rows come from the hourly
  ETL, not the bootstrap. And the bootstrap is asynchronous (ADR-0028) and a
  fresh catalog is tens of thousands of items, so it runs for many minutes.

## Decision

Add `scripts/verify.py` (`make verify STAGE=<env>`), a **key-free** smoke test
that is uniform across dev and prod:

1. **Liveness** — `GET {ApiUrl}/v1/openapi.json` returns 200. That route is
   public, so no key is needed; a 200 proves API Gateway + a Lambda serve.
2. **RDS-backed serving** — invoke the admin-query Lambda (ADR-0026) with
   `select 1`; success proves the in-VPC Postgres path (IAM auth) works. Key-free
   and reuses an existing component instead of requiring an API key.
3. **Data present** — the items table is non-empty, checked **execution-aware**:
   it waits while the bootstrap state machine is `RUNNING`, passes as soon as
   items appear, and fails fast if the latest execution `FAILED`. Bounded by
   `--wait` (`VERIFY_WAIT`, default 1200s) — but it returns as soon as the real
   bootstrap execution finishes, so it does not guess a fixed duration for the
   large first-time catalog sync.

**Market rows are deliberately not asserted** — they are ETL-populated and absent
on a fresh environment; check (2) proves the RDS path serves.

`make deploy` becomes `build → sam deploy → verify`; migrations and the
first-create bootstrap already run inside `sam deploy`, so this only appends the
confirmation. `VERIFY=false` skips it (deploy infra without gating on data), and
`make verify` runs standalone.

## Consequences

- (+) `make deploy` now confirms the stack actually serves, uniformly on dev and
  prod, with no API key to provision or manage.
- (+) The data check follows the real async bootstrap rather than a blind timer,
  so it neither false-fails a slow first catalog sync nor waits needlessly on a
  redeploy where data already exists.
- (−) On a brand-new environment the data check can block `make deploy` for the
  full first-time catalog bootstrap; use `VERIFY=false` (then `make verify`
  later) or raise `VERIFY_WAIT` if that wait is unwanted.
- (−) It exercises the API's public liveness route and the data plane directly,
  not the key-gated API read path end-to-end; a prod-only demo-key check could be
  layered on later if that coverage is wanted.
- (−) `verify` is wired into the local `make deploy` only; the CI prod deploy
  runs `sam deploy` directly and is not changed here.

## Notes

Implements ADR-0024 item 5 and completes the deploy-convergence slices. Builds on
ADR-0026 (admin-query Lambda, reused for the RDS check) and ADR-0028 (the
bootstrap the data check waits on).
