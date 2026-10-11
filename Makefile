UV ?= uv
ifndef RECORD
RECORD := runs/smoke-$(shell date -u +%Y%m%dT%H%M%SZ)
endif

.PHONY: sync check doctor smoke live replay-live replay report

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
