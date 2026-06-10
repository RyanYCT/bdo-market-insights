# 13. REST API custom domain via ACM + Route 53

## Status

Accepted

## Context

The API is reached through the generated API Gateway `execute-api` URL, which
is opaque and changes if the API is recreated. A stable, branded hostname is
wanted (e.g. `api.example.com`), consistent across redeploys and per
environment.

DNS for the parent domain (`example.com`) is hosted in Route 53 and is **shared
infrastructure** used by multiple services (frontend, docs, this API), each
owned by its own stack/repo.

Naming follows `{service}.{env}.example.com`, with prod omitting the env label:

| Service | prod | dev |
|---------|------|-----|
| API (this repo) | `api.example.com` | `api.dev.example.com` |
| Frontend | `www.example.com` | `www.dev.example.com` |
| Docs | `docs.example.com` | `docs.dev.example.com` |

Env-as-parent-label (`api.dev.example.com`, not `dev.api.example.com`) is the
common cloud convention: it allows delegating an entire `dev.example.com`
subtree to a separate account, scoping a single `*.dev.example.com` wildcard
cert per tier, and grouping non-prod blast radius under one label.

## Decision

Add an **optional, parameter-driven** custom domain to the API stack
(`infra/api.yaml`), gated on a `HasCustomDomain` condition (true when
`ApiDomainName` is non-empty):

- `AWS::CertificateManager::Certificate` — **regional**, **DNS-validated**
  through the Route 53 hosted zone (CloudFormation writes the validation record
  and waits for issuance).
- `AWS::ApiGateway::DomainName` — `REGIONAL`, `SecurityPolicy: TLS_1_2`.
- `AWS::ApiGateway::BasePathMapping` — maps the domain root to the deployed
  stage (routes already carry the `/v1` prefix, so the base path is empty).
- `AWS::Route53::RecordSet` — an A-alias from the hostname to the regional
  domain target.

The hosted zone is **referenced by ID** (`HostedZoneId` parameter) and never
created or modified beyond this stack's own record — ownership stays with the
shared-infra stack. Per-stage hostnames are supplied at deploy time and are not
stored in committed config; the parameters default to empty so a plain deploy
builds no domain resources.

Raw resources are used rather than the SAM `Domain:` property because the
latter cannot be conditionally toggled, and the domain must be opt-in per stage.

## Consequences

- Stable, branded URL per environment; the `execute-api` URL still works.
- The first deploy that sets a domain blocks for a few minutes on ACM DNS
  validation.
- The hosted zone is an external dependency passed in as a parameter; if it is
  unavailable or the ID is wrong, the cert/record fail to create.
- `REGIONAL` keeps the cert in the API's own region (no edge/CloudFront path).
- Adding/removing the domain later is a parameter change, not a code change.
