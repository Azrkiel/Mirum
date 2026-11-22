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


_BYPASS_NAMES = {'model_construct', 'model_copy', 'TypeAdapter', '__pydantic_validator__'}
# Names banned as a bare reference, alias, import or string constant (calls of these are labelled separately).
# `construct` is the deprecated pydantic v2 alias of model_construct (present on BaseModel in 2.9.2).
_REF_NAMES = _BYPASS_NAMES | {'setattr', 'import_module', '__import__', 'construct', 'vars', 'eval', 'exec'}
_STRING_NAMES = _REF_NAMES | {'__setattr__', '__delattr__', '__dict__', 'compile'}
_CALL_LABELLED = ('setattr', 'import_module', '__import__')
_DUNDER_ATTRS = {'__setattr__', '__delattr__'}
# Receivers on which the deprecated `.copy(...)` alias (which skips validation) is always flagged.
_IR_CLASS_NAMES = {n for n, v in vars(ir_module).items() if isinstance(v, type) and hasattr(v, 'model_fields')}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    found = []
    for node in ast.walk(tree):
        line = getattr(node, 'lineno', 0)
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:  # catches `X as Y` aliases
                if alias.name in _REF_NAMES:
                    found.append((line, f'import {alias.name}'))
                elif alias.name == '*' and (node.module or '').split('.')[0] == 'pydantic':
                    found.append((line, 'import * from pydantic'))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in _STRING_NAMES:
            found.append((line, f'string {node.value}'))  # getattr(x, 'model_construct') and friends
        elif isinstance(node, ast.Attribute):
            if node.attr == '__pydantic_validator__':
                found.append((line, '__pydantic_validator__'))
            elif (node.attr == '__setattr__' and isinstance(node.value, ast.Name)
                  and node.value.id == 'object'):
                found.append((line, 'object.__setattr__'))  # call or alias
            elif node.attr in _DUNDER_ATTRS:
                found.append((line, f'.{node.attr}'))  # type./super()./Model./alias, call or not
            elif node.attr == '__dict__':
                found.append((line, '__dict__'))
            elif node.attr in ('eval', 'exec', 'compile') and isinstance(node.value, ast.Name) \
                    and node.value.id == 'builtins':
                found.append((line, f'builtins.{node.attr}'))
            elif node.attr == 'copy' and isinstance(node.value, ast.Name) and node.value.id in _IR_CLASS_NAMES:
                found.append((line, 'IR class .copy'))
            elif node.attr in _REF_NAMES and id(node) not in call_funcs:
                found.append((line, f'reference {node.attr}'))
        elif isinstance(node, ast.Name):
            if node.id in _REF_NAMES and id(node) not in call_funcs:
                found.append((line, f'reference {node.id}'))
            elif node.id == 'compile':
                found.append((line, 'compile'))  # bare builtin (re.compile is an Attribute, not matched)
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in ('model_construct', 'construct'):
                found.append((line, name))
            elif name == 'model_copy' and (node.args or any(k.arg in ('update', None) for k in node.keywords)):
                found.append((line, 'model_copy(update=)'))  # keyword, **kwargs or positional
            elif name == 'copy' and any(k.arg == 'update' for k in node.keywords):
                found.append((line, 'copy(update=)'))
            elif name == 'TypeAdapter':
                found.append((line, 'TypeAdapter'))
            elif name in _CALL_LABELLED:
                found.append((line, name))
            elif name in ('vars', 'eval', 'exec'):
                found.append((line, name))
    return [f'{path.name}:{line} {what}' for line, what in sorted(found)]


def _program_literal(value: ast.AST | None, package: ast.AST | None = None) -> bool:
    """True for a string literal naming engineering.program (or under it), absolute or relative."""
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
        return False
    name = value.value
    # The parent package itself reaches program by attribute access (PR #137 re-review).
    if name == 'engineering' or name == 'engineering.program' or name.startswith('engineering.program.'):
        return True
    if name == 'program' or name.startswith('program.') or name.startswith('.program'):
        return (isinstance(package, ast.Constant) and isinstance(package.value, str)
                and package.value.split('.')[0] == 'engineering')
    return False


