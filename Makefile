.PHONY: lint format typecheck test build deploy deploy-dev deploy-prod db-tunnel-up db-tunnel-down clean

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
	sam deploy

deploy-dev:
	sam deploy --config-env dev

deploy-prod:
	sam deploy --config-env prod

db-tunnel-up:
	@echo "TODO: Start IAM-authenticated SSH tunnel via EC2 Instance Connect Endpoint to RDS:5432"

db-tunnel-down:
	@echo "TODO: Stop IAM-authenticated SSH tunnel via EC2 Instance Connect Endpoint to RDS:5432"

clean:
	rm -rf .aws-sam/ build/ dist/ *.egg-info
