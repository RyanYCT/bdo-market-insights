# ADR-0005: API Key with Usage Plan

## Status

Accepted

## Context

The REST API needs authentication. This is a single-user, low-traffic project.
Options considered were Cognito user pools, API Gateway API keys, and IAM
authentication. Cognito adds significant operational overhead for one user.

## Decision

Use an API Gateway API key with a usage plan configured for 10 RPS burst,
5 RPS sustained rate, and a 1000 requests/day quota. Cognito is deferred to a
future phase if multi-user support is ever needed.

## Consequences

- (+) Zero user management overhead.
- (+) Built-in throttling via usage plan protects backend resources.
- (+) Simple client integration (single `x-api-key` header).
- (-) API keys are not true authentication (no user identity).
- (-) Not suitable for multi-tenant use without upgrading to Cognito/IAM.
