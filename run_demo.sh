#!/usr/bin/env bash
# One-command setup and demo for AgriCredit Agent.
#
#   ./run_demo.sh            set up (if needed), run tests, open the officer dashboard on http://localhost:8501
#   ./run_demo.sh --cli      set up (if needed), run the scripted terminal demo instead of the dashboard
#
# Needs: Python >= 3.11 and Node.js >= 20 (for the official MCP filesystem server). Nothing else.
# Uses AGRICREDIT_LLM_BACKEND from .env (copied from .env.example on first run). The default is
# `ollama` with qwen2.5:7b-instruct; if no Ollama server answers, the script falls back to the
# clearly-labelled offline stub so the rest of the system can still be inspected.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || die "python3 not found (need >= 3.11)"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || die "Python >= 3.11 required"
command -v node >/dev/null || die "Node.js not found (need >= 20 for @modelcontextprotocol/server-filesystem)"
command -v npm >/dev/null || die "npm not found"

[ -f .env ] || { cp .env.example .env; say "Created .env from .env.example"; }
set -a; . ./.env; set +a

if [ ! -x .venv/bin/python ]; then
  say "Creating virtual environment"
  "$PY" -m venv .venv
fi
if [ ! -f .venv/.installed ] || [ requirements.lock -nt .venv/.installed ]; then
  say "Installing pinned Python dependencies (requirements.lock)"
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.lock
  .venv/bin/pip install --quiet --no-deps -e .
  touch .venv/.installed
fi
if [ ! -x node_modules/.bin/mcp-server-filesystem ]; then
  say "Installing the official MCP filesystem server (pinned in package-lock.json)"
  npm ci --silent --no-audit --no-fund
fi
if [ ! -f data/agricredit.db ]; then
  say "Generating the SYNTHETIC dataset (seeded, offline)"
  .venv/bin/python data/generate_synthetic_data.py
fi
if [ ! -f data/risk_model.joblib ]; then
  say "Training the calibrated risk model (seeded, ~20 s)"
  .venv/bin/python -m agricredit.risk.train >/dev/null
fi

if [ "${AGRICREDIT_LLM_BACKEND:-ollama}" = "ollama" ]; then
  if ! curl -fsS --max-time 3 "${OLLAMA_BASE_URL:-http://localhost:11434}/api/tags" >/dev/null 2>&1; then
    say "No Ollama server at ${OLLAMA_BASE_URL:-http://localhost:11434}: using the OFFLINE STUB for this session."
    echo "    The stub is not a language model. To use Qwen: install Ollama, run 'ollama pull ${OLLAMA_MODEL:-qwen2.5:7b-instruct}', re-run."
    export AGRICREDIT_LLM_BACKEND=stub
  fi
fi

say "Running the test suite"
.venv/bin/python -m pytest -q tests

if [ "${1:-}" = "--cli" ]; then
  say "Scripted terminal demo"
  .venv/bin/python -m agricredit.cli demo
else
  say "Starting the officer dashboard on http://localhost:${PORT:-8501}  (Ctrl+C to stop)"
  exec .venv/bin/streamlit run src/agricredit/ui/app.py --server.port "${PORT:-8501}" --server.headless true \
       --browser.gatherUsageStats false
fi
