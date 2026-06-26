.PHONY: lint format typecheck test test-integration openapi postman build verify-layer deploy db-tunnel-up db-tunnel-down migrate migrate-lambda seed clean

STAGE ?= dev
AWS_REGION ?= us-east-1
LOCAL_DB_PORT ?= 5432

# Full CloudFormation parameter set for `make deploy`. CloudFormation reverts
# any *unspecified* parameter to its template default (not its previous value),
# and `sam deploy --parameter-overrides` replaces the whole set rather than
# merging -- so every deploy must declare the COMPLETE desired state. These
# variables assemble it; override any on the command line or via the shell
# environment. Export the (account-specific, never-committed) domain vars once
# per shell so repeated deploys keep the custom domain:
#   export API_DOMAIN_NAME=api.example.com HOSTED_ZONE_ID=Z123
BDO_REGION ?= tw
USE_RDS_PROXY ?= false
ENABLE_BASTION ?= false
ENABLE_DEMO_KEY ?= false
API_DOMAIN_NAME ?=
HOSTED_ZONE_ID ?=
DEPLOY_PARAMS := Stage=$(STAGE) BdoRegion=$(BDO_REGION) UseRdsProxy=$(USE_RDS_PROXY) EnableBastion=$(ENABLE_BASTION) EnableDemoKey=$(ENABLE_DEMO_KEY) ApiDomainName=$(API_DOMAIN_NAME) HostedZoneId=$(HOSTED_ZONE_ID)

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
