---
inclusion: always
---

# Repository Structure

Forward-looking layout based on `.kiro/specs/v3/tasks.md`. Sections
fill in as their phase lands; track progress against the `tasks.md`
checkboxes.

```
bdo-market-insights/
├── AGENTS.md                          # cross-vendor agent ops manual
├── README.md
├── LICENSE
├── pyproject.toml                     # uv-managed
├── Makefile                           # build/test/lint/deploy/db-tunnel
├── samconfig.toml                     # dev + prod envs
├── template.yaml                      # SAM root, nests infra/*.yaml
├── log.md                             # session log (append-only)
├── .github/workflows/ci.yml           # the only CI workflow
├── .kiro/
│   ├── specs/v3/                      # active spec
│   │   ├── requirements.md
│   │   ├── design.md
│   │   └── tasks.md
│   └── steering/                      # this directory
│       ├── product.md
│       ├── tech.md
│       └── structure.md
├── docs/
│   ├── adr/                           # one MD per ADR (Nygard)
│   ├── architecture.md
│   ├── runbook.md
│   └── slo.md
├── infra/
│   ├── network.yaml                   # VPC, subnets, SGs, gateway endpoints
│   ├── data.yaml                      # RDS, DynamoDB, Secrets Manager
│   ├── bastion.yaml                   # gated by EnableBastion
│   ├── etl.yaml                       # Step Functions + EventBridge crons
│   ├── api.yaml                       # API Gateway + usage plan
│   ├── observability.yaml             # dashboard + alarms
│   └── openapi.yaml                   # generated in CI
├── migrations/                        # Alembic
│   ├── alembic.ini
│   └── versions/0001_initial.py
├── scripts/
│   └── seed_items.py                  # one-time DynamoDB seed
├── src/
│   ├── functions/                     # 8 Lambda handlers
│   │   ├── retrieve_items/app.py
│   │   ├── fetch_data/app.py
│   │   ├── clean_data/app.py
│   │   ├── store_data/app.py
│   │   ├── rollup_daily/app.py
│   │   ├── purge_old_snapshots/app.py
│   │   ├── item_registry/app.py
│   │   └── market_query/app.py
│   └── layer/python/bdo_common/       # shared layer (ADR-0003)
│       ├── arsha_client.py
│       ├── db.py
│       ├── dynamo.py
│       ├── repositories.py
│       ├── models.py
│       ├── pricing.py
│       ├── analytics.py
│       ├── config.py
│       └── rates.json
└── tests/
    ├── unit/                          # mirrors src/layer/python/bdo_common
    ├── integration/
    └── conftest.py
```

## Conventions

- **One Lambda per directory** under `src/functions/`, each with a
  single `app.py` entry point. Handlers import from `bdo_common`.
- **Shared code lives in the Lambda Layer**; never duplicated across
  functions.
- **Infra split by concern** (`network`, `data`, `bastion`, `etl`,
  `api`, `observability`). No 1000-line monolithic CFN.
- **Test layout mirrors source**: e.g. `tests/unit/test_arsha_client.py`
  tests `bdo_common/arsha_client.py`.
- **One root `template.yaml`** that nests `infra/*.yaml`. No second
  SAM template, no parallel Terraform.
