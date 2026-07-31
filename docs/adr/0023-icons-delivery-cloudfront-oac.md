# ADR-0023: Serve item icons via CloudFront + Origin Access Control

## Status

Accepted

## Context

`iconSync` materializes item icons into the **private** `bdo-<stage>-icons`
bucket (ADR-0019), and the item registry exposes an `icon_url` of
`{ICON_BASE_URL}/icons/<id>.png` (ADR-0021). But there was no public delivery
path — the bucket blocks all public access — so `ICON_BASE_URL` had no value to
point at and `icon_url` stayed `null`. This ADR adds that delivery path.

Constraints:

- The bucket must **stay private** (block-public-access on); we do not want to
  expose it directly or enable S3 website hosting.
- Icons are small, immutable-ish static objects (the materializer already sets
  `Cache-Control: public, max-age=604800`), so they cache well at an edge.
- Cost posture: no always-on spend; a branded hostname is optional.
- The stack deploys in **us-east-1** (samconfig), which is where CloudFront
  requires its ACM certificate — so an in-stack cert works for a custom domain.

## Decision

Add a **CloudFront distribution with Origin Access Control (OAC)** in front of
the private icons bucket, in `infra/icons.yaml`:

- **OAC (SigV4, `always`)** + an `S3::BucketPolicy` granting `s3:GetObject` to
  the `cloudfront.amazonaws.com` service principal, scoped by
  `AWS:SourceArn = <this distribution>`. The bucket keeps block-public-access on
  (the policy is principal-scoped, not public), so only this distribution can
  read it.
- **Managed `CachingOptimized` cache policy**, `redirect-to-https`, `GET`/`HEAD`
  only, `PriceClass_100` (NA+EU edges — cheapest), HTTP/2+3. Origin is the
  bucket's regional domain; object keys are `icons/<id>.png`, matching the API's
  `icon_url` path.
- **Custom domain is opt-in** (mirrors ADR-0013): when `IconDomainName` is set,
  add a DNS-validated ACM cert (us-east-1) + `Aliases` + a Route 53 A-alias to
  the distribution; when empty, serve on the default `*.cloudfront.net` domain
  with the CloudFront default certificate.
- The stack **outputs `IconBaseUrl`** (the custom domain when set, else the
  distribution domain). `template.yaml` wires that output into the API stack's
  `ICON_BASE_URL`, so `icon_url` resolves automatically once deployed — no
  manual base-URL parameter.

## Consequences

- (+) `icon_url` becomes a real, cacheable, HTTPS URL end-to-end while the bucket
  stays private. Edge caching offloads the bucket; no always-on cost.
- (+) Custom hostname is a per-stage parameter change, not a code change; a plain
  deploy works on the CloudFront default domain.
- (−) The API stack now depends on the icons stack output, so a first deploy
  waits on the distribution create (~15-20 min) and the API stack re-evaluates
  if the icon base changes. Acceptable for an occasional deploy.
- (−) A CloudFront distribution + OAC + bucket policy is more moving parts than
  a bare bucket; teardown ordering is handled by CloudFormation (distribution
  before bucket) and the existing non-prod janitor (ADR-0019) still empties the
  bucket first.

## Notes

Builds on ADR-0019 (icons bucket) and ADR-0021 (the `icon_url` field). Icons are
served under `<IconBaseUrl>/icons/<id>.png`. The API caps out at what the icon
materializer has stored (`icon_status == "stored"`); others remain `null`.
