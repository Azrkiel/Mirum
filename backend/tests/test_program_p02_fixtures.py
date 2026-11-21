"""P02 acceptance corpus for the ProgramIR loader and operation registry."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Literal, TypedDict

import pytest
from pydantic import TypeAdapter, ValidationError

from engineering.program.errors import ProgramError
from engineering.program.ir import ConstructionGraph, Value, load_program_json
from engineering.program.registry import (
    DEFAULT_REGISTRY, load_artifact_graph_json, validate_op_stream,
)
from engineering.program.units import normalize


class ExpectedError(TypedDict):
    code: str
    field: str | None


class FixtureCase(TypedDict):
    id: str
    file: str
    kind: Literal['program', 'artifact_graph', 'construction', 'call', 'quantity', 'executable']
    expect: Literal['accept'] | ExpectedError
    note: str


FIXTURE_DIR = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'p02'
CALL_ARGS = TypeAdapter(dict[str, Value])


def _load_cases(fixture_dir: Path) -> list[FixtureCase]:
    return json.loads((fixture_dir / 'cases.json').read_bytes())


CASES = _load_cases(FIXTURE_DIR)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProgramError('IR003', 'duplicate object key', key)
        result[key] = value
    return result


def _reject_nonfinite(literal: str) -> None:
    raise ProgramError('IR007', f'non-finite number literal {literal}')


def _load_construction(data: bytes) -> ConstructionGraph:
    json.loads(data, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite)
    return ConstructionGraph.model_validate_json(data)


def _run_fixture(case: FixtureCase, fixture_dir: Path) -> None:
    data = (fixture_dir / case['file']).read_bytes()
    match case['kind']:
        case 'program':
            load_program_json(data)
        case 'artifact_graph':
            graph = load_artifact_graph_json(data)
            validate_op_stream(graph.op_stream)
        case 'construction':
            DEFAULT_REGISTRY.validate_construction(_load_construction(data))
        case 'call':
            call = json.loads(data)
            args = CALL_ARGS.validate_json(json.dumps(call['args']))
            DEFAULT_REGISTRY.check_call(call['operation'], call['version'], args)
        case 'quantity':
            quantity = json.loads(data)
            normalize(quantity['value'], quantity['unit'], quantity['expected'])
        case 'executable':
            call = json.loads(data)
            DEFAULT_REGISTRY.require_executable(call['operation'], call['version'])
        case _:
            raise AssertionError(f"Unknown fixture kind {case['kind']!r}")


def _assert_case(case: FixtureCase, fixture_dir: Path) -> None:
    actual: Literal['accept'] | ExpectedError
    try:
        _run_fixture(case, fixture_dir)
    except (ProgramError, ValidationError) as exc:
        error = exc
        if isinstance(exc, ValidationError):
            first = exc.errors(include_url=False)[0]
            if 'error' not in first.get('ctx', {}):
                raise
            error = first['ctx']['error']
        assert isinstance(error, ProgramError), repr(error)
        actual = {'code': error.code, 'field': error.field}
    else:
        actual = 'accept'
    assert actual == case['expect'], f"{case['id']}: {case['note']}"


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_p02_fixture(case: FixtureCase) -> None:
    _assert_case(case, FIXTURE_DIR)


def test_p02_manifest_lists_every_fixture_once() -> None:
    listed = [case['file'] for case in CASES]
    on_disk = {
        path.relative_to(FIXTURE_DIR).as_posix()
        for path in FIXTURE_DIR.rglob('*')
        if path.is_file() and path != FIXTURE_DIR / 'cases.json'
    }
    assert len(listed) == len(set(listed))
    assert set(listed) == on_disk


def test_p02_manifest_covers_required_errors() -> None:
    required = {'IR001', 'IR002', 'IR003', 'IR004', 'IR005', 'IR006', 'IR007', 'IR008'}
    expectations = [case['expect'] for case in CASES]
    covered = {expect['code'] for expect in expectations if expect != 'accept'}
    assert required <= covered


def test_p02_corpus_runs_before_native_work() -> None:
    code = (
        "import runpy, sys\n"
        "from pathlib import Path\n"
        "test_file, fixture_dir = map(Path, sys.argv[1:])\n"
        "sys.path[:0] = [str(test_file.parents[2]), str(test_file.parents[1])]\n"
        "suite = runpy.run_path(str(test_file))\n"
        "for case in suite['_load_cases'](fixture_dir):\n"
        "    suite['_assert_case'](case, fixture_dir)\n"
        "bad = [m for m in ('cadquery', 'OCP', 'google.genai', 'fastapi', 'httpx') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, '-c', code, str(Path(__file__).resolve()), str(FIXTURE_DIR)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
