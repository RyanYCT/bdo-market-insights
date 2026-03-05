.PHONY: help install lint test test-unit test-property test-integration test-all coverage clean format security

help:
	@echo "BDO Market Insights - Development Commands"
	@echo ""
	@echo "Available targets:"
	@echo "  install           Install development dependencies"
	@echo "  lint              Run all linting checks (black, flake8, mypy)"
	@echo "  format            Auto-format code with black"
	@echo "  security          Run security scan with bandit"
	@echo "  test-unit         Run unit tests"
	@echo "  test-property     Run property-based tests"
	@echo "  test-integration  Run integration tests"
	@echo "  test-all          Run all tests"
	@echo "  coverage          Run tests with coverage report"
	@echo "  clean             Remove build artifacts and cache files"

install:
	pip install -r requirements-dev.txt

lint: lint-black lint-flake8 lint-mypy

lint-black:
	@echo "Running Black (code formatting check)..."
	black --check --diff .

lint-flake8:
	@echo "Running Flake8 (linting)..."
	flake8 .

lint-mypy:
	@echo "Running MyPy (type checking)..."
	mypy lambda_layer/python/common/ --ignore-missing-imports
	mypy retrieveIdList/lambda_function.py --ignore-missing-imports
	mypy fetchData/lambda_function.py --ignore-missing-imports
	mypy cleanData/lambda_function.py --ignore-missing-imports
	mypy storeData/lambda_function.py --ignore-missing-imports
	mypy queryData/lambda_function.py --ignore-missing-imports
	mypy analyzeData/lambda_function.py --ignore-missing-imports
	mypy retainData/lambda_function.py --ignore-missing-imports

format:
	@echo "Formatting code with Black..."
	black .

security:
	@echo "Running Bandit (security scan)..."
	bandit -r lambda_layer/python/common/ -ll
	bandit -r retrieveIdList/ -ll
	bandit -r fetchData/ -ll
	bandit -r cleanData/ -ll
	bandit -r storeData/ -ll
	bandit -r queryData/ -ll
	bandit -r analyzeData/ -ll
	bandit -r retainData/ -ll

test-unit:
	@echo "Running unit tests..."
	pytest tests/unit/ -v

test-property:
	@echo "Running property-based tests..."
	HYPOTHESIS_PROFILE=dev pytest tests/property/ --hypothesis-show-statistics -v

test-integration:
	@echo "Running integration tests..."
	pytest tests/integration/ -v

test-all:
	@echo "Running all tests..."
	pytest tests/ -v

coverage:
	@echo "Running tests with coverage..."
	pytest tests/unit/ \
		--cov=lambda_layer/python/common \
		--cov=retrieveIdList \
		--cov=fetchData \
		--cov=cleanData \
		--cov=storeData \
		--cov=queryData \
		--cov=analyzeData \
		--cov=retainData \
		--cov-report=html \
		--cov-report=term \
		--cov-fail-under=80 \
		-v
	@echo "Coverage report generated in htmlcov/index.html"

clean:
	@echo "Cleaning build artifacts and cache files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	rm -rf htmlcov/
	rm -rf build/
	rm -rf dist/
	rm -rf *.zip
	@echo "Clean complete!"
