#!/usr/bin/env bash
#
# deploy-remote.sh — runs ON the Oracle Cloud instance, piped in over SSH by
# .github/workflows/deploy.yml. Kept in version control (rather than inlined
# in the workflow YAML) so deployment logic is reviewable in PRs like any
# other code.
#
# Manual invocation on the box is equivalent:
#     cd ~/T2CIII && bash scripts/deploy-remote.sh
#
# Environment:
#   MIRUM_DEPLOY_DIR  checkout to deploy (default: ~/T2CIII)
#   MIRUM_BRANCH      branch to deploy   (default: main)
#   MIRUM_FORCE_BUILD set to 1 to force a Docker image rebuild

set -euo pipefail

DEPLOY_DIR="${MIRUM_DEPLOY_DIR:-$HOME/T2CIII}"
BRANCH="${MIRUM_BRANCH:-main}"
FORCE_BUILD="${MIRUM_FORCE_BUILD:-0}"

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

cd "$DEPLOY_DIR"

if [ ! -d .git ]; then
    echo "ERROR: $DEPLOY_DIR is not a git checkout." >&2
    echo "Run the one-time setup in docs/deployment.md first." >&2
    exit 1
fi

BEFORE="$(git rev-parse HEAD)"

log "Fetching origin/$BRANCH"
git fetch --prune origin "$BRANCH"

# reset --hard makes the deploy idempotent: the box always ends up exactly
# at origin/$BRANCH regardless of any drift from a previous scp-based
# deploy. It does NOT touch untracked or gitignored files, so the server's
# .env (Gemini key, API key, basic-auth hash) survives untouched.
log "Resetting to origin/$BRANCH"
git reset --hard "origin/$BRANCH"

AFTER="$(git rev-parse HEAD)"

if [ "$BEFORE" = "$AFTER" ]; then
    log "Already at $(git rev-parse --short HEAD) — no new commits"
else
    log "$(git rev-parse --short "$BEFORE") -> $(git rev-parse --short "$AFTER")"
    git --no-pager log --oneline "$BEFORE..$AFTER" | sed 's/^/    /'
fi

# A conda + OpenCASCADE image takes many minutes to rebuild and is memory
# hungry, so only rebuild when something that affects the image actually
# changed. Application code is bind-mounted read-only, so ordinary Python
# edits need a container recreate, not an image build.
BUILD_FLAG=""
if [ "$FORCE_BUILD" = "1" ]; then
    log "Rebuild forced by MIRUM_FORCE_BUILD"
    BUILD_FLAG="--build"
elif [ "$BEFORE" != "$AFTER" ] && \
     git diff --name-only "$BEFORE" "$AFTER" \
       | grep -qE '(Dockerfile|requirements\.txt|docker-compose\.yml)$'; then
    log "Image inputs changed — rebuilding"
    BUILD_FLAG="--build"
fi

# scp/git can leave 0700 dirs; caduser inside the backend image is a system
# user distinct from the host's ubuntu, so /app must stay world-traversable
# or uvicorn fails with "Could not import module 'main'".
log "Normalizing permissions"
chmod -R a+rX "$DEPLOY_DIR"

log "Recreating containers ${BUILD_FLAG:+($BUILD_FLAG)}"
# --profile production adds the Caddy edge proxy (basic auth + TLS). Without
# it nothing is published publicly, since backend and frontend bind loopback.
# shellcheck disable=SC2086 — BUILD_FLAG is intentionally word-split
docker compose --profile production up -d --force-recreate --remove-orphans $BUILD_FLAG

log "Waiting for backend health"
for i in $(seq 1 30); do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        log "Backend healthy after ${i}0s"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: backend did not become healthy within 300s" >&2
        docker compose ps
        docker compose logs --tail=80 backend >&2
        exit 1
    fi
    sleep 10
done

log "Deployed $(git rev-parse --short HEAD)"
docker compose ps

# Reclaim space from superseded image layers. Oracle Always Free boot
# volumes fill up fast when a multi-GB conda image is rebuilt repeatedly.
if [ -n "$BUILD_FLAG" ]; then
    log "Pruning dangling images"
    docker image prune -f >/dev/null 2>&1 || true
fi
