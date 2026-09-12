---
inclusion: always
---

# Repository Structure

Current repository layout. v3 is shipped; work since then lands as
feature specs under `.kiro/specs/<feature>/`.

```
bdo-market-insights/
├── AGENTS.md                          # cross-vendor agent ops manual
├── README.md
├── LICENSE
├── pyproject.toml                     # uv-managed
├── Makefile                           # build/test/lint/deploy/break-glass
├── samconfig.toml                     # dev + prod envs
├── template.yaml                      # SAM root, nests infra/*.yaml
├── log.md                             # session log (append-only)
├── .github/workflows/ci.yml           # the only CI workflow
├── .kiro/
│   ├── specs/
│   │   ├── v3/                        # shipped baseline spec
│   │   │   ├── requirements.md
│   │   │   ├── design.md
│   │   │   ├── domain-model.md
│   │   │   └── tasks.md
│   │   └── <feature>/                 # one dir per feature since v3
│   ├── skills/                        # workspace agent skills (SKILL.md)
│   │   ├── domain-modeling/           # glossary + ADR discipline
│   │   └── grill-with-docs/           # design interview, records as it goes
│   └── steering/                      # this directory
│       ├── product.md
│       ├── tech.md
│       └── structure.md
├── docs/
│   ├── adr/                           # one MD per ADR (Nygard)
│   ├── architecture.md
│   ├── runbook.md
│   └── slo.md
├── infra/                             # nested stacks (order per ADR-0032)
│   ├── network.yaml                   # VPC, subnets, SGs, gateway endpoints
│   ├── data.yaml                      # RDS, DynamoDB, IAM roles
│   ├── platform.yaml                  # shared bdo-common layer (Tier-0)
│   ├── etl.yaml                       # Step Functions + EventBridge crons
│   ├── api.yaml                       # API Gateway + usage plan
│   ├── insights.yaml                  # LLM insights pipeline
│   ├── catalog.yaml                   # catalog artifact + sync
│   ├── icons.yaml                     # icon storage + sync
│   ├── cdn.yaml                       # delivery CloudFront (Tier-0)
│   ├── bootstrap.yaml                 # DB bootstrap orchestrator
│   ├── observability.yaml             # dashboard + alarms
│   ├── break-glass.yaml               # on-demand only; not in root template
│   └── openapi.yaml                   # generated in CI
├── migrations/                        # Alembic
│   ├── alembic.ini
│   └── versions/                      # 0001_initial.py … (sequential)
├── scripts/
│   ├── data/                          # offline tracking inputs (see below)
│   ├── select_tracked.py              # resolves data/ -> tracked_items.json
│   ├── build_market_catalog.py        # builds the catalog artifact
│   ├── seed_*.py                      # one-time seeds (items, catalog, dev)
│   ├── db_admin.py, db_bootstrap.py   # DB ops helpers
│   ├── export_openapi.py, export_postman.py
│   └── verify.py                      # deploy verification (ADR-0029)
├── src/
│   ├── functions/                     # 21 Lambda handlers, one dir each (app.py)
│   │   ├── retrieve_items/ fetch_data/ clean_data/ store_data/      # ETL pipeline
│   │   ├── rollup_daily/ purge_old_snapshots/                       # scheduled maintenance
│   │   ├── item_registry/ market_query/ admin_query/ docs/          # API
│   │   ├── catalog_sync/                                            # catalog artifact sync
│   │   ├── icon_sync/ icon_origin/ bucket_janitor/                  # icons + CDN
│   │   ├── insights_compute/ insights_summarize/ insights_store/ insights_discord/  # LLM insights
│   │   ├── seed_tracked/                                            # tracked-items seed
│   │   └── bootstrap_trigger/ migrator/                             # bootstrap + DB migration
│   └── layer/python/bdo_common/       # shared layer (ADR-0003)
│       ├── arsha_client.py
│       ├── db.py
│       ├── dynamo.py
│       ├── repositories.py
│       ├── models.py
│       ├── pricing.py
│       ├── analytics.py
│       ├── catalog.py
│       ├── catalog_artifact.py
│       ├── icons.py
│       ├── tracking.py
│       ├── config.py
│       ├── rates.json
│       └── insights/                  # digest, narrative, prompt, models
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
- **Infra split by concern** (`network`, `data`, `etl`, `api`,
  `observability`). No 1000-line monolithic CFN. (`break-glass.yaml` is
  on-demand only and not nested in the root template.)
- **Test layout mirrors source**: e.g. `tests/unit/test_arsha_client.py`
  tests `bdo_common/arsha_client.py`.
- **Specs use the canonical Kiro format** (details in `AGENTS.md` →
  "Specs and ADRs"): `requirements.md` (`# Requirements Document` /
  `## Introduction` / `## Requirements` with `### Requirement N`, user
  stories, EARS acceptance criteria), `design.md` (`## Overview` /
  `## Architecture` / `## Components and Interfaces` / `## Data Models`),
  and a checkbox `tasks.md`. Applies to specs created or materially
  revised from the format recalibration onward; earlier specs keep their
  original format until revised.
- **Agent skills live in `.kiro/skills/<name>/`**, each a `SKILL.md`
  (plus an optional `references/` folder for supporting docs), committed
  and shared. `domain-modeling` maintains the two domain homes — the
  `## Language` glossary in `steering/product.md` (terms; auto-loaded)
  and the active spec's `domain-model.md` (formulas/worked numbers) —
  and records ADRs under `docs/adr/` in Nygard format.
  `grill-with-docs` runs a design interview and records terms and ADRs
  as decisions settle.
- **One root `template.yaml`** that nests `infra/*.yaml`. No second
  SAM template, no parallel Terraform.
- **Changing what's tracked** (add/remove an item or series, e.g. after a
  patch) uses the offline data files under `scripts/data/`
  (`track_sets.json`, `presets.json`, `full_items.json`) resolved by
  `scripts/select_tracked.py` into `tracked_items.json`, then seeded — see
  the runbook's "Adding or removing tracked items & series". `tracked_items.json`
  is hand-formatted, so verify tool edits with a preview run (`+ 0 added`).
- **SSM parameter names are repo-scoped**:
  `/bdo-market-insights/<stage>/<category>/<key>` — never the bare
  `/bdo/...` root (it is not repo-scoped: `bdo-analytics` and
  `bdo-market-insights` both abbreviate to `bdo`, so `/bdo/...` risks
  cross-repo collisions). `<category>` groups related keys
  (`domain`, `api-gateway`, `catalog`, `insights`, ...). Examples:
  `/bdo-market-insights/prod/domain/api-domain-name`,
  `/bdo-market-insights/dev/catalog/checksum`. Deploy config
  (domains/zone/toggles) is resolved from these at deploy (ADR-0024);
  seed with `make seed-config`.
- **S3 bucket names are account- and region-qualified**:
  `bdo-<stage>-<purpose>-${AWS::AccountId}-${AWS::Region}` (e.g. the delivery
  CDN bucket `bdo-<stage>-cdn-<account>-<region>`). S3 bucket names are a
  single global namespace, so qualifying with account + region guarantees
  uniqueness and avoids cross-account/region collisions. Always use the
  `${AWS::AccountId}`/`${AWS::Region}` pseudo-parameters — never a literal
  account id in a committed template. Bucket names are internal (behind
  CloudFront / accessed by ARN), so the longer name is invisible to consumers.
