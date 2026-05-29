# ADR-0011: Single-AZ Workload

## Status

Accepted

## Context

This is a single-user hobby/portfolio project with a strict cost target of
~$15/month. Multi-AZ RDS doubles the database cost. Cross-AZ data transfer
adds additional charges. High availability is not required for a non-critical
workload with one user.

## Decision

Deploy all resources in a single Availability Zone. The DB Subnet Group spans
2 AZs only because AWS requires a minimum of 2 subnets in different AZs for
the subnet group resource. Actual workload (RDS instance, VPC Lambdas, bastion)
runs in the primary AZ only.

## Consequences

- (+) No cross-AZ data transfer charges.
- (+) No Multi-AZ RDS premium (saves ~$15/month).
- (+) Stays comfortably within the $15/month budget target.
- (-) Single point of failure at the AZ level.
- (-) Acceptable risk for a non-critical single-user workload.
- (-) Upgrade path: enable Multi-AZ RDS and distribute Lambdas if availability
  requirements change.
