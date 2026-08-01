.PHONY: lint format typecheck test test-integration openapi postman build verify-layer deploy seed-config db-tunnel-up db-tunnel-down dba-password migrate migrate-lambda market-catalog track seed-catalog seed-tracked seed-data seed clean

STAGE ?= dev
AWS_REGION ?= us-east-1
LOCAL_DB_PORT ?= 5432

# Full CloudFormation parameter set for `make deploy`. The domain / hosted-zone
# / demo-key parameters are SSM-resolved (ADR-0024): we pass SSM *key paths*
# (not secrets) and CloudFormation substitutes the stored values at deploy, so a
# full-state deploy can't drop the custom domain and no account-specific host is
# committed. Seed the keys once per stage first: `make seed-config STAGE=<env>`.
BDO_REGION ?= tw
USE_RDS_PROXY ?= false
ENABLE_BASTION ?= false
DEPLOY_PARAMS := Stage=$(STAGE) BdoRegion=$(BDO_REGION) UseRdsProxy=$(USE_RDS_PROXY) EnableBastion=$(ENABLE_BASTION) EnableDemoKey=/bdo/$(STAGE)/enable-demo-key ApiDomainName=/bdo/$(STAGE)/api-domain-name IconDomainName=/bdo/$(STAGE)/icon-domain-name HostedZoneId=/bdo/shared/route53/hosted-zone-id

# Built layer artifacts (CommonLayer is nested under EtlStack).
LAYER_PYTHON := .aws-sam/build/EtlStack/CommonLayer/python

lint:
	uv run ruff check . && uv run ruff format --check .

format:
	uv run ruff format . && uv run ruff check --fix .

typecheck:
	uv run mypy src/ tests/

test:
	uv run pytest

# Requires a reachable Postgres; set TEST_DATABASE_URL (CI uses a service
# container). Skips automatically when TEST_DATABASE_URL is unset.
test-integration:
	uv run pytest -m integration

# Regenerate infra/openapi.yaml from the Powertools API handlers. CI runs the
# same export and fails if the committed spec is out of date.
openapi:
	uv run python scripts/export_openapi.py

# Regenerate the Postman collection from the same OpenAPI document. Publish the
# result to a public workspace for "try the API" links; the demo key value goes
# in the Postman environment, never in this file or the repo.
postman:
	uv run python scripts/export_postman.py

build:
	sam build
	$(MAKE) verify-layer

# Fail loudly if the built CommonLayer is missing its runtime dependencies,
# so a broken (e.g. source-only) layer can never reach `sam deploy`. This
# guards against pip silently vendoring nothing (exit 0) on an unwritable or
# Windows-mounted (/mnt/*) build filesystem. build_layer.py asserts the same
# set at build time; this is the deploy-gate backstop.
verify-layer:
	@for pkg in bdo_common aws_lambda_powertools pydantic pydantic_core psycopg; do \
		test -d "$(LAYER_PYTHON)/$$pkg" || { \
			echo "ERROR: built layer missing '$$pkg' under $(LAYER_PYTHON)"; \
			echo "Refusing to deploy a layer without its runtime dependencies."; \
			echo "Run 'make build' on a native Linux filesystem (not /mnt/*) first."; \
			exit 1; }; \
	done; \
	echo "verify-layer: CommonLayer contains its runtime dependencies."

# Single full-state deploy for any stage. `build` runs verify-layer first, so a
# source-only CommonLayer can never reach `sam deploy`. CI deploys prod the same
# way, supplying the domain from GitHub Actions variables (see docs/runbook.md).
#
#   make deploy STAGE=dev
#   make deploy STAGE=prod ENABLE_DEMO_KEY=true API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=Z123
#   make deploy STAGE=prod ENABLE_BASTION=true ENABLE_DEMO_KEY=true API_DOMAIN_NAME=... HOSTED_ZONE_ID=...
#
# The bastion is a transient toggle (bring it up for a DBA session, then deploy
# again with ENABLE_BASTION=false). Because the whole state is declared each
# time, keep the persistent flags (demo key, domain) in the command -- exporting
# the domain vars once per shell makes that a non-issue.
deploy: build
	sam deploy --config-env $(STAGE) --parameter-overrides "$(DEPLOY_PARAMS)"

# One-time (or on change) per stage: write the deploy config into SSM so the
# SSM-resolved template params (ADR-0024) can be substituted at deploy. Values
# default to "none"/"false" (no custom domain); pass real ones to publish them.
# The hosted zone id is looked up from Route 53 by PARENT_DOMAIN (or pass
# HOSTED_ZONE_ID directly). Run once before the first `make deploy STAGE=<env>`.
#
#   make seed-config STAGE=dev
#   make seed-config STAGE=prod API_DOMAIN_NAME=api.example.com \
#       ICON_DOMAIN_NAME=icons.example.com PARENT_DOMAIN=example.com ENABLE_DEMO_KEY=true
seed-config:
	@set -e; \
	api="$${API_DOMAIN_NAME:-none}"; icon="$${ICON_DOMAIN_NAME:-none}"; demo="$${ENABLE_DEMO_KEY:-false}"; \
	put() { aws ssm put-parameter --region $(AWS_REGION) --overwrite --type String --name "$$1" --value "$$2" >/dev/null; echo "  $$1 = $$2"; }; \
	echo "seeding SSM deploy config (stage=$(STAGE), region=$(AWS_REGION)):"; \
	put "/bdo/$(STAGE)/api-domain-name"  "$$api"; \
	put "/bdo/$(STAGE)/icon-domain-name" "$$icon"; \
	put "/bdo/$(STAGE)/enable-demo-key"  "$$demo"; \
	zone="$${HOSTED_ZONE_ID:-}"; \
	if [ -z "$$zone" ] && [ -n "$${PARENT_DOMAIN:-}" ]; then \
	  zone=$$(aws route53 list-hosted-zones-by-name --dns-name "$$PARENT_DOMAIN" --max-items 1 \
	    --query 'HostedZones[0].Id' --output text | sed 's#/hostedzone/##'); \
	fi; \
	put "/bdo/shared/route53/hosted-zone-id" "$${zone:-none}"

