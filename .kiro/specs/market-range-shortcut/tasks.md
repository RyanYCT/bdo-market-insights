# Market range shortcut — Implementation Tasks

> **Not scheduled.** Design only. Tasks are detailed once the endpoint shape is
> chosen (requirements.md "Open questions").

## Blocked on decisions

- [ ] Choose the endpoint shape: unified `/series?range=` vs `range` sugar on the
      existing `/snapshots` + `/daily` endpoints (Open question 1)
- [ ] Resolve the `30d`-hourly vs `MAX_SNAPSHOT_LIMIT` interaction — crossover or
      cap (Open question 2)
- [ ] Decide whether to also accept an explicit `granularity` override
      (Open question 3)

## Then (sketch, to be detailed after the decisions)

- [ ] `resolve_range(token, *, now)` pure helper (range → granularity + from/to)
      + unit tests (each token, `all`, retention clamp, unknown token → 400)
- [ ] Wire `range` into the chosen endpoint(s): derive bounds, route to the
      existing repo query, enforce mutual exclusion with `from`/`to`
- [ ] Add the `granularity` (+ resolved window) field to the response
- [ ] Regenerate `infra/openapi.yaml`; update the domain-model spec if the
      response model changes
- [ ] Handler tests: each `range` routes to the right granularity/source;
      `range` + `from`/`to` → 400; bare call defaults unchanged
- [ ] Gates: `ruff` + `mypy` + `pytest` + `bandit`; OpenAPI drift check
