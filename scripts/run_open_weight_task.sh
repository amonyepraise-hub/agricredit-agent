#!/usr/bin/env bash
# Run one complete AgriCredit task with an open-weight Qwen model served locally by Ollama,
# and keep the evidence for the submission.
#
#   ./scripts/run_open_weight_task.sh                 # case BEN-0421, model from .env (default qwen2.5:7b-instruct)
#   ./scripts/run_open_weight_task.sh BEN-0002        # another case
#   OLLAMA_MODEL=qwen3:8b ./scripts/run_open_weight_task.sh
#
# Writes open_weight_runs/<timestamp>/ containing the full transcript, the model identifier and digest,
# and this run's tool-call and audit records. Nothing in that folder is edited after the run.
set -euo pipefail
cd "$(dirname "$0")/.."

CASE=${1:-BEN-0421}
[ -f .env ] || cp .env.example .env
# Values given on the command line (e.g. OLLAMA_MODEL=qwen2.5:3b-instruct ./scripts/...) win over .env.
_CLI_MODEL=${OLLAMA_MODEL:-}; _CLI_BASE=${OLLAMA_BASE_URL:-}; _CLI_TIMEOUT=${AGRICREDIT_LLM_TIMEOUT_S:-}
set -a; . ./.env; set +a
[ -n "$_CLI_MODEL" ] && export OLLAMA_MODEL="$_CLI_MODEL"
[ -n "$_CLI_BASE" ] && export OLLAMA_BASE_URL="$_CLI_BASE"
[ -n "$_CLI_TIMEOUT" ] && export AGRICREDIT_LLM_TIMEOUT_S="$_CLI_TIMEOUT"
export AGRICREDIT_LLM_BACKEND=ollama
MODEL=${OLLAMA_MODEL:-qwen2.5:7b-instruct}
BASE=${OLLAMA_BASE_URL:-http://localhost:11434}

command -v ollama >/dev/null || { echo "Install Ollama first: https://ollama.com/download"; exit 1; }
curl -fsS --max-time 3 "$BASE/api/tags" >/dev/null || { echo "Ollama is not running at $BASE (start it with 'ollama serve')"; exit 1; }
[ -x .venv/bin/python ] || { echo "Run ./run_demo.sh --cli once first to install dependencies"; exit 1; }

echo "Pulling $MODEL (skipped if already present)..."
ollama pull "$MODEL"

OUT="open_weight_runs/$(date -u +%Y%m%dT%H%M%SZ)_${CASE}"
mkdir -p "$OUT"
{
  echo "model: $MODEL"
  echo "ollama_version: $(ollama --version 2>&1 | tail -1)"
  echo "model_digest: $(ollama list | awk -v m="$MODEL" '$1==m {print $2}')"
  curl -fsS "$BASE/api/show" -d "{\"model\": \"$MODEL\"}" | .venv/bin/python -c \
    'import json,sys; d=json.load(sys.stdin); print("details:", json.dumps(d.get("details", {})))'
  echo "host: $(uname -srm)"
} > "$OUT/model.txt"
cat "$OUT/model.txt"

export AGRICREDIT_LOG_DIR="$PWD/$OUT/logs"
echo "Running $CASE end to end with $MODEL, approving as OFF-001..."
.venv/bin/python -m agricredit.cli run "$CASE" --approve OFF-001 2>&1 | grep -v -E "Secure MCP Filesystem|Client does not support MCP Roots" \
  | tee "$OUT/transcript.txt"

LLM_OK=$(grep -c '"tool_name": "llm.chat' "$OUT/logs/tool_calls.jsonl" || true)
LLM_FAIL=$(grep '"tool_name": "llm.chat' "$OUT/logs/tool_calls.jsonl" | grep -c '"success": false' || true)
echo
echo "LLM calls logged: $LLM_OK (failed: $LLM_FAIL). Evidence saved in $OUT/"
if [ "$LLM_FAIL" != "0" ] || [ "$LLM_OK" = "0" ]; then
  echo "WARNING: the model did not complete every LLM step; this run does NOT demonstrate a full open-weight task."
  exit 2
fi
