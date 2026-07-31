# Item icon URL — Requirements

## Product

Expose a **public icon URL** (`icon_url`) per item on the item registry — an
address for the self-hosted icon the materializer already stores. The field is
**additive and nullable**: existing consumers are unaffected, and it is `null`
when the icon is unavailable.

## Functional Requirements

- **FR-1** The `/v1/items` item shape (list and single-item) SHALL include an
  `icon_url` field per item.
- **FR-2** `icon_url` SHALL be an absolute, fetchable URL WHEN the item's
  `icon_status == "stored"` and a delivery base is configured; otherwise `null`.
- **FR-3** The URL SHALL follow a stable, documented convention (design.md)
  derived from a configured base and the `item_id`, matching the object key the
  materializer writes, so it is cacheable and stable across deploys.
- **FR-4** `icon_url` SHALL be item-level (independent of `sid`).
- **FR-5** The field SHALL be additive and optional; consumers that ignore it
  are unaffected. The OpenAPI document is regenerated for the typed
  `ItemResponse` change.

## Non-Functional Requirements

- **NFR-1 (delivery)** Icons are served as static objects behind a CDN with
  cache-friendly headers; API payloads carry only the URL, never image bytes.
- **NFR-2 (stability)** A stored icon's URL is deterministic and durable for a
  given base + id (no per-request signing that churns the URL).
- **NFR-3 (explicit nulls)** The field is present with an explicit `null` when
  unavailable, so "unknown" is unambiguous and partial rollout is safe.
- **NFR-4 (config hygiene)** The icon delivery base is a deploy-time parameter
  with an empty default (opt-in); real hosts are never committed.

## Out of scope

- The **CloudFront-OAC distribution** in front of the private icons bucket (the
  public delivery origin `icon_url` points at) — a separate follow-up already
  noted in `infra/icons.yaml`. Until it exists and a base is configured,
  `icon_url` is `null`.
