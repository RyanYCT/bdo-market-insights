# ADR-0007: AWS Lambda Powertools (Mandatory)

## Status

Accepted

## Context

The project needs structured logging, distributed tracing, custom metrics, and
parameters caching. A previous rewrite hand-rolled all of these utilities, and
they drifted out of sync and eventually broke. Consistency and reliability of
observability tooling is critical.

## Decision

AWS Lambda Powertools is mandatory for all cross-cutting concerns: Logger,
Tracer, Metrics, and Parameters. No hand-rolled retry logic, circuit breakers,
rate limiters, or structured loggers are permitted.

## Consequences

- (+) Battle-tested library maintained by AWS.
- (+) Consistent JSON logs with correlation IDs across all functions.
- (+) Built-in X-Ray tracing integration.
- (+) EMF (Embedded Metric Format) metrics without custom CloudWatch calls.
- (-) Added dependency (~15 MB in the Lambda Layer).
- (-) All developers must follow Powertools patterns and conventions.
