.PHONY: lint format typecheck test test-integration openapi postman build verify-layer deploy verify seed-config break-glass-up break-glass-down db-bootstrap db-admin migrate migrate-lambda market-catalog track seed-catalog seed-tracked seed-data seed bootstrap clean

STAGE ?= dev
AWS_REGION ?= us-east-1
LOCAL_DB_PORT ?= 5432

# Release version surfaced by GET /v1/meta (ADR-0037). CI passes the release
# tag; local/dev deploys mark it with git describe (or "dev" outside a repo).
API_VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)

# Command-line overrides of the stage-static parameter set. EMPTY by default, on
# purpose: an empty value contributes nothing to DEPLOY_PARAMS, so the value
# samconfig.toml declares for the stage is what deploys. Setting one on the
# command line is what makes it win --
#
#   make deploy STAGE=<env> AUTO_MIGRATE=false VERIFY=false
#
# is step 2 of a first-time bring-up (ADR-0025's two-phase migration
# introduction, docs/runbook.md), so these overrides MUST keep working. They are
# honoured by having the reader replace the key in the set it emits, not by
# appending a second `Key=` later in the --parameter-overrides string: which of
# two duplicates SAM honours is undocumented, and a silently-ignored
# AUTO_MIGRATE=false would break a fresh environment's bring-up.
USE_RDS_PROXY ?=
AUTO_MIGRATE ?=
AUTO_BOOTSTRAP ?=
BDO_REGIONS ?=
# Post-deploy smoke test (ADR-0029). VERIFY=false skips it at the end of a
# deploy; VERIFY_WAIT caps the execution-aware wait for the async bootstrap to
# populate data (it returns as soon as the bootstrap execution completes).
VERIFY ?= true
VERIFY_WAIT ?= 1200

# Content hash of the migration set. Passed to CloudFormation as
# MigrationsFingerprint; when it changes, the auto-migrate custom resource
# re-runs `alembic upgrade head` on deploy (ADR-0025). A no-op deploy (same
# migrations) leaves it unchanged, so the migrator is not re-invoked.
MIGRATIONS_FINGERPRINT := $(shell find migrations/versions -type f -name '*.py' -exec sha256sum {} \; | sort | sha256sum | cut -c1-32)

# Full CloudFormation parameter set for `make deploy`, COMPOSED from the stage's
# samconfig.toml set (Stage, BdoRegions, UseRdsProxy, AutoMigrate, AutoBootstrap
# and the four SSM-resolved key paths -- Req 2.5) plus the only two parameters
# that have no static value because they are derived at deploy time: ApiVersion
# (ADR-0037) and MigrationsFingerprint (ADR-0025). The static ones are NOT
# restated here; samconfig.toml is their single source.
#
# COMPOSITION rather than merging, because SAM offers no merge: a command-line
# `--parameter-overrides` REPLACES samconfig's list wholesale -- unspecified
# parameters revert to template.yaml defaults, not to the values the stage
# declares (aws-sam-cli#2380). So supplying even one derived value obliges the
# caller to supply every static one, and the reader is what supplies them from
# the file instead of by hand.
#
# Recursively expanded (=) so the samconfig read (a `uv run`) fires only when the
# deploy recipe references this, not at parse time for every target.
DEPLOY_OVERRIDES = ApiVersion=$(API_VERSION) MigrationsFingerprint=$(MIGRATIONS_FINGERPRINT) \
	$(if $(BDO_REGIONS),BdoRegions=$(BDO_REGIONS)) \
	$(if $(USE_RDS_PROXY),UseRdsProxy=$(USE_RDS_PROXY)) \
	$(if $(AUTO_MIGRATE),AutoMigrate=$(AUTO_MIGRATE)) \
	$(if $(AUTO_BOOTSTRAP),AutoBootstrap=$(AUTO_BOOTSTRAP))
DEPLOY_PARAMS = $(shell uv run python scripts/samconfig_regions.py --parameters $(STAGE) $(DEPLOY_OVERRIDES))

