#!/usr/bin/env bash
# Usage: run_matrix.sh <image> <voxel_mm> <label> [cpuset-or-"all"] ...
# One named container per run, no network, STL on a named volume, hashed in the
# same container, then deleted. Prints one TSV row per run.
set -u
image=$1; vox=$2; label=$3; shift 3
vol=picogk-out
docker volume create "$vol" >/dev/null
for spec in "$@"; do
  name="picogk-${label}-${vox//./_}-${spec//[,-]/_}-$RANDOM"
  cpuarg=(); [ "$spec" != all ] && cpuarg=(--cpuset-cpus "$spec")
  out=$(MSYS_NO_PATHCONV=1 docker run --rm --name "$name" --network none "${cpuarg[@]}" \
        -v "$vol":/out --entrypoint /bin/sh "$image" -c \
        "/app/spike $vox /out/$name.stl; rc=\$?; echo rc=\$rc; if [ -f /out/$name.stl ]; then sha256sum /out/$name.stl | cut -c1-16; stat -c %s /out/$name.stl; [ -n \"\${KEEP:-}\" ] || rm -f /out/$name.stl; fi" 2>&1)
  printf '%s\t%s\t%s\t%s\n' "$label" "$vox" "$spec" "$(echo "$out" | tail -n 6 | tr '\n' ' ')"
done
