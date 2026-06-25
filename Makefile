.PHONY: lint format typecheck test test-integration openapi postman build verify-layer deploy deploy-dev deploy-prod bastion-up bastion-down domain-up domain-down db-tunnel-up db-tunnel-down migrate migrate-lambda seed clean

STAGE ?= dev
AWS_REGION ?= us-east-1
LOCAL_DB_PORT ?= 5432

# Base CloudFormation parameter set for the additive-deploy targets below.
# A CLI --parameter-overrides REPLACES samconfig's set (it does not merge per
# key), so these targets must pass the full set with one flag flipped -- else a
# prod toggle would silently fall back to the Stage=dev template default.
# Defaults mirror samconfig; override on the command line when needed.
BDO_REGION ?= tw
USE_RDS_PROXY ?= false
BASE_PARAMS := Stage=$(STAGE) BdoRegion=$(BDO_REGION) UseRdsProxy=$(USE_RDS_PROXY)

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

deploy: verify-layer
	sam deploy --config-env dev

deploy-dev: verify-layer
	sam deploy --config-env dev

deploy-prod: verify-layer
	sam deploy --config-env prod

# --- Additive redeploys (opt-in infra toggles) ----------------------------
# Each depends on `build`, so verify-layer always gates the deploy: an additive
# toggle can never republish a source-only CommonLayer (the cause of the
# "No module named 'aws_lambda_powertools'" init failures). Pass STAGE=prod
# (and the domain vars) on the command line as needed. Changeset confirmation
# is governed by samconfig (confirm_changeset=true), so these prompt before
# applying -- consistent with deploy-dev/deploy-prod.

# Add the bastion + EICE for DBA access (ADR-0009).
bastion-up: build
	sam deploy --config-env $(STAGE) \
		--parameter-overrides "$(BASE_PARAMS) EnableBastion=true"

# Remove the bastion once you're done (saves the t4g.nano cost).
bastion-down: build
	sam deploy --config-env $(STAGE) \
		--parameter-overrides "$(BASE_PARAMS) EnableBastion=false"

# Enable the API custom domain (ADR-0013). Requires API_DOMAIN_NAME and
# HOSTED_ZONE_ID (account-specific; kept out of committed config), e.g.:
#   make domain-up STAGE=prod API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=Z123
domain-up: build
	@test -n "$(API_DOMAIN_NAME)" || { echo "Set API_DOMAIN_NAME (e.g. api.example.com)"; exit 1; }
	@test -n "$(HOSTED_ZONE_ID)" || { echo "Set HOSTED_ZONE_ID (Route 53 zone for the parent domain)"; exit 1; }
	sam deploy --config-env $(STAGE) \
		--parameter-overrides "$(BASE_PARAMS) EnableBastion=false ApiDomainName=$(API_DOMAIN_NAME) HostedZoneId=$(HOSTED_ZONE_ID)"

# Disable the custom domain (ApiDomainName reverts to empty -> HasCustomDomain
# false), removing the cert, domain name, base-path mapping, and DNS record.
domain-down: build
	sam deploy --config-env $(STAGE) \
		--parameter-overrides "$(BASE_PARAMS) EnableBastion=false"

db-tunnel-up:
	@BASTION_ID=$$(aws ec2 describe-instances --region $(AWS_REGION) \
		--filters "Name=tag:Name,Values=bdo-$(STAGE)-bastion" \
		          "Name=instance-state-name,Values=running" \
		--query 'Reservations[0].Instances[0].InstanceId' --output text); \
	if [ -z "$$BASTION_ID" ] || [ "$$BASTION_ID" = "None" ]; then \
		echo "No running bastion for stage '$(STAGE)'. Deploy with EnableBastion=true."; exit 1; fi; \
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

seed:
	uv run python scripts/seed_items.py

clean:
	rm -rf .aws-sam/ build/ dist/ *.egg-info
