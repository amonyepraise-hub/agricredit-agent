#!/usr/bin/env bash
# Image build step: install pinned dependencies and build the real-open-data store and risk model
# from the extracts committed in data/real/processed (no internet needed beyond the package registries).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

pip install --no-cache-dir -r requirements.lock
pip install --no-cache-dir --no-deps -e .
npm ci --no-audit --no-fund

. deploy/real_env.sh
export AGRICREDIT_DB_PATH="$APP_DIR/data/agricredit_real.db"
export AGRICREDIT_INTEGRITY_KEY_FILE="$(mktemp -d)/build_key"   # throwaway: the real key is made at first start
python -m agricredit.opendata build
python -m agricredit.risk.train > /dev/null
rm -f "$AGRICREDIT_INTEGRITY_KEY_FILE"
echo "build complete: real open data as of $AGRICREDIT_AS_OF"
