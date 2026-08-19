# Beelieve dev workflow. `make help` lists targets.

COMPOSE := docker compose
TEST_SERVICES := ingestion simulator stream-processor

.PHONY: help up dev down logs ps test lint typecheck loadtest clean

help:
	@echo "up     - build and start the full stack"
	@echo "dev    - full stack plus the hive simulator"
	@echo "down   - stop everything"
	@echo "logs   - tail service logs"
	@echo "ps     - show container status"
	@echo "test   - run all service test suites"
	@echo "lint   - ruff over services/"
	@echo "clean  - remove caches"

up:
	$(COMPOSE) up -d --build

dev: up
	$(COMPOSE) --profile dev up -d hive-simulator

down:
	$(COMPOSE) --profile dev down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test:
	@set -e; for s in $(TEST_SERVICES); do \
		echo "== services/$$s"; \
		( cd services/$$s && python -m pytest -q ); \
	done

lint:
	ruff check services scripts

typecheck:
	cd services/ingestion && mypy
	cd services/simulator && mypy

loadtest:
	python scripts/loadtest.py --hives 50 --rate 200 --duration 30

clean:
	find . -type d \( -name __pycache__ -o -name .pytest_cache -o -name .ruff_cache \) -prune -exec rm -rf {} +
