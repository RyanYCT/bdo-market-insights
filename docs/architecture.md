# Architecture

## Overview

Serverless market-data platform for Black Desert Online. Hourly ETL
ingests price and stock data from arsha.io into RDS Postgres; a REST
API exposes snapshots, daily rollups, and BDO-domain analytics.

## System Components

```
EventBridge (cron)
  -> Step Functions state machine
       -> retrieveItems (DynamoDB scan)
       -> Map: fetchData -> cleanData -> storeData
       -> rollupDaily (daily schedule)
       -> purgeOldSnapshots (daily schedule)

API Gateway (REST, API key)
  [optional custom domain: api[.<env>].example.com -> ACM cert + Route 53 alias]
  -> itemRegistry Lambda   (DynamoDB read/write)
  -> marketQuery Lambda    (RDS read)

Data Stores
  - RDS Postgres   : market_snapshot, market_daily, item, item_sid
  - DynamoDB       : bdo-<stage>-items (item registry)

Shared Layer (bdo-common)
  arsha_client, db, models, repositories, pricing, analytics
```

## Key Design Decisions

Architectural decisions live in `docs/adr/` (one ADR per file, Michael
Nygard format). The canonical decision table — kept in lock-step with
the architecture — is in
[`.kiro/specs/v3/design.md`](../.kiro/specs/v3/design.md). This file
intentionally does not duplicate it.

## Data Flow

**Hourly ETL** - EventBridge triggers the ETL state machine.
`retrieveItems` scans DynamoDB for tracked items, then a Map state
fans out: `fetchData` calls arsha.io, `cleanData` normalises the
response, `storeData` upserts rows into Postgres.

**Daily Aggregation** - `rollupDaily` compresses snapshots into
`market_daily` rows. `purgeOldSnapshots` removes data older than 90
days.

## Networking

Single VPC with 2 private subnets (AWS requires 2 AZs for a DB
Subnet Group). Only database-touching Lambdas are placed inside the
VPC. A DynamoDB Gateway Endpoint provides in-VPC access without NAT.

EICE (EC2 Instance Connect Endpoint) allows DBA access to RDS through
a bastion host without a public IP or NAT gateway.

## Custom Domain (optional)

The REST API can be fronted by a branded hostname (ADR-0013). It is
**opt-in**: when the `ApiDomainName` parameter is empty (the default), no
domain resources are created and the API is reached via the generated
`execute-api` URL.

When a hostname is supplied, the API stack provisions a regional ACM
certificate (DNS-validated through Route 53), an API Gateway custom
`DomainName`, a base-path mapping to the stage, and a Route 53 A-alias
record. The parent hosted zone (`example.com`) is shared infrastructure
owned elsewhere and is referenced by ID only — this stack never creates or
modifies the zone beyond its own record.

Naming follows `{service}.{env}.example.com`, prod omitting the env label —
e.g. `api.example.com` (prod), `api.dev.example.com` (dev).
