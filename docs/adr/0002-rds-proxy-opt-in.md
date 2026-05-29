# ADR-0002: RDS Proxy Opt-In

## Status

Accepted

## Context

Lambda cold starts create new database connections. With 8 Lambdas at low
traffic (~24 ETL runs/day plus minimal API calls), connection pooling may not
be necessary. RDS Proxy costs approximately $15/month, which is significant
for a single-user hobby project targeting a $15/month total budget.

## Decision

Use a module-global `psycopg` connection that is reused across warm
invocations by default. RDS Proxy is available as an opt-in via the SAM
parameter `UseRdsProxy` (default: `false`). When enabled, Lambdas connect
through the proxy endpoint instead of directly to RDS.

## Consequences

- (+) No extra cost by default; stays within budget.
- (+) Simple connection model for low-concurrency workloads.
- (+) Clear upgrade path when traffic grows (flip one parameter).
- (-) Must monitor connection count if concurrency increases unexpectedly.
- (-) Module-global connection requires graceful handling of stale connections.
