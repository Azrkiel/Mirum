"""K30 follow-ups to the F02 security review (all fail closed).

K30a SemanticId over-length is IR010 (identifier format), never IR012.
K30b _dumps_strict decides non-finite numbers from the values, not from CPython's message text.
K30c model_construct / model_copy(update=) / TypeAdapter bypass validation: banned outside program/.
K30d ExecutionLock and ConstructionStep take the budget pre-pass; ExecutionLock strings and SourceSpan
     positions are bounded.
"""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.program import ir as ir_module
from engineering.program.errors import ProgramError, SourceSpan
from engineering.program.hashing import execution_lock_sha256, semantic_sha256
from engineering.program.ir import (
    MAX_POSITION, ConstructionStep, ExecutionLock, ProgramIR, _dumps_strict, _validate_record,
    execution_lock, load_program_json,
)

ROOT = Path(__file__).resolve().parents[2]
P03 = ROOT / 'backend' / 'tests' / 'fixtures' / 'program' / 'p03' / 'language.program.json'
ENGINEERING = ROOT / 'backend' / 'engineering'
P03_SEMANTIC = '4d81808ade568a03fd196034652f3c4d1257b94623be178707202f91cd36c068'


def _program() -> dict:
    return json.loads(P03.read_text(encoding='utf-8'))


def _lock(**overrides) -> dict:
    data = dict(schema_version='mirum_execution_lock_v0_1', program_ir_version='mirum_program_ir_v0_1',
                semantics_version='mirum_semantics_v0_1', registry_version='mirum_operation_registry_v0_1',
                capability_id='rectangular_cantilever', capability_version='1.0.0',
                numerical_policy_version='beam_numerical_v1', resource_policy_version='engineering_resources_v1',
                backend='occt', exports=['step', 'glb'])
    data.update(overrides)
    return data


def _step(**overrides) -> dict:
    data = dict(id='body.sketch', operation='construct.sketch', operation_version='0.1.0', args={}, inputs=[])
    data.update(overrides)
    return data


def _code(excinfo) -> str:
    """The ProgramError code, raised directly or wrapped by pydantic."""
    error = excinfo.value
    if isinstance(error, ProgramError):
        return error.code
    for item in error.errors():
        inner = (item.get('ctx') or {}).get('error')
        if isinstance(inner, ProgramError):
            return inner.code
    return error.errors()[0]['type']


# ---------------------------------------------------------------- K30a

@pytest.mark.parametrize('where', ['requirement', 'analysis_ref'])
def test_semantic_id_over_length_is_ir010_not_ir012(where):
    data = _program()
    long_id = 'a' * 129
    if where == 'requirement':
        data['requirements']['requirements'][0]['id'] = long_id
    else:
        data['analysis'][0]['requirement_refs'] = [long_id]
    with pytest.raises(ProgramError) as excinfo:
        load_program_json(json.dumps(data))
    assert excinfo.value.code == 'IR010'


def test_semantic_id_at_limit_is_accepted_by_the_format_rule():
    data = _program()
    data['requirements']['requirements'][0]['id'] = 'a' * 128
    # The id no longer matches the analysis references, so the failure (if any) is a reference error.
    with pytest.raises(ProgramError) as excinfo:
        load_program_json(json.dumps(data))
    assert excinfo.value.code == 'IR004'


# ---------------------------------------------------------------- K30b

@pytest.mark.parametrize('bad', [math.nan, math.inf, -math.inf])
def test_dumps_strict_nonfinite_is_ir007(bad):
    with pytest.raises(ProgramError) as excinfo:
        _dumps_strict({'a': [1.0, {'b': bad}]})
    assert excinfo.value.code == 'IR007'


def test_dumps_strict_does_not_depend_on_the_json_error_wording(monkeypatch):
    class OtherWording:
        @staticmethod
        def dumps(*args, **kwargs):
            raise ValueError('a different interpreter wording')

    monkeypatch.setattr(ir_module, 'json', OtherWording)
    with pytest.raises(ProgramError) as excinfo:
        _dumps_strict({'x': math.nan})
    assert excinfo.value.code == 'IR007'
    with pytest.raises(ProgramError) as excinfo:
        _dumps_strict({'x': 1.0})
    assert excinfo.value.code == 'IR010'


def test_dumps_strict_circular_reference_is_ir010():
    loop: dict = {}
    loop['self'] = loop
    with pytest.raises(ProgramError) as excinfo:
        _dumps_strict(loop)
    assert excinfo.value.code == 'IR010'


def test_dumps_strict_unserializable_is_ir010():
    with pytest.raises(ProgramError) as excinfo:
        _dumps_strict({'x': object()})
    assert excinfo.value.code == 'IR010'


# ---------------------------------------------------------------- K30c

