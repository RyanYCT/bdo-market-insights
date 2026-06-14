# Cleanup Tasks — Legacy Resource Decommission

Tracking list for retiring the **pre-v3** AWS resources (the original
`main`/v1 deployment and the abandoned `rewrite-project`/v2 attempt) once the
v3 stack (`redesign-v3`) has cut over and soaked in production.

> **Status: COMPLETE (2026-06-09).** The legacy pre-v3 decommission was
> carried out and signed off; the v3 stacks (`bdo-market-dev` /
> `bdo-market-prod`) are the only live deployment. This document is retained
> as the **process record and reusable template** — the discovery commands and
> the staged disable→observe→delete procedure below stand on their own for any
> future decommission. Per repo policy, concrete legacy resource
> names/ARNs/account-specific inventory are **not** recorded here; that detail
> was tracked privately during the cleanup. The blank tables below are left
> intentionally empty.

> **This was the destructive tail of Phase 7.** The preconditions below were
> required before starting it (all were met before the work was done):
>
> 1. `bdo-market-prod` deployed and soaked (per `tasks.md` Phase 7), and
> 2. `main` force-replaced from `redesign-v3`.
>
> Each item below required **explicit, individual sign-off** before deletion.
> Deletions are irreversible; the staged approach in
> [Decommission procedure](#decommission-procedure) (disable → observe →
> delete) was preferred over deleting in place.

## How to use this list

