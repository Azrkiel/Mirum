#!/usr/bin/env bash
# Codex cloud setup for the PicoGK regen chamber generator (no Docker needed).
#
# This unpacks the prebuilt builder without requiring Docker
# and PicoGK runtime from the public image ghcr.io/azrkiel/mirum-regen:slice-1
# (pinned by digest) and runs the repo's current Python verifier natively.
# The C# builder and PicoGK runtime are unchanged since slice-1; only the Python
# verifier differs, and it comes from this checkout.
#
# Needs setup-time registry, package mirror, and PyPI access.
# Paste into the Codex environment's setup script field:   bash codex-cloud/setup.sh
set -euo pipefail

PREFIX="${REGEN_PREFIX:-/opt/regen}"
IMAGE_REPO="azrkiel/mirum-regen"
IMAGE_DIGEST="sha256:5492e66b0937a774235333c42a8e5efe9119405238569389c9fd69557ff01494"  # slice-1
PY_VERSION="3.13"
# Same versions as the Debian trixie packages inside the image.
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_LIBS=(libtbb libblosc libboost_iostreams libz libzstd liblz4 libsnappy libbz2 liblzma libxxhash)

log() { printf '[regen-setup] %s\n' "$*"; }
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    # Managed non-root machines often already provide these system libraries.
    command -v curl >/dev/null && command -v python3 >/dev/null || {
      log 'curl and python3 are required; ask the host administrator to install them'; exit 1;
    }
    python3 - <<'PY'
import ctypes
for library in ('libGL.so.1', 'libX11.so.6'):
    ctypes.CDLL(library)
PY
    PREFIX="${REGEN_PREFIX:-/workspace/regen-runtime}"
  fi
fi

log "system libraries"
if [ "$(id -u)" -eq 0 ] || [ -n "$SUDO" ]; then
  $SUDO apt-get update -qq
  DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq --no-install-recommends \
    libgl1 libx11-6 ca-certificates curl python3 >/dev/null
fi

log "pulling builder + PicoGK runtime from ghcr.io/${IMAGE_REPO}@${IMAGE_DIGEST}"
$SUDO mkdir -p "$PREFIX"
if [ -n "$SUDO" ]; then $SUDO chown "$(id -u):$(id -g)" "$PREFIX"; fi
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
token="$(curl -fsSL "https://ghcr.io/token?scope=repository:${IMAGE_REPO}:pull" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
auth=(-H "Authorization: Bearer ${token}")
curl -fsSL "${auth[@]}" -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
  "https://ghcr.io/v2/${IMAGE_REPO}/manifests/${IMAGE_DIGEST}" -o "$work/manifest.json"
got="sha256:$(sha256sum "$work/manifest.json" | cut -d' ' -f1)"
[ "$got" = "$IMAGE_DIGEST" ] || { log "manifest digest mismatch: $got"; exit 1; }
rootfs="$work/rootfs"; mkdir -p "$rootfs"
python3 -c 'import json,sys; [print(l["digest"]) for l in json.load(open(sys.argv[1]))["layers"]]' \
  "$work/manifest.json" | while read -r layer; do
  curl -fsSL "${auth[@]}" "https://ghcr.io/v2/${IMAGE_REPO}/blobs/${layer}" -o "$work/layer.tgz"
  [ "sha256:$(sha256sum "$work/layer.tgz" | cut -d' ' -f1)" = "$layer" ] || { log "layer digest mismatch $layer"; exit 1; }
  # The builder, its gcc runtime, and every non-glibc library picogk.26.2.so links,
  # taken from the image so only glibc comes from the host. Ubuntu 24.04's oneTBB is
  # too old for picogk.26.2.so; all of these need GLIBC <= 2.38 (host has 2.39).
  tar -xzf "$work/layer.tgz" -C "$rootfs" --wildcards 'app/*' 'usr/local/lib/picogk-gcc/*' 'licenses/*' \
    $(for l in "${IMAGE_LIBS[@]}"; do printf "usr/lib/x86_64-linux-gnu/%s* " "$l"; done) 2>/dev/null || true
  rm -f "$work/layer.tgz"
done
rm -rf "$PREFIX/app" "$PREFIX/lib" "$PREFIX/licenses"
mv "$rootfs/app" "$PREFIX/app"
mv "$rootfs/usr/local/lib/picogk-gcc" "$PREFIX/lib"
cp -a "$rootfs"/usr/lib/x86_64-linux-gnu/. "$PREFIX/lib/"
mv "$rootfs/licenses" "$PREFIX/licenses"
[ "$(sha256sum "$PREFIX/app/picogk.26.2.so" | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$PREFIX/app/picogk.26.2.so.sha256")" ] \
  || { log "picogk.26.2.so hash mismatch"; exit 1; }
deps="$(LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/app" ldd "$PREFIX/app/picogk.26.2.so")"
! grep -q 'not found' <<<"$deps" || { log "unresolved libraries: $deps"; exit 1; }
# Everything except glibc must resolve to the image's copies.
leak="$(grep '=>' <<<"$deps" | grep -vE "=> $PREFIX/lib/|lib(c|m)\.so\.6 " || true)"
[ -z "$leak" ] || { log "host library used instead of the image's: $leak"; exit 1; }

log "python ${PY_VERSION} venv"
if command -v uv >/dev/null 2>&1; then
  if [ ! -x "$PREFIX/venv/bin/python" ]; then uv venv -q --python "$PY_VERSION" "$PREFIX/venv"; fi
  uv pip install --python "$PREFIX/venv/bin/python" -q -r "$repo/codex-cloud/requirements.txt"
else
  "python${PY_VERSION}" -m venv "$PREFIX/venv"
  "$PREFIX/venv/bin/pip" install -q -r "$repo/codex-cloud/requirements.txt"
fi

cat > "$PREFIX/env.sh" <<EOF
export REGEN_BUILDER="$PREFIX/app/builder"
# regen_cli puts this dir on LD_LIBRARY_PATH for the builder subprocess only.
export REGEN_GCC_LIB="$PREFIX/lib"
export PATH="$PREFIX/venv/bin:\$PATH"
export PYTHONDONTWRITEBYTECODE=1
EOF
log "done: source $PREFIX/env.sh, then codex-cloud/regen.sh <spec.json> <outdir>"
log "browser: REGEN_PREFIX=$PREFIX bash codex-cloud/web.sh"
