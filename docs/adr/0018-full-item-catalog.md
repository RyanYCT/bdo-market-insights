# ADR-0018: Full item catalog in the items table — sparse tracked index, grade, localized names, icons

## Status

Accepted. Extends [ADR-0010](0010-lazy-item-population.md) (item population) and
builds on [ADR-0004](0004-step-functions-etl.md) (the hourly ETL state machine).

## Context

The DynamoDB items table (`bdo-<stage>-items`) has so far held only the subset
of items the ETL polls. `retrieveItems` selects that subset with a
`Scan` + `FilterExpression(tracked = true)`, which is acceptable while the table
is small.

New requirements need item metadata for the *whole* game, not just the polled
subset:

- an item **grade** (the colour tier shown as a badge) for browsing/filtering;
- item **names in more than one language** (English plus Traditional Chinese
  initially);
- item **icons** for display.

arsha.io exposes the entire BDO item universe — `id`, `name`, `grade` — at
`GET https://api.arsha.io/util/db?lang=<lang>`, keyed by the same `lang` codes
recorded in `arsha_client.SUPPORTED_LANGS`. This universe is tens of thousands
of items (including non-marketable furniture, costumes, quest items), i.e. far
larger than the polled subset. Two structural questions follow: where the full
catalog lives, and how it is written without corrupting the fields the ETL
depends on.

## Decision

### 1. One table, distinguished by a sparse tracked index

Store the full catalog in the **existing single items table** rather than a
separate table. Every item is a row; the polled subset carries a marker
attribute that is **absent** on catalog-only rows. A **sparse GSI**
(`tracked-index`) is built on that marker, so the index contains only the polled
rows regardless of how large the catalog grows.

`retrieveItems` changes from a `Scan` + filter to a **`Query` on
`tracked-index`**. A `Scan` bills for every row read before the filter applies
(so catalog growth would inflate the hourly cost); a `Query` on the sparse
index bills only for the polled rows. Read cost therefore scales with the polled
subset, not the catalog size.

Keeping grade and names on the item row (rather than in a second table) means
they need no denormalization or cross-table join to reach a query result — the
grade is simply an attribute of the item.

### 2. Item attributes

Added to the `Item` model (all optional, defaulted — existing rows and callers
are unaffected):

- **`grade: int | None`** — the raw grade code straight from `util/db`
  (0=White, 1=Green, 2=Blue, 3=Gold, 4=Orange, 5=Violet, …). Stored as an
  open-ended integer with **no closed enum and no check constraint**, so a grade
  introduced by a future game update still stores and reads without error. The
  code-to-name/colour mapping lives in the presentation layer with a neutral
  fallback for unmapped codes.
- **`names: dict[str, str]`** — non-default localizations, e.g. `{"tw": "…"}`.
  English is *not* duplicated here; it stays on the canonical **`name`**, which
  remains the guaranteed-present baseline (and the value the Postgres `item`
  row and `Record` continue to use). `Item.display_name(lang)` resolves a
  language to its localized name, falling back to the English `name` when a
  localization is absent.
- **`icon_status: "unset" | "stored" | "missing"`** — the S3 icon
  materialization state (see §5).

### 3. Catalog sync (weekly) and one-time backfill

A weekly EventBridge rule invokes a `catalogSync` Lambda that pulls `util/db`
for the configured languages (initially `en` and `tw`), **merges the responses
by `id` in memory**, and upserts each item. `grade` is language-independent and
taken from any one language response.

- **One-time backfill** into the empty table uses `BatchWriteItem` (25 puts per
  call). The table is empty, so full-item puts have nothing to overwrite.
- **Weekly incremental** uses a **partial `UpdateItem` per item** (see §4).
  `BatchWriteItem` cannot express partial or conditional writes, so it is used
  only for the empty-table backfill.

New items are those whose `id` first appears in a `util/db` response; they are
inserted with `tracked` unset (catalog-only) and detected via the write itself
(§4) so they can be surfaced for curation. Whether a new item is promoted to the
polled subset stays a separate, explicit step (`item_registry`).

### 4. Disjoint write ownership prevents overwrites

Each writer touches a disjoint set of attributes, all through partial
`UpdateItem`, so no writer can clobber another's fields:

