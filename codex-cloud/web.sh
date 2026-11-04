#!/usr/bin/env bash
# Start the local browser UI and PicoGK job API from the repository checkout.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${REGEN_PREFIX:-/opt/regen}"
if [ -z "${REGEN_PREFIX:-}" ] && [ ! -f "$PREFIX/env.sh" ] && [ -f /workspace/regen-runtime/env.sh ]; then
  PREFIX=/workspace/regen-runtime
fi
if [ ! -f "$PREFIX/env.sh" ]; then
  printf 'PicoGK runtime missing. Run: bash codex-cloud/setup.sh\n' >&2
  exit 1
fi
source "$PREFIX/env.sh"
cd "$repo"
exec env PYTHONPATH="$repo/backend" python -m uvicorn engineering.regen_web:create_app --factory \
  --host "${REGEN_WEB_HOST:-127.0.0.1}" --port "${REGEN_WEB_PORT:-8080}" --workers 1
