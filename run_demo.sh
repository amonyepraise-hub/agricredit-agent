#!/usr/bin/env bash
# One-command setup and demo for AgriCredit Agent.
#
#   ./run_demo.sh            set up (if needed), run tests, open the officer dashboard on http://localhost:8501
#   ./run_demo.sh --cli      set up (if needed), run the scripted terminal demo instead of the dashboard
#   ./run_demo.sh --real     same, but on REAL open data (NASA POWER, ERA5, WFP, HarvestStat/DSA). The first run
#                            downloads the data (needs internet, about 20 to 30 minutes); later runs work offline.
#   ./run_demo.sh --real --refresh-data   download the open data again
#   ./run_demo.sh --real --evals          evaluate every real-data case and write EVALS_REAL.md
#
# Needs: Python >= 3.11 and Node.js >= 20 (for the official MCP filesystem server). Nothing else.
# Uses AGRICREDIT_LLM_BACKEND from .env (copied from .env.example on first run). The default is
# `ollama` with qwen2.5:7b-instruct; if no Ollama server answers, the script falls back to the
# clearly-labelled offline stub so the rest of the system can still be inspected.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

REAL=0; CLI=0; REFRESH=0; EVALS=0
for arg in "$@"; do
  case "$arg" in
    --real) REAL=1 ;;
    --cli) CLI=1 ;;
    --refresh-data) REFRESH=1 ;;
    --evals) EVALS=1 ;;
    *) die "unknown option $arg (use --cli, --real, --refresh-data, --evals)" ;;
  esac
done

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

if [ "$REAL" = 1 ]; then
  SNAP=data/real/processed/snapshot.json
  if [ ! -f "$SNAP" ] || [ "$REFRESH" = 1 ]; then
    say "Downloading REAL open data: HarvestStat/DSA yields, WFP prices, NASA POWER and ERA5 rainfall (needs internet)"
    if [ "$REFRESH" = 1 ]; then .venv/bin/python -m agricredit.opendata fetch --refresh; else .venv/bin/python -m agricredit.opendata fetch; fi
  fi
  [ -f "$SNAP" ] || die "open data download did not finish; check your internet connection and run again"
  if [ ! -f data/agricredit_real.db ] || [ "$SNAP" -nt data/agricredit_real.db ]; then
    say "Building the real-open-data database (offline, seeded)"
    .venv/bin/python -m agricredit.opendata build
  fi
  export AGRICREDIT_DB_PATH=data/agricredit_real.db
  export AGRICREDIT_POLICY_FILE=agent_policy_real.json
  export AGRICREDIT_RISK_MODEL_PATH=data/risk_model_real.joblib
  export AGRICREDIT_MARKET_FRESHNESS_DAYS=90   # WFP prices are monthly and published with a lag
  export AGRICREDIT_WEATHER_FRESHNESS_DAYS=14  # satellite rainfall arrives a few days late
  export AGRICREDIT_TARGET_DEFAULT_RATE=0.094  # BCEAO: Benin microfinance portfolio degradation rate, Dec 2024
  export AGRICREDIT_UPLIFT_REALISATION=1.0     # real packages carry a sourced yield gain (see opendata/sources.py)
  AGRICREDIT_AS_OF=$(.venv/bin/python -c "import json; print(json.load(open('$SNAP'))['as_of'])")
  export AGRICREDIT_AS_OF
  if [ ! -f data/risk_model_real.joblib ] || [ data/agricredit_real.db -nt data/risk_model_real.joblib ]; then
    say "Training the risk model on the real-open-data store (~20 s)"
    .venv/bin/python -m agricredit.risk.train >/dev/null
  fi
  say "REAL open data as of $AGRICREDIT_AS_OF (households are simulated inside real communes; see data/real/SOURCES.md)"
fi

if [ "$EVALS" = 1 ] && [ "$REAL" = 1 ]; then
  say "Evaluating every real-data case (writes EVALS_REAL.md)"
  exec .venv/bin/python evals/run_real_evals.py
fi

if [ "$CLI" = 1 ] && [ "$REAL" = 1 ]; then
  say "Real-data cases"
  CASES=$(.venv/bin/python -m agricredit.cli cases)
  printf '%s\n' "$CASES" | sed -n '1,8p'
  FIRST=$(printf '%s\n' "$CASES" | sed -n '1p' | cut -d' ' -f1)
  say "Running $FIRST with a named officer approval"
  .venv/bin/python -m agricredit.cli run "$FIRST" --approve OFF-001
elif [ "$CLI" = 1 ]; then
  say "Scripted terminal demo"
  .venv/bin/python -m agricredit.cli demo
else
  say "Starting the officer dashboard on http://localhost:${PORT:-8501}  (Ctrl+C to stop)"
  exec .venv/bin/streamlit run src/agricredit/ui/app.py --server.port "${PORT:-8501}" --server.headless true \
       --browser.gatherUsageStats false
fi
