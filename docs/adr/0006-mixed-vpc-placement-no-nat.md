# ADR-0006: Mixed VPC Placement (No NAT)

## Status

Accepted

## Context

Some Lambdas need internet access to call the arsha.io API, while others need
access to RDS in a private subnet. A NAT Gateway costs approximately $32/month
plus data transfer charges, which exceeds the project budget on its own.

## Decision

Only DB-touching Lambdas (storeData, rollupDaily, purgeOldSnapshots,
marketQuery) attach to the VPC. The remaining Lambdas (retrieveItems,
fetchData, cleanData, itemRegistry) run outside the VPC with default Lambda
internet egress. A DynamoDB Gateway Endpoint is provisioned for in-VPC Lambdas
that need DynamoDB access.

## Consequences

- (+) Eliminates NAT Gateway cost entirely.
- (+) Keeps infrastructure within the ~$15/month budget target.
- (+) Internet-facing Lambdas have simpler networking (no VPC cold start
  penalty).
- (-) More complex Lambda configuration; must carefully track which functions
  need VPC attachment.
- (-) In-VPC Lambdas cannot reach the internet without additional setup.
