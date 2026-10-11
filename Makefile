UV ?= uv
ifndef RECORD
RECORD := runs/smoke-$(shell date -u +%Y%m%dT%H%M%SZ)
endif

.PHONY: sync check doctor smoke live replay-live replay report sync-local lock-local check-local doctor-local smoke-local

sync:
	$(UV) sync --locked

check:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked ty check src tests
	$(UV) run --locked pytest

doctor:
	$(UV) run --locked autobench instrumentation doctor

smoke:
	$(UV) run --locked autobench validate benchmarks/smoke.yaml
	$(UV) run --locked autobench run benchmarks/smoke.yaml --record "$(RECORD)"
	$(UV) run --locked autobench replay "$(RECORD)"

live:
	$(UV) run --locked autobench validate benchmarks/live.yaml
	$(UV) run --locked python -m freya_hackathon.live --record "$(RECORD)"

replay-live:
	$(UV) run --locked python -m freya_hackathon.live --replay-only --record "$(RECORD)"

replay:
	$(UV) run --locked autobench replay "$(RECORD)"

report:
	$(UV) run --locked autobench report "$(RECORD)"

lock-local:
	$(UV) pip compile --no-config --no-sources --python-version 3.13 environments/local/requirements.in -o environments/local/requirements.txt

sync-local:
	$(UV) venv --python 3.13 --allow-existing .venv-local
	$(UV) pip sync --no-config --no-sources --python .venv-local/bin/python environments/local/requirements.txt

check-local:
	.venv-local/bin/ruff check .
	.venv-local/bin/ruff format --check .
	.venv-local/bin/ty check --python .venv-local/bin/python src tests
	.venv-local/bin/pytest

doctor-local:
	.venv-local/bin/autobench instrumentation doctor

smoke-local:
	.venv-local/bin/autobench validate benchmarks/smoke.yaml
	.venv-local/bin/autobench run benchmarks/smoke.yaml --record "$(RECORD)"
	.venv-local/bin/autobench replay "$(RECORD)"