# Built layer artifacts (CommonLayer is nested under PlatformStack, ADR-0032).
LAYER_PYTHON := .aws-sam/build/PlatformStack/CommonLayer/python

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
# way, composing the same parameter set from the same samconfig.toml stage table
# (see .github/workflows/deploy.yml and docs/runbook.md).
#
#   make deploy STAGE=dev
#   make deploy STAGE=prod
#   make deploy STAGE=prod AUTO_MIGRATE=false VERIFY=false   # bring-up, ADR-0025
#
# The persistent flags (demo key, domain, regions, RDS Proxy) no longer need to
# be repeated in the command: they live in the stage's samconfig.toml set, and
# the domain / demo-key values live in SSM behind the key paths it carries
# (`make seed-config STAGE=<env>`). Pass one of the override variables above only
# to deviate from the committed set for a single deploy.
#
# `confirm_changeset = true` stays set for both stages in samconfig.toml, so this
# target still shows the changeset and waits -- an interactive operator is present
# here. (The control-plane deploy passes --no-confirm-changeset because its output
# is captured and its own plan-then-`--yes` gate is the confirmation.)
deploy: build
	sam deploy --config-env $(STAGE) --parameter-overrides "$(DEPLOY_PARAMS)"
ifneq ($(VERIFY),false)
	$(MAKE) verify STAGE=$(STAGE) AWS_REGION=$(AWS_REGION)
endif

# Post-deploy smoke test (ADR-0029): liveness (public /v1/openapi.json 200),
# RDS-backed serving (admin-query `select 1`), and data present (items table
# non-empty, waiting on the async bootstrap up to VERIFY_WAIT). Key-free and
# uniform dev/prod. Runs automatically at the end of `make deploy`; run it
# standalone anytime.
verify:
	uv run python scripts/verify.py --stage $(STAGE) --region $(AWS_REGION) --wait $(VERIFY_WAIT)

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
	put "/bdo-market-insights/$(STAGE)/domain/api-domain-name"  "$$api"; \
	put "/bdo-market-insights/$(STAGE)/domain/icon-domain-name" "$$icon"; \
	put "/bdo-market-insights/$(STAGE)/api-gateway/enable-demo-key"  "$$demo"; \
	zone="$${HOSTED_ZONE_ID:-}"; \
	if [ -z "$$zone" ] && [ -n "$${PARENT_DOMAIN:-}" ]; then \
	  zone=$$(aws route53 list-hosted-zones-by-name --dns-name "$$PARENT_DOMAIN" --max-items 1 \
	    --query 'HostedZones[0].Id' --output text | sed 's#/hostedzone/##'); \
	fi; \
	put "/bdo-market-insights/$(STAGE)/domain/hosted-zone-id" "$${zone:-none}"

# On-demand break-glass access (ADR-0027): deploy an ephemeral t4g.nano + EICE,
# then open an IAM-authenticated SSH tunnel localhost:$(LOCAL_DB_PORT) -> RDS. For
# rare DDL / bulk / master-level work only -- routine reads/fixes use `make
# db-admin`. Connect as the RDS master (resolve the master secret). Leave running
# (Ctrl-C to close the tunnel), then `make break-glass-down` to delete the host.
break-glass-up:
	@echo "Deploying on-demand break-glass stack for stage '$(STAGE)'..."; \
	SUBNET=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--query "Stacks[?starts_with(StackName,'bdo-market-$(STAGE)')].Outputs[] | [?OutputKey=='PrivateSubnetA'].OutputValue | [0]" --output text); \
	SG=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--query "Stacks[?starts_with(StackName,'bdo-market-$(STAGE)')].Outputs[] | [?OutputKey=='BreakGlassSecurityGroupId'].OutputValue | [0]" --output text); \
	if [ -z "$$SUBNET" ] || [ "$$SUBNET" = "None" ] || [ -z "$$SG" ] || [ "$$SG" = "None" ]; then \
		echo "Could not resolve PrivateSubnetA / BreakGlassSecurityGroupId from the bdo-market-$(STAGE) stacks. Is the stack deployed?"; exit 1; fi; \
	aws cloudformation deploy --region $(AWS_REGION) \
		--stack-name bdo-market-$(STAGE)-break-glass \
		--template-file infra/break-glass.yaml \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides Stage=$(STAGE) PrivateSubnetA=$$SUBNET BreakGlassSecurityGroupId=$$SG; \
	INSTANCE_ID=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--stack-name bdo-market-$(STAGE)-break-glass \
		--query "Stacks[0].Outputs[?OutputKey=='BreakGlassInstanceId'].OutputValue | [0]" --output text); \
	RDS_ENDPOINT=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--query "Stacks[?starts_with(StackName,'bdo-market-$(STAGE)')].Outputs[] | [?OutputKey=='RdsEndpoint'].OutputValue | [0]" --output text); \
	echo "Tunnel: localhost:$(LOCAL_DB_PORT) -> $$RDS_ENDPOINT:5432 via $$INSTANCE_ID (Ctrl-C to close, then 'make break-glass-down')"; \
	aws ec2-instance-connect ssh --instance-id $$INSTANCE_ID --region $(AWS_REGION) \
		--connection-type eice --local-forwarding "$(LOCAL_DB_PORT):$$RDS_ENDPOINT:5432"

