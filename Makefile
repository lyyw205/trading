.PHONY: test test-unit test-integration test-all test-cov lint format install dev-install clean

# Testing
test:
	pytest -m "not slow"

test-unit:
	pytest -m unit

test-integration:
	pytest -m integration

test-all:
	pytest --cov=app --cov-report=term-missing

test-cov:
	pytest --cov=app --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

# Linting
lint:
	ruff check app/ tests/

format:
	ruff format app/ tests/

format-check:
	ruff format --check app/ tests/

# Installation
install:
	pip install -e .

dev-install:
	pip install -e ".[dev]"
	pre-commit install

# Database
test-db-up:
	docker compose -f docker/docker-compose.yml --profile test up -d test-db

test-db-down:
	docker compose -f docker/docker-compose.yml --profile test down

# Cleanup
clean:
	rm -rf htmlcov .coverage .pytest_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
