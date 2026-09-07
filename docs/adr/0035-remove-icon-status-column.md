# ADR-0035: Remove the vestigial `icon_status` column

## Status

Accepted

## Context

`icon_status` (`unset` / `stored` / `missing`) tracked each item's S3 icon
materialization state (ADR-0018, ADR-0021). ADR-0033 replaced the scheduled
push materializer with **read-through** delivery and made `public_icon_url`
**universal** — the URL resolves for every item regardless of status, and icons
materialize on first request. That left `icon_status`:

- no longer gating anything (the API and the catalog artifact expose `icon_url`
  unconditionally),
- still surfaced in the `/v1/items` contract and `catalog.json`,
- still written by `iconSync`,

i.e. dead weight, and a mildly misleading one — a `stored`/`missing`/`unset`
value that no longer corresponds to anything the system acts on. ADR-0033
recorded that a follow-up would remove it; this is that follow-up.

## Decision

Remove `icon_status` entirely:

- Drop the field from the `Item` model and from the DynamoDB read/write path
  (`_item_to_model`, `put_item`, the catalog upsert projection, the artifact
  scan projection).
- Remove it from the `ItemResponse` contract and regenerate the OpenAPI spec, and
  from the `catalog.json` projection.
- Remove the last `icon_status` gate in `iconSync`: the on-demand warm-prefetch
  now materializes **every** tracked item unconditionally (the S3 write is
  idempotent, so a re-run simply re-writes). `iconSync` no longer writes item
  state, so its DynamoDB grant narrows from `DynamoDBCrudPolicy` to
  `DynamoDBReadPolicy` (least privilege).

No data migration is required: DynamoDB is schemaless, so the stray attribute on
existing rows is simply ignored by the new code. It is cleared out-of-band with a
one-time scrub (an ad-hoc operation, not committed to `main`).

## Consequences

- (+) Smaller model and public contract; no meaningless status field; the last
  `icon_status` coupling from the pre-read-through design is gone.
- (+) `iconSync` is now read-only on DynamoDB.
- (−) Removing a response field is a contract change. It is a **minor** version
  bump: `icon_status` has been best-effort/vestigial since ADR-0033 and nothing
  should depend on it, but a consumer still reading it will find it gone.
- (−) A one-time scrub is needed to drop the leftover attribute from existing
  rows; it is an out-of-band operational step, not part of the IaC.

## Notes

Completes the follow-up ADR-0033 deferred. The historical ADRs that introduced
and described `icon_status` (ADR-0018, ADR-0021, ADR-0023) are left unchanged as
records of the original design.