def _imports_program(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ''
            if 'program' in module.split('.') or (node.level and module == '' and
                                                   any(a.name == 'program' for a in node.names)):
                return True
            if module == 'engineering' and any(a.name == 'program' for a in node.names):
                return True
        elif isinstance(node, ast.Import) and any('program' in a.name.split('.') for a in node.names):
            return True
        elif (isinstance(node, ast.Attribute) and node.attr == 'program'
              and isinstance(node.value, ast.Name) and node.value.id == 'engineering'):
            return True  # `import engineering` then engineering.program
        elif isinstance(node, ast.Call):  # a literal dynamic load of the package reaches it without an import
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in ('import_module', '__import__') and node.args:
                package = node.args[1] if len(node.args) > 1 else next(
                    (k.value for k in node.keywords if k.arg == 'package'), None)
                if _program_literal(node.args[0], package):
                    return True
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
              and node.value.attr == 'modules' and isinstance(node.value.value, ast.Name)
              and node.value.value.id == 'sys' and _program_literal(node.slice)):
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
        'd = TypeAdapter(ProgramIR)\n'
        'from pydantic import TypeAdapter as T\n'
        'e = getattr(ProgramIR, "model_construct")()\n'
        'object.__setattr__(a, "design", "x")\n'
        'f = ProgramIR.__pydantic_validator__\n', encoding='utf-8', newline='\n')
    assert _imports_program(sample)
    assert [v.split(' ', 1)[1] for v in _violations(sample)] == [
        'import TypeAdapter', 'model_construct', 'model_copy(update=)', 'TypeAdapter', 'import TypeAdapter',
        'string model_construct', 'object.__setattr__', '__pydantic_validator__']


def _scan(tmp_path, source: str) -> list[str]:
    sample = tmp_path / 'snippet.py'
    sample.write_text(source, encoding='utf-8', newline='\n')
    return [v.split(' ', 1)[1] for v in _violations(sample)]


@pytest.mark.parametrize('source', [
    # K33-1: non-call references and aliases
    'import pydantic\nt = pydantic.TypeAdapter\n',
    'f = Model.model_construct\n',
    'v = Model.__pydantic_validator__\n',
    'from pydantic import TypeAdapter as T\n',
    'from pydantic import TypeAdapter\nt = TypeAdapter\n',
    'g = lambda m: m.model_construct\n',
    'h = Model.model_copy\n',
    'setter = object.__setattr__\n',
    'from pydantic import *\n',
    # K33-2: builtin setattr, including aliases
    'setattr(rec, "x", 1)\n',
    's = setattr\ns(rec, "x", 1)\n',
    'import builtins\nbuiltins.setattr(rec, "x", 1)\n',
    'f = getattr(builtins, "setattr")\n',
    # K33-3: dynamic loads
    'import importlib\nm = importlib.import_module("pydantic")\n',
    'from importlib import import_module\n',
    'm = __import__("pydantic")\n',
    'import pydantic\nt = getattr(pydantic, "TypeAdapter")\n',
    'c = getattr(x, "model_construct")\n',
    'c = getattr(x, "__pydantic_validator__")\n',
])
def test_the_ban_scanner_catches_k33_bypass_forms(tmp_path, source):
    assert _scan(tmp_path, source) != []


@pytest.mark.parametrize('source', [
    'import json\nx = json.loads("{}")\n',
    'a = rec.model_copy()\n',
    'a = rec.model_dump()\nb = Model.model_validate(a)\n',
    'from pydantic import BaseModel, ConfigDict\n',
    'import importlib\n',
    'v = getattr(obj, "name", None)\n',
    'class C:\n    def __setattr__(self, k, v):\n        raise AttributeError(k)\n',
    'object.__getattribute__(a, "x")\n',
    'name = "set_attribute"\n',
])
def test_the_ban_scanner_ignores_benign_code(tmp_path, source):
    assert _scan(tmp_path, source) == []


def _in_scope(tmp_path, source: str) -> bool:
    sample = tmp_path / 'scope.py'
    sample.write_text(source, encoding='utf-8', newline='\n')
    return _imports_program(sample)


@pytest.mark.parametrize('source', [
    'import importlib\nm = importlib.import_module("engineering.program.ir")\n',
    'from importlib import import_module\nm = import_module("engineering.program")\n',
    'm = __import__("engineering.program")\n',
    'import sys\nm = sys.modules["engineering.program"]\n',
    'import importlib\nm = importlib.import_module(".program.ir", package="engineering")\n',
    'import importlib\nm = importlib.import_module(".program", "engineering.x")\n',
    'from engineering import program\n',
    'from engineering import program as p\n',
    'from engineering.program import ir\n',
    'import engineering.program.ir\n',
    # PR #137 re-review MEDIUM: the parent package reaches program by attribute access.
    'import importlib\nm = importlib.import_module("engineering").program\n',
    'm = __import__("engineering").program\n',
    'import sys\nm = sys.modules["engineering"].program\n',
    'import engineering\nm = engineering.program\n',
])
def test_scope_includes_files_that_reach_the_package_dynamically(tmp_path, source):
    assert _in_scope(tmp_path, source)


