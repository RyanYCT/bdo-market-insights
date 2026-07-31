# Item icon URL — Implementation Tasks

Single feature branch; one PR. The field is additive/nullable, so nothing ships
half-built. Tests accompany the code change (repo convention).

- [x] `icons.public_icon_url(item_id, icon_status, base)` — `icons/<id>.png`
      convention, gated on `stored` + a configured base (`test_icons.py`)
- [x] `item_registry` `ItemResponse.icon_url`, resolved from `ICON_BASE_URL`
      (`test_item_registry_handler.py`)
- [x] Infra: `IconBaseUrl` parameter (empty default) threaded
      `template.yaml → api.yaml → itemRegistry` `ICON_BASE_URL` env
- [x] Regenerate `infra/openapi.yaml` (typed `ItemResponse` changed)
- [x] `ruff` + `mypy` + `pytest` + `bandit` + `sam validate --lint` green
- [x] ADR-0021; `log.md` entry

## Delivered separately

- Public **CloudFront-OAC distribution** in front of the private icons bucket
  (the delivery origin `icon_url` points at) — **ADR-0023** / `infra/icons.yaml`.
  The distribution's base URL is wired into the API automatically, so `icon_url`
  resolves once deployed (custom hostname optional via `IconDomainName`).
