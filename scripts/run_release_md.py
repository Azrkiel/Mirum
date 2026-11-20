#!/usr/bin/env python3
"""Extract and run the CI-executable blocks of RELEASE.md.

Stdlib only. A fenced block whose info string is exactly `bash release-step=<id>` is
executed, in document order, in ONE bash session with `set -euo pipefail`.
`bash release-manual=<id>` blocks (and every other fence) are never extracted.

  run_release_md.py [--file RELEASE.md] --list
  run_release_md.py [--file RELEASE.md] --emit OUT.sh

The emitted script records per-step timing to $RELEASE_STEP_TIMING (default
$RELEASE_WORK/release-steps.json), also when a step fails.
"""
import argparse
import re
import sys
from pathlib import Path

FENCE = "`" * 3
_ID = r"[A-Za-z0-9][A-Za-z0-9_-]*"
_STEP_INFO = re.compile(rf"^bash release-step=({_ID})$")
_FORBIDDEN = [
    (re.compile(r"\bdown\b[^\n]*(\s-v\b|\s--volumes\b)"), "down -v"),
    (re.compile(r"\bvolume\s+rm\b"), "volume rm"),
    (re.compile(r"\bprune\b"), "prune"),
    (re.compile(r"\bset\s+-[a-zA-Z]*x"), "set -x"),
    (re.compile(r"\b(echo|printf)\b[^\n|;&<>]*\$\{?MIRUM_ACCESS_TOKEN\b"), "token print"),
    (re.compile(r"\bprintenv\b[^\n]*MIRUM_ACCESS_TOKEN"), "token print"),
    (re.compile(r"^\s*(env|export|declare\s+-x)\s*$", re.M), "environment dump"),
]


def extract(path):
    """Return [(id, code)] for every release-step block, in order."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    steps, seen = [], set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith(FENCE):
            i += 1
            continue
        info = line[len(FENCE):].strip()
        start = i + 1
        j = start
        while j < len(lines) and lines[j].rstrip() != FENCE:
            j += 1
        if j >= len(lines):
            raise ValueError(f"unterminated fence opened at line {i + 1}")
        body = lines[start:j]
        i = j + 1
        if "release-step" not in info:
            continue
        m = _STEP_INFO.match(info)
        if not m:
            raise ValueError(f"malformed release-step info string at line {start}: {info!r}")
        sid = m.group(1)
        if sid in seen:
            raise ValueError(f"duplicate release-step id {sid!r}")
        seen.add(sid)
        code = "\n".join(body).strip("\n")
        if not code.strip():
            raise ValueError(f"empty release-step block {sid!r}")
        for rx, label in _FORBIDDEN:
            if rx.search(code):
                raise ValueError(f"forbidden content ({label}) in release-step {sid!r}")
        steps.append((sid, code))
    return steps


_PRELUDE = r"""#!/usr/bin/env bash
set -euo pipefail
export RELEASE_WORK="${RELEASE_WORK:-/tmp/mirum-release}"
umask 077
mkdir -p -- "$RELEASE_WORK"
__timing="${RELEASE_STEP_TIMING:-$RELEASE_WORK/release-steps.json}"
__rows=""
__cur=""
__start=0
__on_exit() {
  if [ -n "$__cur" ]; then
    __rows="${__rows:+$__rows,}{\"id\":\"$__cur\",\"status\":\"failed\",\"seconds\":$((SECONDS - __start))}"
  fi
  printf '{"steps":[%s]}\n' "$__rows" > "$__timing"
}
trap __on_exit EXIT
"""


def script(steps):
    """One bash script running every step in order, with banners and timing."""
    out = [_PRELUDE]
    for sid, code in steps:
        out.append(f'\n__cur={sid}\n__start=$SECONDS\necho "=== release-step {sid} ==="\n')
        out.append(code + "\n")
        out.append(
            '__secs=$((SECONDS - __start))\n'
            f'__rows="${{__rows:+$__rows,}}{{\\"id\\":\\"{sid}\\",\\"status\\":\\"passed\\",\\"seconds\\":$__secs}}"\n'
            '__cur=""\n'
            f'echo "=== release-step {sid} done in ${{__secs}}s ==="\n'
        )
        if sid == "token":
            # GitHub Actions only: mask the run-local token for the runner and the log filter.
            out.append('if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::add-mask::$MIRUM_ACCESS_TOKEN"; fi\n')
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=str(Path(__file__).resolve().parents[1] / "RELEASE.md"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--emit", metavar="FILE")
    a = ap.parse_args(argv)
    try:
        steps = extract(a.file)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not steps:
        print("error: no release-step blocks", file=sys.stderr)
        return 2
    if a.list:
        for sid, _ in steps:
            print(sid)
        return 0
    Path(a.emit).write_text(script(steps), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
