"""The response schemas the pipeline sends must be accepted by the real Gemini SDK.

The other schema tests stub ``google.generativeai`` and check a fixed list of
forbidden keys, so a Pydantic constraint the list misses (``gt=`` ->
``exclusiveMinimum`` on ``Wing3DAeroReport.span_efficiency_e``) passed them
while every ``/generate`` call failed in production with
``ValueError: Unknown field for Schema: exclusiveMinimum``.

Each check runs in a subprocess so the stubs other test modules install in
``sys.modules`` cannot shadow the real SDK.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]

_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {backend!r})
    try:
        from google.generativeai.types import generation_types
    except ImportError:
        print("NO_SDK")
        raise SystemExit(0)
    import agents

    schemas = {{
        "planner": agents._get_gemini_schema(),
        "rea": agents._rea_schema_for("a mounting bracket"),
        "rea_propulsion": agents._rea_schema_for("a rocket engine with a turbopump"),
    }}
    for name, schema in schemas.items():
        generation_types.to_generation_config_dict(
            {{"response_mime_type": "application/json", "response_schema": schema}}
        )
        print("OK", name)
    """
)


def test_response_schemas_convert_with_real_gemini_sdk() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE.format(backend=str(_BACKEND))],
        capture_output=True, text=True, timeout=120,
    )
    if "NO_SDK" in proc.stdout:
        pytest.skip("google-generativeai not installed")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.count("OK ") == 3, proc.stdout
