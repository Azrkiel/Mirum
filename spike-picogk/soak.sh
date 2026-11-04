#!/usr/bin/env bash
# Usage: soak.sh <image> <total_runs> <parallel> <log.tsv>
# Oversubscribes CPU to provoke the PicoGKRuntime #31 timer race. Each run is a
# named container writing its STL inside the container. Records exit code and hash.
set -u
image=$1; total=$2; par=$3; log=$4
: > "$log"
one() {
  i=$1
  name="picogk-soak-$i"
  out=$(MSYS_NO_PATHCONV=1 docker run --rm --name "$name" --network none -e "PICOGK_STOP_MEM_TIMER=${PICOGK_STOP_MEM_TIMER:-0}" --entrypoint /bin/sh "$image" -c \
    '/app/spike 0.4 /tmp/s.stl > /tmp/o.txt 2>&1; rc=$?; grep "^vox=" /tmp/o.txt; echo "rc=$rc"; if [ "$rc" != 0 ]; then tail -n 25 /tmp/o.txt; fi; [ -f /tmp/s.stl ] && sha256sum /tmp/s.stl | cut -c1-16' 2>&1)
  outer=$?
  printf '%s\t%s\t%s\n' "$i" "$outer" "$(echo "$out" | tr '\n' ' ')" >> "$log"
}
export -f one
export image log
export PICOGK_STOP_MEM_TIMER="${PICOGK_STOP_MEM_TIMER:-0}"
seq 1 "$total" | xargs -P "$par" -I{} bash -c 'one {}'
echo "runs: $(wc -l < "$log")"
echo "exit-code counts:"; grep -oE 'rc=[0-9]+' "$log" | sort | uniq -c
echo "hash counts:"; grep -oE '[0-9a-f]{16}( |$)' "$log" | sort | uniq -c
echo "docker-exit nonzero: $(awk -F'\t' '$2!=0' "$log" | wc -l)"
echo "non-clean rows:"; grep -v 'rc=0' "$log" | head -20
echo "sec range:"; grep -oE 'sec=[0-9.]+' "$log" | cut -d= -f2 | sort -n | sed -n '1p;$p'
