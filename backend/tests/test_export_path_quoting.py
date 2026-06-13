"""
backend/tests/test_export_path_quoting.py
==========================================

Pins that filesystem paths embedded into generated CadQuery scripts
round-trip exactly through Python string-literal parsing. Found live
2026-06-12 (Windows host): ``run_single_part_export`` embedded
``C:\\tmp\\mirum\\work\\<uuid>.glb`` in a plain quoted literal, the script
parsed ``\\t`` as TAB -> Assembly.save wrote nowhere main.py looks ->
"Script succeeded but .glb file was not created" on every /generate.
Docker never hits it (forward-slash paths), so only a host e2e could.

Run directly:
    cd T2CIII/backend
    python tests/test_export_path_quoting.py
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import sys
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")

_BACKEND = Path(__file__).resolve().parent.parent
_ROOT = _BACKEND.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT))

_WIN_PATH = "C:\\tmp\\mirum\\work\\test_export.glb"


def _save_literals(script: str) -> list[str]:
    tree = ast.parse(script)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "save"
                and node.args and isinstance(node.args[0], ast.Constant)):
            out.append(node.args[0].value)
    return out


def test_single_part_export_path_survives_windows_backslashes():
    import agents
    script = asyncio.run(agents.run_single_part_export("result = 1", _WIN_PATH))
    literals = _save_literals(script)
    assert literals, "no .save() string literals found in export script"
    for value in literals:
        assert pathlib.PurePath(value) == pathlib.PurePath(_WIN_PATH), (
            f"path mangled in script literal: {value!r}")


def test_pipeline_work_paths_use_forward_slashes():
    # main.py builds every scratch path via _work_path(); embedded in script
    # literals (including via str.replace on the assembler's output.glb
    # sentinel) a backslash path mangles, a forward-slash path never does.
    import main
    p = main._work_path("abc.glb")
    assert "\\" not in p, f"_work_path emitted backslashes: {p!r}"
    assert p.endswith("/abc.glb")


def _run_all() -> int:
    funcs = [
        test_single_part_export_path_survives_windows_backslashes,
        test_pipeline_work_paths_use_forward_slashes,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
