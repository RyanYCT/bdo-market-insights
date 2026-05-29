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
  -> itemRegistry Lambda   (DynamoDB read/write)
  -> marketQuery Lambda    (RDS read)

Data Stores
  - RDS Postgres   : market_snapshot, market_daily, item, item_sid
  - DynamoDB       : bdo-v3-items (item registry)

Shared Layer (bdo-common)
  arsha_client, db, models, repositories, pricing, analytics
```

## Key Design Decisions

Full rationale lives in `docs/adr/`. Summary:

| ADR | Decision |
|-----|----------|
| 0001 | SAM for IaC |
| 0006 | No NAT - mixed VPC placement |
| 0007 | Powertools for observability |
| 0008 | IAM database authentication |
| 0010 | Lazy item population via registry |
| 0011 | Single-AZ workload |

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
