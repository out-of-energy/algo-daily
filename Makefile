PYTHON ?= python3

.PHONY: help data data-check demo build validate test stats topics refresh doctor serve

help:
	@echo "make data      First run: fetch + build the question bank (skips if already built)"
	@echo "make data-check  Report whether the question bank is built"
	@echo "make demo      Generate the bundled demo page + .eml preview"
	@echo "make build     Build normalized JSON/JSONL + per-topic files + SQLite"
	@echo "make validate  Check dataset, exports and SQLite consistency"
	@echo "make test      Run dataset and daily app tests"
	@echo "make stats     Print dataset statistics"
	@echo "make topics    Print topic counts"
	@echo "make doctor    Check AI/email config (no secrets printed)"
	@echo "make serve     Serve site/ at http://127.0.0.1:8765"
	@echo "make refresh   Fetch latest LeetCode data, backfill Chinese, rebuild, validate"

data:
	$(PYTHON) scripts/bootstrap_data.py

data-check:
	$(PYTHON) scripts/bootstrap_data.py --check

demo:
	$(PYTHON) scripts/run_daily.py --demo

build:
	$(PYTHON) scripts/build_dataset.py

validate:
	$(PYTHON) scripts/validate_dataset.py

test:
	$(PYTHON) -m unittest discover -s tests -v

doctor:
	$(PYTHON) scripts/doctor.py

serve:
	$(PYTHON) scripts/serve.py --open

stats:
	$(PYTHON) algo_store.py stats

topics:
	$(PYTHON) algo_store.py topics

refresh:
	$(PYTHON) scripts/bootstrap_data.py --refetch
