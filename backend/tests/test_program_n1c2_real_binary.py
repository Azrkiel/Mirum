"""N1c-2: the real pinned mirum-native binary plus the real OCCT plate worker (native shard, opt-in).

Set MIRUM_N1C2_REAL_BIN to the downloaded binary (it must match the pin) and, when this interpreter has no
CadQuery, MIRUM_N1C2_OCCT_PYTHON to an interpreter that has it. Skipped without the binary; the fail-rather-than-skip
rule for MIRUM_NATIVE_GATE_IMAGE=1 arrives with the native gate image in N1c-3.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from engineering.language.semantic import compile_source
from engineering.program.kernel import ExecutionContext, plan_for
from engineering.program.planning import generator_candidates

BIN = os.environ.get('MIRUM_N1C2_REAL_BIN')
pytestmark = [pytest.mark.skipif(os.name != 'posix' or not BIN, reason='needs posix and MIRUM_N1C2_REAL_BIN')]

PLATE = (Path(__file__).resolve().parents[1] / 'engineering' / 'fixtures' / 'editable_plate' / 'edge.mir').read_bytes()


def adapter():
    from engineering.program.adapters.native import NativePlateAdapter
    return NativePlateAdapter(binary=BIN, python=os.environ.get('MIRUM_N1C2_OCCT_PYTHON') or sys.executable)


def plan(width):
    source = PLATE.replace(b'backend = "occt"', b'backend = "mirum-native"').replace(b'80 mm', f'{width} mm'.encode())
    candidate = generator_candidates(compile_source(source).brief_content)[0]
    return plan_for('editable_plate', '1.0.0', 'mirum-native', candidate, ('step',))


def test_real_probe_and_builds(monkeypatch):
    monkeypatch.setenv('MIRUM_NATIVE_KERNEL', '1')
    kernel = adapter()
    probe = asyncio.run(kernel.probe())
    assert probe.available, probe.reasons
    ctx = ExecutionContext(deadline_s=60, max_artifact_bytes=1024 ** 2, workspace='n1c2-real')
    for width in (80, 100):
        built = asyncio.run(kernel.build(plan(width), ctx))
        assert built.result.outcome == 'completed' and built.result.domain_outcome == 'accepted', built.result
        assert built.result.claim_scope == 'geometry_checks_only' and built.result.backend == 'mirum-native'
        assert built.evaluation['report']['outcome'] == 'pass'
        assert len(built.evaluation['report']['bores']) == 4