| Writer         | Writes                                             | Never writes                                  |
| -------------- | -------------------------------------------------- | --------------------------------------------- |
| catalog backfill (empty table) | whole row via `BatchWriteItem`        | — (nothing pre-exists)                        |
| catalog sync (weekly)          | `name`, `names`, `grade`, `updated_at`, `created_at` (once) | `tracked`, `model_id`, `cron_table`, `icon_status` |
| `item_registry` (promotion)    | tracked marker, `model_id`, `cron_table` | `name`, `grade`, `names`                     |
| `ensureIcon` (ETL)             | `icon_status`                          | everything else                               |

Conditional expressions are used only to stamp first-seen
(`created_at = if_not_exists(created_at, :now)`) and to distinguish new from
existing rows (`ReturnValues = ALL_OLD`: an empty old image means the item is
new). A condition can gate a write but cannot make a `PutItem` partial, so
field preservation comes from using `UpdateItem` (partial `SET`), not from a
condition. Because the sync and promotion writers touch disjoint attributes,
per-attribute last-writer-wins also means they can run concurrently on the same
item without losing each other's changes — no condition needed for that either.

### 5. Icons

Icons come from the official Pearl Abyss CDN
(`https://{host}/{region}/TradeMarket/Common/img/BDO/item/{id}.png`, region
upper-cased) and are self-hosted in a private S3 bucket rather than hotlinked. A
**standalone daily `iconSync` Lambda** materializes them for the **tracked
subset only** — the items the grid displays; fetching all ~tens-of-thousands of
catalog icons (mostly items never shown) would be wasted bandwidth and storage.
It processes items with `icon_status = unset`, keyed on that attribute: `stored`
→ skip; fetch → `PUT` to S3 → `stored`; a `403`/`404` (the CDN has no icon for
that id) → `missing` (stop retrying; the UI uses a placeholder); a transient
error → leave `unset` to retry next run. It runs independently of the hourly ETL
(the hot path is untouched); a newly curated tracked item gets its icon by the
next daily run.

## Consequences

- (+) The hourly ETL read cost is bounded by the polled subset via the sparse
  index, independent of catalog size.
- (+) Grade and localized names live on the item row, so a query result carries
  them directly — no second table, join, or denormalization step.
- (+) Open-ended `grade` and the `name`/`names` fallback both degrade
  gracefully: an unknown grade renders neutrally, a missing localization renders
  English — neither errors, and future grades/languages need no schema change.
- (+) Disjoint write ownership plus partial updates make cross-writer overwrites
  structurally impossible, so the weekly sync cannot corrupt ETL-owned fields.
- (−) The catalog write is tens of thousands of partial `UpdateItem` calls
  weekly (no `BatchWriteItem` for partial writes); acceptable at a weekly
  cadence, paced and parallelized.
- (−) The table now mixes a large, rarely-read reference set with the small hot
  polled set. Any genuine full-table `Scan` (admin/debug) now traverses the
  whole catalog; the ETL path avoids this by querying the sparse index.
- (−) A separate catalog table would give physical blast-radius isolation; this
  decision trades that for a single source of truth and no grade
  denormalization, relying on disjoint write ownership for safety instead.

## Future work

- **Full-catalog item browser.** Icons are materialized only for the tracked
  subset (§5). A later frontend feature — a page to browse every BDO item and
  choose which to track — would extend icon coverage to the whole catalog. Icon
  materialization would then be **catalog-driven rather than curation-driven**:
  run `iconSync` chained after `catalogSync`, gated on whether the catalog
  actually changed (unchanged catalog ⇒ no new items ⇒ skip both), replacing the
  standalone daily schedule used for the tracked-only scope. Deferred until the
  browser exists, to avoid fetching and storing icons for the many catalog items
  that are never displayed.

## Related

- ADR-0004 — Step Functions ETL; this ADR switches its `retrieveItems` stage to
  the sparse-index query. The catalog sync and icon materializer run as separate
  scheduled Lambdas, not inside the state machine.
- ADR-0010 — lazy item population; this ADR extends catalog handling and follows
  the same "materialize later, don't block the hot path" spirit for icons (via a
  separate scheduled Lambda rather than the ETL).
- ADR-0003 — the shared Lambda layer, where `SUPPORTED_LANGS`, the `Item` model,
  and the DynamoDB wrappers live.
