# ADR-0001: AWS SAM over CDK

## Status

Accepted

## Context

We need Infrastructure as Code for a serverless stack comprising Lambda, API
Gateway, Step Functions, RDS, and DynamoDB. AWS CDK offers L2/L3 constructs
but adds a TypeScript dependency and an extra abstraction layer between the
developer and CloudFormation. The team is a single person, and iteration speed
matters more than high-level abstractions.

## Decision

Use AWS SAM with nested CloudFormation stacks (`infra/*.yaml`) as the sole IaC
tool. No CDK dependency.

## Consequences

- (+) Simpler templates that map directly to CloudFormation resources.
- (+) Faster iteration with `sam build && sam deploy`; no synth step.
- (+) No TypeScript toolchain required in a Python-only project.
- (-) More verbose YAML for complex resources (no L2/L3 constructs).
- (-) Nested stack orchestration is manual via `AWS::CloudFormation::Stack`.
