# ADR-0032: Stack topology and shared-asset ownership

## Status

Proposed

## Context

The application is deployed as a parent template with nested stacks
(`infra/*.yaml`). The dependency graph is acyclic and roughly layered, but two
cross-cutting **shared assets are owned by workload stacks**, which leaks
ownership and forces unrelated stacks to depend on a feature stack purely to
reach a shared resource:

- **`CommonLayer`** (the shared `bdo-common` Lambda layer) is defined in the
  **`etl`** stack, yet consumed by `insights`, `api`, `catalog`, and
  `bootstrap`. Four stacks depend on `etl` only for the layer.
- **The delivery CDN** (icons S3 bucket + CloudFront distribution + OAC + CORS +
  the `cdn.ryanyct.com` cert/record) is defined in the **`icons`** stack, yet
  consumed by `api` (icon base URL) and, more recently, `catalog` (publishes the
  catalog artifact into the same bucket). `icons` is really two concerns fused
  together: the shared *delivery* infrastructure and the *icon producer*
  (iconSync + janitor).

Both are instances of the same anti-pattern. Without a recorded rule for where
shared assets live, each new shared resource (another layer, a shared bucket, a
shared topic) risks landing in whatever workload stack happens to create it
first, and we repeat this restructuring.

## Decision

Adopt an explicit, tiered stack topology and a single ownership rule.

### Tiers

| Tier | Stacks | Contains |
| --- | --- | --- |
| **0 — Foundation / platform** | `network`, `data`, `platform` (new), `cdn` (new) | Stable, shared, no application logic. Cross-cutting assets everyone builds on. |
| **1 — Producers / workloads** | `etl`, `insights`, `api`, `catalog`, `icons` (producer only) | Feature/workload logic. |
| **2 — Orchestration / cross-cutting** | `bootstrap`, `observability` | Wiring, seeding, monitoring across tiers. |

### Ownership rule

> A shared asset lives one tier below all of its consumers. No Tier-1 (workload)
> stack owns a resource that another Tier-1 stack consumes. Stack dependencies
> flow strictly downward (Tier 2 -> Tier 1 -> Tier 0), never sideways between
> workloads for a shared asset.

Applied:

- **`platform`** (new, Tier 0) owns the `CommonLayer`. `etl`, `insights`, `api`,
  `catalog`, and `bootstrap` consume `CommonLayerArn` from it. `etl` stops
  owning it.
- **`cdn`** (new, Tier 0) owns the delivery bucket, CloudFront distribution,
  OAC, bucket policy, CORS behaviors, and the `cdn.ryanyct.com` cert + Route 53
  record. It outputs the bucket name + base URL. `api` and `catalog` consume
  those; the `icons` stack is reduced to the icon *producer* (iconSync + janitor)
  writing `icons/*` into the shared bucket.

### DNS / cert / domain ownership

Three separate concerns, deliberately not lumped together ("own your subdomain,
share the zone"):

- **Hosted zone** (`ryanyct.com`) — account-wide, long-lived, shared. Owned by
  no application stack; referenced via the `HostedZoneId` parameter resolved
  from SSM (ADR-0024). Unchanged.
- **ACM cert + custom-domain binding** — lives with the stack that owns the
  endpoint it terminates on. `api.ryanyct.com`'s cert + API Gateway
  `DomainName` stay in `api`; `cdn.ryanyct.com`'s cert (us-east-1, required by
  CloudFront) + distribution alias move to `cdn`.
- **Route 53 record** — lives with the endpoint's stack (it needs the endpoint
  target). `api.*` record in `api`; `cdn.*` record in `cdn`.

A centralized "edge/DNS stack owns all certs and records" pattern is explicitly
rejected: it would force the DNS stack to depend on every service to learn its
endpoint target, inverting the downward-only dependency flow. It only pays off
at org scale with a dedicated platform/DNS team.

### Migration approach

Executed as separate, independently reviewable changes, sequenced by risk:

1. **`platform` extraction (low risk).** A Lambda layer version is an immutable
   published artifact: the new stack publishes a version, consumers repoint to
   the new ARN, the old version drops. No stateful data. Deploy the platform
   stack first.
2. **`cdn` extraction (medium risk).** Moving a retained bucket-with-data and a
   live CloudFront distribution (`cdn.ryanyct.com` + ACM + Route 53) between
   stacks would normally force replacement. Use **CloudFormation stack
   refactoring** to move resources without recreation. Sequence with the
   read-through icon cache work so the icons bucket contents are disposable
   (self-healing), leaving only the distribution + domain as the sticky piece
   the refactor must preserve.

## Consequences

- (+) Shared assets have one obvious home; new shared resources get placed by
  the rule, not by accident. No more "workload stack owns a shared asset"
  restructuring.
- (+) `api`/`catalog` depend on `cdn` (symmetric `Producers -> CDN`) instead of
  on `icons`; `etl` no longer the de-facto layer owner.
- (+) Each service stays independently deployable; downward-only dependencies
  preserved.
- (-) Two more stacks (`platform`, `cdn`) and a one-time migration, including a
  medium-risk move of the distribution/domain.
- (-) Slightly more parent-template wiring (more `GetAtt` edges into Tier 0).

## Notes

Builds on ADR-0023 (icons CDN) and ADR-0031 (catalog artifact), whose
`Catalog -> Icons` edge this supersedes with `Catalog -> CDN`. The hosted-zone /
SSM domain handling follows ADR-0024. Rollout is phased; this ADR records the
target topology and the ownership rule so future stacks conform without
re-litigating the structure.
