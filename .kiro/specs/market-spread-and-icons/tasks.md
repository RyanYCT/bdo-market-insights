# Market spread & item icon URL — Implementation Tasks

Single feature branch; one PR. Both fields are additive/nullable, so nothing
ships half-built. Tests accompany each code change (repo convention).

## Spread

- [x] `analytics.spread_pct(price_min, price_max)` — canonical formula, 1 dp,
      `None` on missing/degenerate inputs (`test_analytics.py`)
- [x] `ItemSidRepo.get(conn, region, item_id, sid)` — primary-key read of the
      `item_sid` band (`test_repositories.py`)
- [x] `market_query` analysis handler: fetch the `item_sid` row and add
      top-level `spread_pct` to the response (`test_market_query_handler.py`)

## Icon URL

- [x] `icons.public_icon_url(item_id, icon_status, base)` — `icons/<id>.png`
      convention, gated on `stored` + a configured base (`test_icons.py`)
- [x] `item_registry` `ItemResponse.icon_url`, resolved from `ICON_BASE_URL`
      (`test_item_registry_handler.py`)
- [x] Infra: `IconBaseUrl` parameter (empty default) threaded
      `template.yaml → api.yaml → itemRegistry` `ICON_BASE_URL` env

## Wrap-up

- [x] Regenerate `infra/openapi.yaml` (typed `ItemResponse` changed)
- [x] `ruff` + `mypy` + `pytest` + `bandit` + `sam validate --lint` green
- [x] ADR-0021; `log.md` entry

## Reserved (out of scope)

- Public **CloudFront-OAC distribution** in front of the private icons bucket
  (the delivery origin `icon_url` points at). Until it lands and `IconBaseUrl`
  is configured, `icon_url` is `null`. Tracked in `infra/icons.yaml`.
- True order-book best-bid/best-ask inputs for the spread (the `spread_pct`
  field name and semantics already accommodate them).