1. Run the [discovery commands](#discovery-inventory-do-this-first) to produce
   the real account inventory — **do not delete from guessed names.**
2. Fill the **Resource (actual name/ARN)** column from that output.
3. For each row: confirm it is **not** referenced by any `bdo-market-dev` /
   `bdo-market-prod` resource, set **Decision**, get **Signed off by**, then
   delete and tick **Done**.

### What is v3 (KEEP — never in scope here)

These belong to the live v3 stacks and must be preserved:

- CloudFormation stacks `bdo-market-dev`, `bdo-market-prod` (+ their nested
  stacks).
- DynamoDB table **`bdo-${Stage}-items`** (`bdo-dev-items`, `bdo-prod-items`).
- Everything named `bdo-<stage>-*` (e.g. `bdo-dev-*`, `bdo-prod-*`):
  Lambdas (`bdo-<stage>-migrator`, the 8 functions), the `bdo-common` layer,
  `bdo-<stage>-alarms` SNS topic, `bdo-<stage>-dba-credentials` secret, the
  `bdo-<stage>` dashboard, VPC/subnets/SGs created by `infra/network.yaml`,
  and the v3 RDS instance + DynamoDB created by `infra/data.yaml`.

When in doubt, check the resource's `aws:cloudformation:stack-name` tag — if it
points at a `bdo-market-*` stack, **keep it.**

## Discovery inventory (do this first)

Read-only. Paste results back into the table, then fill the **Resource**
column. Adjust the `bdo`/`bdo.` name filters to whatever the v1 naming
actually was.

> **Sweep more than one region.** v3 runs in `us-east-1`, but v1 almost
> certainly ran in **`ap-northeast-1`** — this repo's region was corrected
> *from* `ap-northeast-1` to `us-east-1` during the v3 bring-up
> (commit `1c5f554`). If you only scan `us-east-1` the legacy inventory may
> come back empty and look "already clean." Run the regional block below for
> **each** of `us-east-1` and `ap-northeast-1` (add any others v1 may have
> used). IAM is global — run that block once.

```sh
# --- Run once PER region -------------------------------------------------
for REGION in us-east-1 ap-northeast-1; do
echo "================ REGION: $REGION ================"

# CloudFormation: separate v3 (keep) from any legacy stacks (candidates)
aws cloudformation describe-stacks --region "$REGION" \
  --query "Stacks[].StackName" --output text | tr '\t' '\n'

# DynamoDB tables — expect bdo-<stage>-items (KEEP) + legacy bdo.accessory (candidate)
aws dynamodb list-tables --region "$REGION" --output text

# RDS instances AND Aurora clusters — keep the v3 one (tagged to bdo-market-*)
aws rds describe-db-instances --region "$REGION" \
  --query "DBInstances[].[DBInstanceIdentifier,Engine,DBInstanceStatus]" --output text
aws rds describe-db-clusters --region "$REGION" \
  --query "DBClusters[].[DBClusterIdentifier,Engine,Status]" --output text

# Lambda functions NOT prefixed bdo-dev-/bdo-prod- (i.e. legacy candidates).
# Negation is done with grep, not JMESPath '!', to avoid bash history expansion.
aws lambda list-functions --region "$REGION" \
  --query "Functions[].FunctionName" --output text \
  | tr '\t' '\n' | grep -vE '^bdo-(dev|prod)-' || true

# EventBridge: default bus rules + any custom buses (which may carry their own rules)
aws events list-rules --region "$REGION" --query "Rules[].Name" --output text
aws events list-event-buses --region "$REGION" --query "EventBuses[].Name" --output text

# API Gateway: both REST (v1) and HTTP/WebSocket (v2) APIs
aws apigateway get-rest-apis --region "$REGION" \
  --query "items[].[name,id]" --output text
aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[].[Name,ApiId,ProtocolType]" --output text

done

# --- Run ONCE (IAM is global, no region) ---------------------------------
# Review each: the 'bdo' filter also surfaces v3's NAMED roles — exclude
# anything tagged to a bdo-market-* stack (see ownership check below).
aws iam list-users  --query "Users[?contains(UserName,'bdo')].UserName" --output text
aws iam list-roles  --query "Roles[?contains(RoleName,'bdo')].RoleName" --output text
aws iam list-policies --scope Local \
  --query "Policies[?contains(PolicyName,'bdo')].[PolicyName,Arn]" --output text
```

For any candidate, confirm ownership before acting:

```sh
# Is this resource part of a v3 stack? (example: a DynamoDB table)
aws dynamodb list-tags-of-resource --region "$REGION" \
  --resource-arn <arn> \
  --query "Tags[?Key=='aws:cloudformation:stack-name']"
```

## Decommission candidates

Legacy / pre-v3 only. Fill actual names from discovery; one sign-off per row.

### Data stores

| # | Resource (actual name/ARN) | What it is | Decision | Signed off by | Done |
|---|----------------------------|------------|----------|---------------|------|
| D1 | `bdo.accessory` (DynamoDB) | v1 item table; v3 seed source (`scripts/seed_items.py`). Delete only after confirming the v3 `bdo-prod-items` table is fully populated and authoritative. |  |  | [ ] |
| D2 | _legacy RDS instance_ | v1 Postgres/MySQL (if any). **Take a final snapshot before deletion.** |  |  | [ ] |
| D3 | _other legacy DynamoDB tables_ | any v1/v2 tables surfaced by discovery |  |  | [ ] |

### Compute & orchestration

| # | Resource (actual name/ARN) | What it is | Decision | Signed off by | Done |
|---|----------------------------|------------|----------|---------------|------|
| C1 | _legacy Lambda functions_ | v1/v2 functions not prefixed `bdo-<stage>-` |  |  | [ ] |
| C2 | _legacy EventBridge rules_ | old ETL schedules/triggers |  |  | [ ] |
| C3 | _legacy API Gateway API(s)_ | old REST/HTTP APIs + usage plans/keys |  |  | [ ] |
| C4 | _legacy Step Functions_ | any v1/v2 state machines |  |  | [ ] |

### IAM (review carefully — shared principals may exist)

| # | Resource (actual name/ARN) | What it is | Decision | Signed off by | Done |
|---|----------------------------|------------|----------|---------------|------|
| I1 | _legacy IAM users_ | static-key users from v1 (rotate/remove access keys first) |  |  | [ ] |
| I2 | _legacy IAM roles_ | v1/v2 Lambda/execution roles not owned by `bdo-market-*` |  |  | [ ] |
| I3 | _legacy customer-managed policies_ | orphaned policies after I1/I2 |  |  | [ ] |

### Networking & storage leftovers

| # | Resource (actual name/ARN) | What it is | Decision | Signed off by | Done |
|---|----------------------------|------------|----------|---------------|------|
| N1 | _legacy VPC / subnets / SGs / NAT_ | old network not owned by `infra/network.yaml` (a NAT GW here is a live cost — prioritise) |  |  | [ ] |
| N2 | _legacy S3 buckets_ | old deploy/artifact buckets (empty before delete) |  |  | [ ] |
| N3 | _orphaned CloudWatch log groups_ | `/aws/lambda/<legacy-fn>`, etc. |  |  | [ ] |
| N4 | _old SAM/CFN deploy buckets & stacks_ | abandoned v1/v2 CloudFormation stacks |  |  | [ ] |

## Decommission procedure (per resource)

Prefer staged deletion so a missed dependency is recoverable:

1. **Confirm ownership** — not tagged to a `bdo-market-*` stack (see discovery).
2. **Snapshot/back up** if it holds data (RDS final snapshot; export DynamoDB
   if it may be needed).
3. **Disable, don't delete** — IAM: detach policies / deactivate access keys.
   EventBridge: disable the rule. Lambda: leave but stop invoking. Observe for
   an agreed window (e.g. 7 days) to surface hidden dependents.
4. **Delete** after the observation window with sign-off recorded above.
5. **Verify v3 unaffected** — dashboard green, ETL succeeds, API 200s.

### Ordering

Delete dependents before dependencies: triggers/schedules (C2) →
compute (C1/C3/C4) → data (D*) → IAM (I*) → networking (N*). Detach IAM
policies before deleting roles/users.

## Notes / open questions

- Exact legacy names are intentionally blank — fill from discovery rather than
  assume. The only legacy name hard-referenced in this repo is the DynamoDB
  table **`bdo.accessory`** (`scripts/seed_items.py`).
- **Region:** v1 likely ran in `ap-northeast-1` (the region this repo was
  corrected away from in `1c5f554`); the discovery block sweeps both that and
  `us-east-1`. Confirm the actual v1 region before concluding "nothing found."
- Confirm whether v1 ran any **NAT Gateway** — it bills hourly + per-GB, so
  it is the highest-value early deletion (v3 has none, per ADR-0006).
- Branch archiving (`tag archive/main-v1`, rename `rewrite-project` →
  `archive/rewrite-project`) is tracked separately in `tasks.md` Phase 7 and is
  **not** part of this AWS-resource list.
