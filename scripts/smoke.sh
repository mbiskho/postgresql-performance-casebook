#!/usr/bin/env bash
# End-to-end smoke test on a tiny dataset, isolated from the real one:
# its own compose project, data volume, port and output directory.
#
# It checks the machinery (seed, setup, measure, fix, teardown, report), not
# the 5x targets: at scale 0.02 most tables fit in cache and several plans
# are too fast for the ratios to mean anything.
set -euo pipefail

cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=pg-casebook-smoke
export CASEBOOK_PORT=5435
export CASEBOOK_OUT=.smoke

cleanup() { docker compose --profile tools down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

rm -rf .smoke && mkdir -p .smoke/results .smoke/reports && touch .smoke/index.html && cp README.md .smoke/README.md

docker compose up -d --build --wait postgres
docker compose --profile tools build runner
docker compose run --rm runner check
docker compose run --rm runner seed --scale 0.02
docker compose run --rm runner run 01 03 05 07 08 10
docker compose run --rm runner check

test -s .smoke/reports/casebook.md
test -s .smoke/index.html
python3 - <<'PY'
import json
s = json.load(open(".smoke/results/summary.json"))
assert len(s["cases"]) == 6, s["cases"]
bad = [c["case"] for c in s["cases"] if c.get("same_result") is False]
assert not bad, f"fix changed the result set in cases {bad}"
print("smoke ok:", ", ".join(f"{c['case']} {c['speedup']}x" for c in s["cases"]))
PY
