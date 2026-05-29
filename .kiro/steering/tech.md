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
  (`network`, `data`, `bastion`, `etl`, `api`, `observability`).
  Not CDK, not raw CFN, not Terraform (ADR-0001).
- **IAM database authentication** for Lambdas (ADR-0008); a separate
  `dba` Postgres role with password in Secrets Manager exists only
  for human bastion access (ADR-0009).
- **Alembic** owns the Postgres schema.
- RDS Proxy is opt-in via SAM parameter `UseRdsProxy`, default
  `false` (ADR-0002).

## Tooling

- **uv** manages `pyproject.toml`.
- **ruff** (lint + format) and **mypy** (type-check) gate every PR.
- **pytest** + **moto** for tests; ephemeral Postgres in CI for DB
  tests. **No coverage gate** — coverage is a signal, not a wall.
- **Single GitHub Actions workflow** (`.github/workflows/ci.yml`).
  Adding a second workflow is a smell.

## Forbidden — already paid for in `rewrite-project`

- Hand-rolled retry, circuit breaker, rate limiter, structured logger.
- Multiple CI workflows that drift out of sync.
- Per-Lambda `.env.example` files duplicating central config.
- Bespoke shell scripts where SAM/Make would do.
- DOCUMENTATION_MAP.md-style meta-docs that index other docs.
- 80%+ coverage gates the suite cannot meet.
- Generic min/max/avg statistics replacing BDO-specific business
  logic.
- Big-bang rewrites in a single PR.