@pytest.mark.parametrize('source', [
    'import json\n',
    'from engineering import grounding\n',
    'import re\np = re.compile("x")\n',
    'import importlib\nm = importlib.import_module(name)\n',
    'import importlib\nm = importlib.import_module(self.module)\n',
    'import importlib\nm = importlib.import_module("cem.api")\n',
    'import importlib\nm = importlib.import_module("program")\n',
    'import importlib\nm = importlib.import_module(".program", package=pkg)\n',
    'import sys\nm = sys.modules[name]\n',
    'import sys\nm = sys.modules["cem.api"]\n',
    'import sys\nx = sys.modules\n',
    'from sys import modules\n',
])
def test_scope_excludes_unrelated_files(tmp_path, source):
    assert not _in_scope(tmp_path, source)


def test_a_literal_program_load_is_in_scope_and_flagged(tmp_path):
    source = 'import importlib\nm = importlib.import_module("engineering.program.ir")\n'
    assert _in_scope(tmp_path, source)
    assert _scan(tmp_path, source) == ['import_module']


@pytest.mark.parametrize('source', [
    # K33 round 2, gap 2: any __setattr__ / __delattr__ reference
    'type.__setattr__(rec, "x", 1)\n',
    'class C(Base):\n    def f(self):\n        super().__setattr__("x", 1)\n',
    's = Model.__setattr__\n',
    'object.__delattr__(rec, "x")\n',
    'd = rec.__delattr__\n',
    'f = getattr(rec, "__setattr__")\n',
    # gap 3: deprecated pydantic v2 aliases
    'a = ProgramIR.construct()\n',
    'c = ProgramIR.construct\n',
    'a = rec.copy(update={"design": "x"})\n',
    'a = ProgramIR.copy(rec)\n',
    'a = ExecutionLock.copy\n',
    # gap 4: __dict__ and vars
    'd = rec.__dict__\n',
    'rec.__dict__["x"] = 1\n',
    'd = vars(rec)\n',
    'v = vars\n',
    # gap 5: eval / exec / compile
    'eval("1")\n',
    'exec("x = 1")\n',
    'c = compile("1", "f", "eval")\n',
    'f = eval\n',
    'import builtins\nbuiltins.exec("x = 1")\n',
    # gap 6: model_copy however the arguments arrive
    'a = rec.model_copy(**kw)\n',
    'a = rec.model_copy({"x": 1})\n',
    'a = rec.model_copy(deep=True, update=u)\n',
    'f = rec.model_copy\n',
])
def test_the_ban_scanner_catches_k33_round2_forms(tmp_path, source):
    assert _scan(tmp_path, source) != []


@pytest.mark.parametrize('source', [
    'a = rec.model_copy(deep=True)\n',
    'import re\np = re.compile("x")\n',
    'a = d.copy()\nb = arr.copy()\n',
    'class C:\n    def __setattr__(self, k, v):\n        pass\n    def __delattr__(self, k):\n        pass\n',
    'x = obj.__class__\n',
    'x = obj.dict_value\n',
])
def test_the_ban_scanner_ignores_benign_round2_code(tmp_path, source):
    assert _scan(tmp_path, source) == []


# Known static-scan limits (recorded, not fixed): dynamic loads with a non-literal argument (capabilities.py,
# proposal_contract.py, proposer.py, ...) do not pull a file into scope, only a literal engineering.program load
# or sys.modules['engineering.program...'] does; string obfuscation ("model_" + "construct", "".join(...),
# bytes), `from builtins import *`, and the scan root, which is limited to backend/engineering.
# `.copy(` is ambiguous (dict/numpy), so it is flagged only with update= or on a known IR class receiver;
# `.construct` has no benign use here and is flagged everywhere.


def test_dumps_strict_nonfinite_dict_key_is_ir007():
    with pytest.raises(ProgramError) as excinfo:
        _dumps_strict({math.nan: 1})
    assert excinfo.value.code == 'IR007'


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
