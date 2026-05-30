.PHONY: lint format typecheck test build deploy deploy-dev deploy-prod db-tunnel-up db-tunnel-down migrate seed clean

STAGE ?= dev
AWS_REGION ?= ap-northeast-1
LOCAL_DB_PORT ?= 5432

lint:
	uv run ruff check . && uv run ruff format --check .

format:
	uv run ruff format . && uv run ruff check --fix .

typecheck:
	uv run mypy src/ tests/

test:
	uv run pytest

build:
	sam build

deploy:
	sam deploy --config-env dev

deploy-dev:
	sam deploy --config-env dev

deploy-prod:
	sam deploy --config-env prod

db-tunnel-up:
	@BASTION_ID=$$(aws ec2 describe-instances --region $(AWS_REGION) \
		--filters "Name=tag:Name,Values=bdo-$(STAGE)-bastion" \
		          "Name=instance-state-name,Values=running" \
		--query 'Reservations[0].Instances[0].InstanceId' --output text); \
	if [ -z "$$BASTION_ID" ] || [ "$$BASTION_ID" = "None" ]; then \
		echo "No running bastion for stage '$(STAGE)'. Deploy with EnableBastion=true."; exit 1; fi; \
	RDS_ENDPOINT=$$(aws cloudformation describe-stacks --region $(AWS_REGION) \
		--query "Stacks[?starts_with(StackName,'bdo-$(STAGE)')].Outputs[?OutputKey=='RdsEndpoint'].OutputValue | [][0]" \
		--output text); \
	echo "Tunnel: localhost:$(LOCAL_DB_PORT) -> $$RDS_ENDPOINT:5432 via $$BASTION_ID (Ctrl-C to close)"; \
	aws ec2-instance-connect ssh --instance-id $$BASTION_ID --region $(AWS_REGION) \
		--connection-type eice -- -N -L $(LOCAL_DB_PORT):$$RDS_ENDPOINT:5432

db-tunnel-down:
	@pkill -f "ec2-instance-connect ssh" && echo "Tunnel closed." || echo "No active tunnel."

# Requires an open tunnel (make db-tunnel-up) and DATABASE_URL pointing at
# localhost:$(LOCAL_DB_PORT). See docs/runbook.md for the full flow.
migrate:
	uv run alembic -c migrations/alembic.ini upgrade head

seed:
	uv run python scripts/seed_items.py

clean:
	rm -rf .aws-sam/ build/ dist/ *.egg-info
