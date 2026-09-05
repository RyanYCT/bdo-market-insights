# ADR-0034: Co-locate the catalog sync checksum with the items table

## Status

Accepted

## Context

`catalogSync` computes a content checksum of the merged catalog and skips the
scan and all writes when it matches the last-synced value (the weekly no-op
fast-path). That checksum was stored in an SSM parameter,
`/bdo-market-insights/<stage>/catalog/checksum`, written at runtime by the
Lambda (not a CloudFormation resource).

The checksum is **runtime-derived state**, not configuration. Storing it in SSM
decoupled its lifecycle from the data it describes: the parameter outlived the
items table it summarized. When the data store is recreated while the parameter
survives — or a fresh environment's bootstrap runs `catalogSync` first, on an
empty table — a stale-but-matching checksum caused the fast-path to skip the
initial full write and leave the catalog empty. ADR-0028's bootstrap and the
guard added in the checksum-reconciliation change (the `catalog_is_empty()`
check) worked around the symptom, but the root cause is the lifecycle
decoupling.

This is the same class of problem ADR-0033 removed for `icon_status`: state that
should share a resource's lifecycle living somewhere that survives that
resource.

Three options were considered:

1. **Keep it in SSM, rely on the empty-table guard.** The guard (ship in the
   prior change) makes the failure self-heal, but the checksum still outlives the
   table, so the hazard is only masked, not removed.
2. **Manage the SSM parameter in CloudFormation.** Contradicts the repo
   convention that SSM parameters are script-seeded config, not CFN-owned
   (ADR-0024): the parameter would need a static placeholder `Value`, and because
   the Lambda overwrites it at runtime the resource would show **permanent
   drift**. It also only couples the lifecycle to the *stack*, not the *table*.
3. **Store the checksum in the items table itself.** The checksum lives as a
   reserved metadata row in `bdo-<stage>-items`, so it shares the table's exact
   lifecycle.

## Decision

Store the catalog checksum as a **reserved metadata row in the items table**
(option 3).

- A single row with the reserved primary key `id = 0` (real BDO item ids are
  `>= 1`) holds the checksum on a `checksum` attribute (plus `kind` /
  `updated_at` for debuggability). `dynamo.read_catalog_checksum()` and
  `write_catalog_checksum()` encapsulate access; `sync_catalog` no longer touches
  SSM.
- Because the checksum now lives in the table it guards, **recreating the table
  drops the checksum with the data**. A stale checksum can no longer outlive an
  empty table, so the original failure is structurally impossible rather than
  merely guarded.
- The `catalog_is_empty()` reconciliation guard is **kept as defence-in-depth**
  (against the catalog rows being cleared while the metadata row somehow
  survives), but it is no longer load-bearing.
- The reserved row is **excluded from every access path that enumerates the
  catalog**: `catalog_is_empty()` (so a lone metadata row still reads empty),
  `scan_catalog_items()` (so it never appears in the `catalog.json` artifact),
  `scan_catalog_fingerprints()`, and `get_item()` (so `GET /v1/items/0` is a
  404). The API list paths are unaffected — the sparse GSIs and the
  `tracked=false` filter already exclude a row that carries none of those
  attributes.
- The `ssm:GetParameter`/`ssm:PutParameter` grant and the
  `CATALOG_CHECKSUM_PARAM` environment variable are removed from the `catalog`
  stack; the existing `DynamoDBCrudPolicy` already covers the metadata row's
  get/update.

## Consequences

- (+) The checksum shares the items table's lifecycle; the stale-checksum /
  empty-table failure is structurally gone, not just guarded.
- (+) One fewer moving part per stage: no runtime-created SSM parameter, no
  cross-service IAM for it, no drift.
- (+) Reads/writes of the checksum ride the same DynamoDB access the sync already
  uses (no extra service on the hot path).
- (−) The reserved metadata row is the documented cost of single-table storage:
  every full-table enumeration must exclude it. This is centralized on one
  module constant and covered by tests.
- (−) The old SSM parameter `/bdo-market-insights/<stage>/catalog/checksum`
  becomes orphaned. It is harmless (nothing reads it) and is deleted as a
  one-time manual cleanup per stage, out of band.

## Notes

The transition is self-correcting and requires no migration. On the first run
after deploy the metadata row does not exist yet, so `read_catalog_checksum()`
returns `None`, the checksum comparison misses, and the sync takes its normal
diff path — which, against an already-populated table, writes ~zero item changes
and then persists the checksum row. Every subsequent run reads the row and takes
the fast-path as before.

Builds on the checksum fast-path and its empty-table guard, and mirrors the
lifecycle-coupling rationale of ADR-0033 (`icon_status`). The SSM
config-plane convention for genuine configuration (domains, flags) is unchanged
(ADR-0024); this only moves runtime-derived state out of it.