def test_model_construct_bypasses_validation_so_it_is_banned():
    # Documents why the static ban below matters: no validator runs.
    bypass = ProgramIR.model_construct(design='NOT A VALID IDENTIFIER')
    assert bypass.design == 'NOT A VALID IDENTIFIER'


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name == 'model_construct':
            found.append(f'{path.name}:{node.lineno} model_construct')
        elif name == 'model_copy' and any(k.arg == 'update' for k in node.keywords):
            found.append(f'{path.name}:{node.lineno} model_copy(update=)')
        elif name == 'TypeAdapter':
            found.append(f'{path.name}:{node.lineno} TypeAdapter')
    return found


def _imports_program(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if 'program' in module.split('.') or (node.level and module == '' and
                                                   any(a.name == 'program' for a in node.names)):
                return True
        elif isinstance(node, ast.Import) and any('program' in a.name.split('.') for a in node.names):
            return True
    return False


def test_no_validation_bypass_on_program_records_outside_the_package():
    program_dir = ENGINEERING / 'program'
    offenders = []
    for path in sorted(ENGINEERING.rglob('*.py')):
        if program_dir in path.parents or '__pycache__' in path.parts:
            continue
        if _imports_program(path):
            offenders.extend(_violations(path))
    assert offenders == []


def test_the_ban_scanner_finds_each_bypass(tmp_path):
    sample = tmp_path / 'sample.py'
    sample.write_text(
        'from engineering.program.ir import ProgramIR\n'
        'from pydantic import TypeAdapter\n'
        'a = ProgramIR.model_construct()\n'
        'b = a.model_copy(update={"design": "x"})\n'
        'c = a.model_copy()\n'
        'd = TypeAdapter(ProgramIR)\n', encoding='utf-8', newline='\n')
    assert _imports_program(sample)
    assert [v.split(' ', 1)[1] for v in _violations(sample)] == [
        'model_construct', 'model_copy(update=)', 'TypeAdapter']


# ---------------------------------------------------------------- K30d

def test_execution_lock_accepts_json_style_arrays_through_the_before_hook():
    lock = ExecutionLock.model_validate(_lock())
    assert lock.exports == ('step', 'glb')


@pytest.mark.parametrize('field', ['program_ir_version', 'semantics_version', 'registry_version',
                                   'capability_id', 'capability_version', 'numerical_policy_version',
                                   'resource_policy_version'])
def test_execution_lock_strings_are_bounded(field):
    assert ExecutionLock.model_validate(_lock(**{field: 'x' * 128}))
    with pytest.raises(ProgramError) as excinfo:
        _validate_record(ExecutionLock, _lock(**{field: 'x' * 129}), 'mirum_execution_lock_v0_1')
    assert excinfo.value.code == 'IR012'
    with pytest.raises(ProgramError) as excinfo:
        _validate_record(ExecutionLock, _lock(**{field: ''}), 'mirum_execution_lock_v0_1')
    assert excinfo.value.code == 'IR010'


def test_execution_lock_budget_prepass():
    deep: object = 'x'
    for _ in range(40):
        deep = [deep]
    with pytest.raises((ProgramError, ValidationError)) as excinfo:
        ExecutionLock.model_validate(_lock(exports=deep))
    assert _code(excinfo) == 'IR012'
    with pytest.raises((ProgramError, ValidationError)) as excinfo:
        ExecutionLock.model_validate(_lock(exports=['step'] * 300))
    assert _code(excinfo) == 'IR012'


def test_construction_step_budget_prepass():
    assert ConstructionStep.model_validate(_step(inputs=['a.b'])).inputs == ('a.b',)
    deep: object = 'x'
    for _ in range(40):
        deep = {'k': deep}
    with pytest.raises((ProgramError, ValidationError)) as excinfo:
        ConstructionStep.model_validate(_step(args={'a': deep}))
    assert _code(excinfo) == 'IR012'
    with pytest.raises((ProgramError, ValidationError)) as excinfo:
        ConstructionStep.model_validate(_step(inputs=['a'] * 300))
    assert _code(excinfo) == 'IR012'


@pytest.mark.parametrize('field', ['line', 'column', 'end_line', 'end_column'])
def test_source_span_positions_are_bounded(field):
    ok = dict(line=1, column=1, end_line=MAX_POSITION, end_column=MAX_POSITION)
    assert SourceSpan.model_validate(ok)
    too_big = dict(line=1, column=1, end_line=MAX_POSITION, end_column=MAX_POSITION)
    too_big[field] = MAX_POSITION + 1
    with pytest.raises(ValidationError):
        SourceSpan.model_validate(too_big)
    data = _program()
    data['span'] = too_big
    with pytest.raises(ProgramError) as excinfo:
        load_program_json(json.dumps(data))
    assert excinfo.value.code == 'IR010'


def test_max_position_covers_a_full_size_source():
    # 64 KiB of newlines has 65537 lines.
    assert MAX_POSITION >= 64 * 1024 + 1


def test_p03_semantic_hash_unchanged_and_lock_hash_matches_a_fresh_lock():
    program = load_program_json(P03.read_bytes())
    assert semantic_sha256(program) == P03_SEMANTIC
    lock = execution_lock(program)
    rebuilt = ExecutionLock.model_validate(json.loads(lock.model_dump_json()))
    assert execution_lock_sha256(rebuilt) == execution_lock_sha256(program)
