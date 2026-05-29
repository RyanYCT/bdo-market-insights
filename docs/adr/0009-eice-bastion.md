# ADR-0009: EC2 Instance Connect Endpoint Bastion

## Status

Accepted

## Context

A human DBA needs occasional access to RDS for debugging and schema inspection.
RDS is in a private subnet with no public access. Options considered were making
RDS publicly accessible, a VPN, a classic bastion host with a public IP, and an
EC2 Instance Connect Endpoint (EICE).

## Decision

Deploy a `t4g.nano` bastion instance in the private subnet, fronted by an EC2
Instance Connect Endpoint. No public IP is assigned anywhere. Access is
IAM-authenticated via SSH port-forwarding using `make db-tunnel-up` and
`make db-tunnel-down`. The bastion is gated by the SAM parameter
`EnableBastion` (default: `false`).

## Consequences

- (+) Zero public attack surface; all access is IAM-gated.
- (+) Minimal cost (~$3/month when running); instance can be stopped when not
  needed.
- (+) No VPN infrastructure to maintain.
- (-) Requires AWS CLI v2 and the EC2 Instance Connect plugin on the operator
  machine.
- (-) Slightly more complex access procedure than a direct SSH bastion.
