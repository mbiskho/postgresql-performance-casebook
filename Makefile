# PostgreSQL Performance Casebook
#
#   make all          clean container -> seed 64M rows -> run 10 cases -> report
#   make case CASE=05 run one case
#   make help         everything else

COMPOSE := docker compose
RUN     := $(COMPOSE) run --rm runner
SCALE   ?= 1
CASES   ?=
CASE    ?=

.PHONY: help all up down reset build seed seed-small check list run case bench report \
        observability psql slowlog smoke

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

all: reset up seed run ## Reproduce everything from a clean container (~25 min)

up: ## Start PostgreSQL 16 (port 5434)
	$(COMPOSE) up -d --build --wait postgres
	$(COMPOSE) --profile tools build runner

down: ## Stop containers, keep the data volume
	$(COMPOSE) --profile tools --profile observability down

reset: ## Stop containers and delete the data volume
	$(COMPOSE) --profile tools --profile observability down -v

build: ## Rebuild images
	$(COMPOSE) --profile tools build

seed: ## Generate the dataset (SCALE=1 is ~64M rows, ~7 GB, ~5 min)
	$(RUN) seed --scale $(SCALE)

seed-small: ## Rebuild the dataset at scale 0.1 for quick iteration
	$(RUN) seed --scale 0.1 --reset

check: ## Verify extensions, preload libraries and seed
	$(RUN) check

list: ## List cases
	$(RUN) list

run: ## Run all cases, or CASES="01 05", with pgbench replay; rebuild the report
	$(RUN) run $(CASES) --bench

case: ## Run one case without the pgbench replay: make case CASE=05
	$(RUN) run $(CASE)

bench: ## Replay before/after statements under pgbench: make bench CASES="01 02"
	$(RUN) bench $(CASES)

report: ## Rebuild reports/casebook.md and index.html from results/
	$(RUN) report

observability: ## Start postgres_exporter, Prometheus (:9094) and Grafana (:3004)
	$(COMPOSE) --profile observability up -d

psql: ## Open psql on the casebook database
	$(COMPOSE) exec postgres psql -U casebook -d casebook

slowlog: ## Show plans auto_explain logged for statements slower than 500 ms
	$(COMPOSE) logs --no-log-prefix postgres 2>&1 | grep -A40 'duration: .* plan:' | tail -200

smoke: ## End-to-end check on a tiny dataset in a separate project (~2 min)
	bash scripts/smoke.sh
