# ADR-0019: Icons bucket deletion policy — retain on prod, delete on dev

## Status

Accepted

## Context

`IconsBucket` (`bdo-<stage>-icons`, `infra/icons.yaml`) originally set
`DeletionPolicy: Retain` / `UpdateReplacePolicy: Retain` unconditionally, so
that materialized icons survive a stack delete and a later stack does not have
to re-scrape the whole tracked set from the Pearl Abyss CDN.

Combined with the bucket's **fixed name** (`bdo-<stage>-icons`, required so
`IconSyncFunction`'s `ICONS_BUCKET` env var and `S3WritePolicy` resolve it
consistently), this created a recurring dev-workflow failure: tearing down or
rolling back the dev stack left the bucket behind (`Retain` did its job), and
the next fresh `IconsStack` create then failed with a bucket-name collision
(`CREATE_FAILED`, S3 "bucket already exists"). The runbook documented a manual
purge-and-delete as the workaround (see `docs/runbook.md`, "Cleanup and
teardown"), but it had to be run by hand every time.

CloudFormation cannot delete a non-empty S3 bucket, regardless of
`DeletionPolicy` — so simply switching to `Delete` on dev is not enough; the
bucket's objects must be removed first.

## Decision

Split the policy by stage:

- **Prod** keeps `DeletionPolicy: Retain` / `UpdateReplacePolicy: Retain` — the
  durability rationale (avoid re-scraping thousands of icons) applies fully
  there, and prod stack deletes are already deliberate, guarded operations.
- **Dev** switches to `Delete` — dev is disposable, the tracked set is small,
  and a fresh deploy should not have to work around a leftover bucket.

Both are expressed with the stage condition (`IsProd`), using the CloudFormation
intrinsic-function support in `DeletionPolicy`/`UpdateReplacePolicy`:

```yaml
DeletionPolicy: !If [IsProd, Retain, Delete]
UpdateReplacePolicy: !If [IsProd, Retain, Delete]
```

Because S3 refuses to delete a non-empty bucket, dev also gets a small
Lambda-backed CloudFormation custom resource, `IconsBucketJanitor`
(`src/functions/bucket_janitor`), wired only under `Condition: IsNotProd`. It
empties `IconsBucket` on the stack's `Delete` event, immediately before
CloudFormation attempts the bucket's own (now non-retaining) delete. It never
reports `FAILED` back to CloudFormation — a real failure to empty the bucket is
logged, but the response is always `SUCCESS`, so the janitor itself can never
block or stall a stack delete. If objects genuinely remain, CloudFormation's
own bucket delete then fails with `BucketNotEmpty`, which is a clearer signal
than a stuck custom-resource retry.

Prod never creates `IconsBucketJanitor` at all (the `Condition`), so prod's
retained icons are structurally never touched by a stack delete.

## Consequences

- (+) Dev teardown/recreate no longer collides on the icons bucket; the manual
  purge-and-delete runbook step is only needed for prod (still `Retain` there)
  or if the janitor itself fails.
- (+) Prod behaviour is unchanged — icons still survive a prod stack delete.
- (+) The custom resource is scoped to a single bucket and a single, narrow IAM
  grant (`s3:ListBucket` + `s3:DeleteObject*` on `bdo-<stage>-icons` only).
- (-) One more Lambda per non-prod stack (negligible cost; short-lived,
  invoked only on stack Delete).
- (-) Lambda-backed custom resources have their own operational surface
  (must respond to the pre-signed `ResponseURL`, or CloudFormation waits out
  the full timeout). Mitigated by always responding `SUCCESS` and logging
  failures instead of propagating them.