db-tunnel-up:
	@BASTION_ID=$$(aws ec2 describe-instances --region $(AWS_REGION) \
		--filters "Name=tag:Name,Values=bdo-$(STAGE)-bastion" \
		          "Name=instance-state-name,Values=running" \
		--query 'Reservations[0].Instances[0].InstanceId' --output text); \
	if [ -z "$$BASTION_ID" ] || [ "$$BASTION_ID" = "None" ]; then \
		echo "No running bastion for stage '$(STAGE)'. Deploy with 'make deploy STAGE=$(STAGE) ENABLE_BASTION=true' (plus the stage's persistent flags)."; exit 1; fi; \
	RDS_ENDPOINT=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--query "Stacks[?starts_with(StackName,'bdo-market-$(STAGE)')].Outputs[] | [?OutputKey=='RdsEndpoint'].OutputValue | [0]" \
		--output text); \
	if [ -z "$$RDS_ENDPOINT" ] || [ "$$RDS_ENDPOINT" = "None" ]; then \
		echo "Could not resolve RdsEndpoint output from stack 'bdo-market-$(STAGE)'. Is the stack deployed?"; exit 1; fi; \
	echo "Tunnel: localhost:$(LOCAL_DB_PORT) -> $$RDS_ENDPOINT:5432 via $$BASTION_ID (Ctrl-C to close)"; \
	aws ec2-instance-connect ssh --instance-id $$BASTION_ID --region $(AWS_REGION) \
		--connection-type eice --local-forwarding "$(LOCAL_DB_PORT):$$RDS_ENDPOINT:5432"

db-tunnel-down:
	@pkill -f "ec2-instance-connect ssh" && echo "Tunnel closed." || echo "No active tunnel."

# Re-sync the dba role password to the current dba secret (ADR-0020). The dba
# secret exists only while the bastion is up and gets a fresh password each
# time, so run this once per bastion session (after make db-tunnel-up) to let
# pgAdmin log in as dba. Requires an open tunnel + ENABLE_BASTION=true.
# Kept separate from `make deploy` by necessity: it needs a live DB connection
# over the tunnel, which needs a bastion that only exists after the deploy
# finishes -- so it is inherently a post-deploy step (and a no-op on the common
# ENABLE_BASTION=false deploy, where no dba secret exists).
dba-password:
	uv run python scripts/set_dba_password.py --stage $(STAGE) --region $(AWS_REGION) \
		--port $(LOCAL_DB_PORT)

# Requires an open tunnel (make db-tunnel-up) and DATABASE_URL pointing at
# localhost:$(LOCAL_DB_PORT). See docs/runbook.md for the full flow.
migrate:
	uv run alembic -c migrations/alembic.ini upgrade head

# Routine schema changes: invoke the in-VPC migrator Lambda (runs
# `alembic upgrade head` from inside the VPC via IAM auth). No tunnel needed.
# The one-time role bootstrap (0001-0003) still uses `make migrate` via the
# bastion as the master user -- see docs/runbook.md.
migrate-lambda:
	@aws lambda invoke --region $(AWS_REGION) \
		--function-name bdo-$(STAGE)-migrator \
		--cli-binary-format raw-in-base64-out --payload '{}' \
		/tmp/bdo-$(STAGE)-migrate.json >/dev/null && \
		cat /tmp/bdo-$(STAGE)-migrate.json && echo

# Regenerate the offline market snapshot (scripts/data/full_items.json) by
# enumerating the arsha.io market taxonomy. This is the ONLY step that calls
# arsha; run it occasionally (e.g. after a BDO patch adds items), then commit.
market-catalog:
	uv run python scripts/build_market_catalog.py

# Interactive, preset-driven track selection -> scripts/data/tracked_items.json.
# For scripted use call the script directly (e.g. --preset accessories --out ...);
# broad selections are guarded (need confirmation or --force). Fully offline.
track:
	uv run python scripts/select_tracked.py

# Full item catalog backfill (id/name/grade from arsha util/db) into the table.
seed-catalog:
	uv run python scripts/seed_catalog.py --target-table bdo-$(STAGE)-items

# Seed the tracked set from tracked_items.json + the committed snapshot (offline,
# no arsha). Add RECONCILE=1 to also untrack items no longer in the list.
seed-tracked:
	uv run python scripts/seed_items.py --target-table bdo-$(STAGE)-items $(if $(RECONCILE),--reconcile,)

# Rebuild all DynamoDB item data in the correct order: catalog first (so
# names/grades exist), then the tracked set. This is the "seed" entry point.
seed-data: seed-catalog seed-tracked

# Back-compat alias. Was tracked-only (a footgun: seeding the tracked set before
# the catalog left items without name/grade). Now runs the full ordered rebuild.
seed: seed-data

clean:
	rm -rf .aws-sam/ build/ dist/ *.egg-info
