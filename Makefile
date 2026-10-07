PY := .venv/bin/python

.PHONY: help install data train test evals demo ui open-weight clean-runtime lock

help:
	@echo "make install      create .venv, install pinned Python deps and the MCP filesystem server"
	@echo "make data         generate the SYNTHETIC dataset (seeded)"
	@echo "make train        train and benchmark the risk model (writes data/model_card.json)"
	@echo "make test         run the pytest suite"
	@echo "make evals        run the evaluation suite and regenerate EVALS.md"
	@echo "make demo         scripted terminal demo"
	@echo "make ui           officer dashboard on http://localhost:8501"
	@echo "make open-weight  run one full task with Qwen via Ollama and save the evidence"

install:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.lock
	$(PY) -m pip install --no-deps -e .
	npm ci --no-audit --no-fund
	touch .venv/.installed

data:
	$(PY) data/generate_synthetic_data.py

train:
	$(PY) -m agricredit.risk.train

test:
	$(PY) -m pytest -q tests

evals:
	$(PY) evals/run_evals.py

demo:
	$(PY) -m agricredit.cli demo

ui:
	.venv/bin/streamlit run src/agricredit/ui/app.py

open-weight:
	./scripts/run_open_weight_task.sh

clean-runtime:
	rm -rf logs/*.jsonl evals/.work fs_mcp_root/audit_exports/*.json
	$(PY) -c "import sqlite3; c=sqlite3.connect('data/agricredit.db'); [c.execute(f'DELETE FROM {t}') for t in ('advisories','approvals','release_outbox')]; c.commit()"

lock:
	$(PY) -m pip freeze --exclude-editable | grep -v '^agricredit' > requirements.lock
