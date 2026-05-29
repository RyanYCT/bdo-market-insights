# ADR-0004: Step Functions for ETL Orchestration

## Status

Accepted

## Context

Hourly market data ingestion requires orchestrating multiple stages:
retrieveItems, fetchData, cleanData, storeData, and rollupDaily. Options
considered were SQS fan-out, direct Lambda chaining (one invokes the next), and
AWS Step Functions.

## Decision

Use AWS Step Functions with a state machine that employs a Map state
(`maxConcurrency=5`, batch size 50), per-stage retry with exponential backoff,
and built-in observability via execution history.

## Consequences

- (+) Visual debugging in the Step Functions console.
- (+) Automatic retry with configurable exponential backoff and max attempts.
- (+) Native error handling with Catch/Fallback states.
- (+) Full execution history for auditing and troubleshooting.
- (-) Slightly higher cost per execution vs direct Lambda invocation.
- (-) Amazon States Language (ASL) adds complexity to the template.
