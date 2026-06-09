# ADR-0010: Lazy Item Population

## Status

Accepted

## Context

The item catalog lives in DynamoDB (`bdo-<stage>-items`). Postgres needs an `item`
table for JOINs in market queries. Options considered were DynamoDB Streams for
real-time sync, a scheduled batch copy, and lazy population on first
observation.

## Decision

The `storeData` Lambda upserts an `item` row on first market observation
(INSERT ON CONFLICT DO NOTHING). No DynamoDB Streams integration in v1.

## Consequences

- (+) Simpler architecture with no Streams infrastructure.
- (+) Zero additional cost (no Stream shards, no extra Lambda).
- (+) Item data is guaranteed to exist before any market data references it.
- (-) New items appear in Postgres only after their first ETL run (max 1-hour
  delay).
- (-) DynamoDB Streams sync is documented here as the upgrade path for
  real-time catalog updates if needed in the future.
