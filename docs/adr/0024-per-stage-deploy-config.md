# ADR-0024: Per-stage deploy config via a gitignored `deploy.<stage>.env`

## Status

Accepted

## Context

`make deploy` runs `sam deploy --parameter-overrides`, which **replaces the
whole parameter set** — CloudFormation reverts any parameter not passed to its
template default, not its previous value. So every deploy must declare the
complete desired state.

The account-specific, environment-bound inputs (custom domains, the Route 53
hosted zone id, the demo-key toggle) must **not** be committed (public repo;
§4d / ADR-0013). Until now they were passed ad-hoc on the command line (or
exported per shell). Two problems:

- **Footgun:** a full-state `make deploy STAGE=prod` that forgets the domain
  vars silently **tears down** the custom-domain resources (cert, alias) — the
  most likely mistake is the most destructive.
- The custom domain is effectively a stable property of an environment (prod
  especially), yet nothing persisted it with that environment.

## Decision

Persist per-stage deploy config in a **gitignored `deploy.<stage>.env`** that
the Makefile auto-sources:

```make
-include deploy.$(STAGE).env
```

- Holds `HOSTED_ZONE_ID`, `API_DOMAIN_NAME`, `ICON_DOMAIN_NAME` (ADR-0023),
  `ENABLE_DEMO_KEY`, etc. `make deploy STAGE=<stage>` reads it, so the full-state
  deploy always carries the environment's domain instead of dropping it.
- Precedence: command line > `deploy.<stage>.env` > shell env > template default
  (empty). One-offs (e.g. `ENABLE_BASTION=true`) still work on the CLI.
- `deploy.*.env` is gitignored; a committed `deploy.env.example` documents the
  keys. Real hosts never enter the repo.
- **CI is unaffected:** the file is absent in CI (`-include` skips it), and the
  prod release job injects these from GitHub secrets/variables via the
  environment.
- Also adds `IconDomainName` to the deploy parameter set so the icons CDN custom
  domain (ADR-0023) is wirable the same way as the API domain.

## Consequences

- (+) Full-state deploys are safe: the custom domain is bound to the environment,
  not to remembering CLI flags. Removes the teardown footgun.
- (+) One place per stage for domain/zone/toggle config; API + icons domains
  handled uniformly.
- (+) No account-specific hosts in the repo; CI path unchanged.
- (−) Operators must create `deploy.<stage>.env` once locally (documented in the
  runbook + `deploy.env.example`); a missing file falls back to template
  defaults (no custom domain), which is safe but not always intended.

## Notes

Mechanism lives in the `Makefile` (`-include`, `DEPLOY_PARAMS`) and
`deploy.env.example`; the runbook "Deployment notes" documents it. Domains
themselves are decided by ADR-0013 (API) and ADR-0023 (icons).
