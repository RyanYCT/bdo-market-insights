# ADR-0030: Co-locate Lambda log groups with their functions

## Status

Accepted (supersedes the central log-group ownership introduced in the
observability stack)

## Context

`infra/observability.yaml` declared an `AWS::Logs::LogGroup` (with retention) for
every Lambda, centrally. Because the observability stack consumes state-machine
ARNs from the ETL, insights, and bootstrap stacks, it is created **last** in the
nested-stack order. That ordering makes central ownership unsafe on a fresh
deploy:

- Lambda auto-creates `/aws/lambda/<function>` on a function's **first
  invocation** if the group does not already exist, and that runtime-created
  group is not managed by CloudFormation.
- Several functions run *during* the deploy or immediately after their own stack
  finishes — the bootstrap-trigger custom resource (which fire-and-forget starts
  the bootstrap state machine → `catalog-sync` → `seed-tracked` → `icon-sync`,
  ADR-0028), the auto-migrate custom resource invoking the migrator (ADR-0025),
  and the hourly ETL schedule.
- So a Lambda auto-creates its log group before the last-in-order observability
  stack gets to create the managed one, and observability then fails with a
  resource-name conflict. CloudFormation's proactive conflict pre-check reports
  this as an opaque stack-level "Validation failed with N error(s)" (no
  per-resource event), which also makes it hard to diagnose.

## Decision

Move each `AWS::Logs::LogGroup` out of the observability stack and **into the
stack that defines the function** (`etl.yaml`, `api.yaml`, `catalog.yaml`,
`icons.yaml`, `insights.yaml`, `bootstrap.yaml`). Each of those stacks gains a
`LogRetentionInDays` parameter (default 30), plumbed from the root template so
retention stays centrally configurable.

Because a function's own stack is created before that function can be invoked,
the managed log group already exists by first invocation, so nothing races it.
For the functions invoked *during* the deploy, an explicit `DependsOn` makes the
invoking resource wait for the log group:

- `SchemaMigration` (auto-migrate custom resource) → `DependsOn: MigratorLogGroup`.
- `BootstrapTrigger` (bootstrap custom resource) → `DependsOn:
  [BootstrapTriggerLogGroup, SeedTrackedLogGroup]`. `catalog-sync` and
  `icon-sync` need no dependency here: their groups live in the catalog/icons
  stacks, which are created before the bootstrap stack.

The observability stack keeps the dashboard and all SLO/pipeline alarms. Three
previously unmanaged log groups (`bootstrap-trigger`, `seed-tracked`,
`icons-bucket-janitor`) are now managed too, so every Lambda has retention.

## Consequences

- (+) A fresh `make deploy` no longer races or conflicts on log groups; the
  deploy-time bootstrap (`AutoBootstrap=true`) works without a workaround.
- (+) Log groups sit beside their functions (the AWS-idiomatic pattern), and
  every Lambda now has managed retention.
- (+) Retention stays a single knob (`LogRetentionInDays`) on the root template.
- (−) Retention is set in six stacks instead of one; the root parameter keeps it
  centrally controlled, but each function stack must wire it through.
- (−) For an **existing** environment whose observability stack already owns
  these groups, this is a cross-stack resource move: the function stacks update
  before observability (dependency order), so a single deploy would try to create
  a group the observability stack has not yet released. Existing environments
  need a migration (remove from observability first, or delete the unmanaged
  groups in a maintenance window before applying) or a fresh stand-up. Fresh
  environments are unaffected.

## Notes

Follows the log-group conflict found while standing up a fresh dev environment
with the converged deploy (ADR-0028 bootstrap + ADR-0029 verify). Retention
values and the parameter list are unchanged from the previous central definition.
