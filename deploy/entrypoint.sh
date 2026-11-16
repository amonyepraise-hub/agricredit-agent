#!/usr/bin/env bash
# Container start: prepare the persistent state folder, create bootstrap accounts, make sure the
# language model is available, then serve the officer dashboard.
#
# State that must survive restarts lives in AGRICREDIT_VAR_DIR (a Docker volume):
#   agricredit_real.db   cases, advisories, approvals, release outbox, officers
#   users.json           officer accounts (hashed passwords)
#   .integrity_key       secret key that signs evidence
#   logs/                tool-call and audit logs
#   fs_mcp_root/         policy (refreshed from the image at every start) and audit exports
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"
VAR="${AGRICREDIT_VAR_DIR:-$APP_DIR/var}"
say() { printf '[agricredit] %s\n' "$*"; }

mkdir -p "$VAR/logs" "$VAR/fs_mcp_root/audit_exports"
umask 077

. deploy/real_env.sh
export AGRICREDIT_DB_PATH="$VAR/agricredit_real.db"
export AGRICREDIT_LOG_DIR="$VAR/logs"
export AGRICREDIT_USERS_FILE="$VAR/users.json"
export AGRICREDIT_INTEGRITY_KEY_FILE="$VAR/.integrity_key"
export AGRICREDIT_FS_MCP_ROOT="$VAR/fs_mcp_root"
export AGRICREDIT_AUTH_REQUIRED="${AGRICREDIT_AUTH_REQUIRED:-true}"

# First start: copy the database built into the image. Later starts keep the officers' work.
if [ ! -f "$AGRICREDIT_DB_PATH" ]; then
  cp data/agricredit_real.db "$AGRICREDIT_DB_PATH"
  say "created the database from the image (real open data as of $AGRICREDIT_AS_OF)"
fi
# The policy always comes from the image, so an updated image brings the updated rules.
rm -rf "$VAR/fs_mcp_root/policy" "$VAR/fs_mcp_root/evals"
cp -r fs_mcp_root/policy "$VAR/fs_mcp_root/policy"
[ -d fs_mcp_root/evals ] && cp -r fs_mcp_root/evals "$VAR/fs_mcp_root/evals"

python -m agricredit.auth bootstrap
if [ "$AGRICREDIT_AUTH_REQUIRED" = "true" ] && [ "$(python -m agricredit.auth list | wc -l)" -eq 0 ]; then
  say "NO OFFICER ACCOUNTS YET. Nobody can sign in until you create one, for example:"
  say "  docker compose exec app python -m agricredit.auth add YOURNAME --officer-id OFF-004 --name \"Your Name\""
fi

# Language model: an Ollama server inside this container (hosted demo) or next to it (docker compose).
if [ "${AGRICREDIT_EMBEDDED_OLLAMA:-false}" = "true" ]; then
  export OLLAMA_HOST=127.0.0.1:11434
  export OLLAMA_BASE_URL=http://127.0.0.1:11434
  ollama serve > "$VAR/logs/ollama.log" 2>&1 &
  say "started the embedded Ollama server"
fi
if [ "${AGRICREDIT_LLM_BACKEND:-ollama}" = "ollama" ]; then
  if ! python deploy/ensure_model.py; then
    say "Qwen is not available: explanations will use the clearly labelled fixed template (decisions are unchanged)."
    export AGRICREDIT_LLM_BACKEND=stub
  fi
fi

PORT="${PORT:-8501}"
say "officer dashboard on port $PORT"
exec streamlit run src/agricredit/ui/app.py \
  --server.port "$PORT" --server.address 0.0.0.0 --server.headless true \
  --server.fileWatcherType none --server.runOnSave false \
  --browser.gatherUsageStats false --client.toolbarMode viewer --client.showErrorDetails false