break-glass-down:
	@pkill -f "[e]c2-instance-connect ssh" 2>/dev/null && echo "Tunnel closed." || echo "No active tunnel."; \
	echo "Deleting break-glass stack bdo-market-$(STAGE)-break-glass..."; \
	aws cloudformation delete-stack --region $(AWS_REGION) --stack-name bdo-market-$(STAGE)-break-glass; \
	aws cloudformation wait stack-delete-complete --region $(AWS_REGION) \
		--stack-name bdo-market-$(STAGE)-break-glass && echo "Break-glass stack deleted." \
		|| echo "Delete initiated; verify in the CloudFormation console."

# Requires an open tunnel (make break-glass-up) and DATABASE_URL pointing at
# localhost:$(LOCAL_DB_PORT). See docs/runbook.md for the full flow.
migrate:
	uv run alembic -c migrations/alembic.ini upgrade head

# Routine schema changes: invoke the in-VPC migrator Lambda (runs
# `alembic upgrade head` from inside the VPC via IAM auth). No tunnel needed.
# Normally unnecessary -- the auto-migrate custom resource runs this on deploy
# (ADR-0025); use it to force a routine migration without a full deploy.
migrate-lambda:
	@aws lambda invoke --region $(AWS_REGION) \
		--function-name bdo-$(STAGE)-migrator \
		--cli-binary-format raw-in-base64-out --payload '{}' \
		/tmp/bdo-$(STAGE)-migrate.json >/dev/null && \
		cat /tmp/bdo-$(STAGE)-migrate.json && echo

# One-time per environment: the privileged role bootstrap (migrations 0001-0003
# -- schema + cluster roles) that the IAM-authenticated migrator role cannot run
# itself. Reads the RDS-managed master credential locally and invokes the
# migrator in bootstrap mode; no bastion or tunnel (ADR-0025). Run once, after
# the first `make deploy STAGE=<env> AUTO_MIGRATE=false`, then deploy normally so
# the auto-migrate custom resource applies routine migrations (0004+).
db-bootstrap:
	uv run python scripts/db_bootstrap.py --stage $(STAGE) --region $(AWS_REGION)

# Ad-hoc SQL against RDS via the in-VPC admin-query Lambda (ADR-0026) -- replaces
# pgAdmin over the bastion. Read-only by default (statements run in a Postgres
# READ ONLY transaction); add WRITE=1 to run DML in a committing transaction.
# No tunnel/bastion; requires lambda:InvokeFunction on bdo-$(STAGE)-admin-query.
#
#   make db-admin STAGE=dev SQL='select count(*) from item'
#   make db-admin STAGE=dev SQL="delete from market_snapshot where id = 42" WRITE=1
db-admin:
	uv run python scripts/db_admin.py --stage $(STAGE) --region $(AWS_REGION) \
		--sql "$(SQL)" $(if $(WRITE),--write,)

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

# Run the in-cloud bootstrap orchestrator (ADR-0028): the Step Functions state
# machine catalog sync -> tracked seed -> icon sync, using the deployed Lambdas.
# Auto-runs once on a fresh environment's first deploy; use this to re-run it on
# demand (all steps idempotent). Contrast with `make seed*`, which are the local
# offline scripts. Resolves the state-machine ARN from the stack outputs.
bootstrap:
	@ARN=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--query "Stacks[?starts_with(StackName,'bdo-market-$(STAGE)')].Outputs[] | [?OutputKey=='BootstrapStateMachineArn'].OutputValue | [0]" \
		--output text); \
	if [ -z "$$ARN" ] || [ "$$ARN" = "None" ]; then \
		echo "Could not resolve BootstrapStateMachineArn from the bdo-market-$(STAGE) stacks. Is the stack deployed?"; exit 1; fi; \
	aws stepfunctions start-execution --region $(AWS_REGION) --state-machine-arn "$$ARN" \
		--query 'executionArn' --output text && echo "Bootstrap started for stage $(STAGE)."

clean:
	rm -rf .aws-sam/ build/ dist/ *.egg-info
