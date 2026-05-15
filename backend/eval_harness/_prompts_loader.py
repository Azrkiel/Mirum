"""Pure-stdlib loader and filter for prompts.json.

Split out from runner.py so the filter contract can be unit-tested without
importing google-generativeai or any other heavy runtime dep. Both
`runner.py` and `backend/tests/test_runner_filters.py` consume this module.

Filter semantics:

  id_prefix       — keep entries whose id startswith the prefix
  regression_for  — keep entries whose `regression_for` array contains the
                    given single-letter tag

Both filters AND together. `regression_for` is intentionally NOT the same as
`domain`: an A-domain entry that does not declare a regression_for tag must
not be picked up by `--regression-for A`. The schema test (test_prompts_
schema.py) already enforces that any present `regression_for` is a list of
strings drawn from {A,B,C,D,E}, so we trust the data shape here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

_HERE = Path(__file__).resolve().parent
PROMPTS_FILE = _HERE / "prompts.json"


def load_prompts(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or PROMPTS_FILE
    return json.loads(target.read_text(encoding="utf-8"))


def filter_prompts(
    prompts: Iterable[dict[str, Any]],
    *,
    id_prefix: str | None = None,
    regression_for: str | None = None,
) -> list[dict[str, Any]]:
    out = list(prompts)
    if id_prefix:
        out = [p for p in out if p["id"].startswith(id_prefix)]
    if regression_for:
        tag = regression_for
        out = [
            p for p in out
            if isinstance(p.get("regression_for"), list)
            and tag in p["regression_for"]
        ]
    return out
