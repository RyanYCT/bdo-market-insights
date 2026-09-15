---
inclusion: always
---

# Tech Stack

The stack below is locked. Any change requires an ADR in `docs/adr/`.

## Runtime

- **AWS Lambda**, Python **3.12**.
- One shared Lambda Layer `bdo-common` (ADR-0003).
- Step Functions for ETL orchestration (ADR-0004).
- Single-AZ workload (ADR-0011); no NAT (ADR-0006); mixed VPC
  placement (Lambdas attach to the VPC only when they touch RDS).

## Languages and libraries

- **Python 3.12** only. Type-annotate everything.
- **Pydantic v2** at every I/O boundary (API request/response,
  arsha.io payloads, DynamoDB items, DB rows).
- **AWS Lambda Powertools** for: structured logging, X-Ray tracing,
  EMF metrics, parameters/secrets caching, idempotency. Mandatory
  (ADR-0007).
- **psycopg[binary] v3** for Postgres; parameterized SQL only;
  repository pattern in `bdo_common.repositories`. **No ORM.**
- **boto3** for AWS SDK (provided by the Lambda runtime).

## Infrastructure

- **AWS SAM** — one root `template.yaml` with nested stacks
  (`network`, `data`, `platform`, `etl`, `api`, `insights`, `catalog`,
  `icons`, `cdn`, `bootstrap`, `observability`; ADR-0032).
  Not CDK, not raw CFN, not Terraform (ADR-0001).
  **SAM CLI >= 1.160.0**, since `etl.yaml`/`insights.yaml` use
  `Fn::ForEach` (`AWS::LanguageExtensions`) for the per-region schedule
  fan-out and need its local expansion, enabled in `samconfig.toml`
  (ADR-0036).
- **IAM database authentication** for Lambdas (ADR-0008). Ad-hoc human
  DB access uses the in-VPC `adminQuery` Lambda (ADR-0026); there is no
  standing bastion, and break-glass is on-demand (ADR-0027).
- **Alembic** owns the Postgres schema.
- RDS Proxy is opt-in via SAM parameter `UseRdsProxy`, default
  `false` (ADR-0002).

## Tooling

- **uv** manages `pyproject.toml`.
- **ruff** (lint + format) and **mypy** (type-check) gate every PR.
- **pytest** + **moto** for tests; ephemeral Postgres in CI for DB
  tests. **No coverage gate** — coverage is a signal, not a wall.
- **Purpose-scoped GitHub Actions workflows** (ADR-0038): one authoritative
  validation workflow (`.github/workflows/ci.yml`) is the required gate. Add
  a separate workflow only for a distinct trigger or permission scope, and
  factor shared setup into a reusable workflow/composite action so they
  cannot drift.

## Forbidden — already paid for in `rewrite-project`

- Hand-rolled retry, circuit breaker, rate limiter, structured logger.
- Copy-pasted workflow steps that drift across workflows (multiple
  purpose-scoped workflows are fine — ADR-0038 — un-factored duplication is
  not).
- Per-Lambda `.env.example` files duplicating central config.
- Bespoke shell scripts where SAM/Make would do.
- DOCUMENTATION_MAP.md-style meta-docs that index other docs.
- 80%+ coverage gates the suite cannot meet.
- Generic min/max/avg statistics replacing BDO-specific business
  logic.
- Big-bang rewrites in a single PR.
