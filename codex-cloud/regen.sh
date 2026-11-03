#!/usr/bin/env bash
# Run the regen generator natively after codex-cloud/setup.sh.
# Same entry point and arguments as the Docker image:
#   codex-cloud/regen.sh <spec.json> <outdir> [--prebuilt-stl mesh.stl]
# Exit 0 = accepted, 2 = rejected (see docs/testing/regen-chamber-testers.md).
set -euo pipefail
PREFIX="${REGEN_PREFIX:-/opt/regen}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$PREFIX/env.sh"
# Same module set as the image's /py: run from the repo's backend package.
PYTHONPATH="$repo/backend" exec python -m engineering.regen_cli "$@"
