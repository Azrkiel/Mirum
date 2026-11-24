"""F04: formatter round trips, beam lowering to ProgramIR (P03 equality with the F02 fixtures),
semantic failures with spans, no-evaluation guarantees and import isolation."""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engineering.language import MelError, format_program, format_source, parse
from engineering.language.diagnostics import MEL_S_IR_CODES
from engineering.language.lower_beam import compile_beam
from engineering.program.beam_bridge import brief_content_from_program, program_from_brief_content
from engineering.program.hashing import execution_lock_sha256, semantic_sha256
from engineering.program.ir import ProgramProvenance, load_program_json

ROOT = Path(__file__).resolve().parents[2]
P03 = ROOT / 'backend' / 'tests' / 'fixtures' / 'program' / 'p03'
MIR = P03 / 'beam.mir'  # byte-identical copy of the documented example (test_program_beam_bridge pins it to the docs copy)
LANGUAGE_DIR = ROOT / 'backend' / 'engineering' / 'language'
BS = chr(92)


def beam_source() -> str:
    return MIR.read_text(encoding='utf-8')


def replace(old: str, new: str) -> str:
    text = beam_source()
    assert old in text
    return text.replace(old, new, 1)


def form_program():
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.program.hashing import source_sha256
    payload = json.loads((P03 / 'form.json').read_text(encoding='utf-8'))
    content, issues = BriefService(MemoryBriefRepository()).validate(payload)
    assert content is not None and not issues
    return program_from_brief_content(content, provenance=ProgramProvenance(
        kind='form', source_sha256=source_sha256(payload), source_id='form.json'))


# ---------------------------------------------------------------- formatter

def test_beam_example_round_trips_through_the_formatter():
    original = parse(beam_source())
    formatted = format_program(original)
    assert parse(formatted) == original
    assert format_source(formatted) == formatted


def test_formatter_is_idempotent_on_messy_layout():
    messy = ('// c\r\nlanguage   mirum "0.1" ;design d{a=1.50e3mm;//x\r\n b = [ [ ] , "q" ,2 N ];'
             '  c = "' + BS + 'u00e9' + BS + 't' + BS + 'u202e' + BS + 'u007f";}')
    once = format_source(messy)
    assert format_source(once) == once
    assert parse(once) == parse(messy)
    assert once.isascii()
    assert 'a = 1.50e3 mm;' in once and 'b = [[], "q", 2 N];' in once


def test_whitespace_and_comments_do_not_change_the_tree():
    a = parse(beam_source())
    b = parse(beam_source().replace('\n', '  // note\r\n').replace(' = ', '='))
    assert a == b


# ---------------------------------------------------------------- P03 lowering

def test_beam_mir_lowers_to_the_f02_p03_semantics():
    raw = MIR.read_bytes()
    ir = compile_beam(raw, source_id='examples/beam.mir')
    fixture = load_program_json((P03 / 'language.program.json').read_bytes())
    form = form_program()
    assert semantic_sha256(ir) == semantic_sha256(fixture) == semantic_sha256(form)
    assert execution_lock_sha256(ir) == execution_lock_sha256(fixture) == execution_lock_sha256(form)
    brief = brief_content_from_program(ir)
    assert brief.brief_sha256 == brief_content_from_program(form).brief_sha256
    assert ir.provenance.kind == 'language'
    assert ir.provenance.source_sha256 == hashlib.sha256(raw).hexdigest() == fixture.provenance.source_sha256
    assert {r.id: r.span for r in ir.requirements.requirements} == {
        r.id: r.span for r in fixture.requirements.requirements}
    assert ir.candidates.parameters[0].span == fixture.candidates.parameters[0].span
    assert ir.span == fixture.span


def test_layout_and_equivalent_units_keep_the_semantic_hash():
    base = compile_beam(beam_source())
    formatted = compile_beam(format_source(beam_source()))
    metres = compile_beam(replace('length = 200 mm;', 'length = 0.2 m;').replace(
        'tip_force = 50 N;', 'tip_force = 0.05 kN;'))
    assert semantic_sha256(formatted) == semantic_sha256(base) == semantic_sha256(metres)
    assert formatted.provenance.source_sha256 != base.provenance.source_sha256


def test_meaning_changes_change_the_semantic_hash():
    base = compile_beam(beam_source())
    assert semantic_sha256(compile_beam(replace('tip_force = 50 N;', 'tip_force = 60 N;'))) != semantic_sha256(base)
    changed = compile_beam(replace('[5 mm, 10 mm, 15 mm]', '[5 mm, 10 mm, 20 mm]'))
    assert semantic_sha256(changed) != semantic_sha256(base)


SEMANTIC = [
    ('unknown-field', ('mode = ', 'colour = "red";\n  mode = '), 'IR002', 'colour', 20),
    ('missing-field', ('  tip_force = 50 N;\n', ''), 'IR010', 'tip_force', 4),
    ('unit-mismatch', ('tip_force = 50 N;', 'tip_force = 50 mm;'), 'IR006', 'tip_force', 13),
    ('unknown-unit', ('tip_force = 50 N;', 'tip_force = 50 lbf;'), 'IR005', 'tip_force', 13),
    ('unitless-length', ('length = 200 mm;', 'length = 200;'), 'IR006', 'length', 10),
    ('string-for-quantity', ('width = 10 mm;', 'width = "10 mm";'), 'IR010', 'width', 11),
    ('quantity-for-string', ('mode = "bounded_thickness";', 'mode = 3;'), 'IR010', 'mode', 20),
    ('source-kind-case', ('"illustrative";', '"Illustrative";'), 'IR010', 'material_source_kind', 18),
    ('source-kind-unknown', ('"illustrative";', '"certified";'), 'IR010', 'material_source_kind', 18),
    ('mass-is-not-force', ('tip_force = 50 N;', 'tip_force = 5 kg;'), 'IR006', 'tip_force', 13),
    ('heights-not-list', ('[5 mm, 10 mm, 15 mm]', '5 mm'), 'IR010', 'candidate_heights', 21),
    ('too-many-heights', ('[5 mm, 10 mm, 15 mm]', '[5 mm, 10 mm, 15 mm, 20 mm, 25 mm]'), 'IR010',
     'candidate_heights', 21),
    ('backend-picogk', ('backend = "occt";', 'backend = "picogk";'), 'IR010', 'backend', 7),
    ('exports-extra', ('["step", "glb"]', '["step", "glb", "zip"]'), 'IR010', 'exports', 22),
    ('wrong-capability', ('"rectangular_cantilever"', '"rectangular_plate"'), 'IR010', 'capability', 5),
    ('design-name-uppercase', ('design reference_beam', 'design Reference_beam'), 'IR010', 'design', 4),
]


MEL_S = {'unknown-field': 'MEL-S002', 'missing-field': 'MEL-S003', 'unit-mismatch': 'MEL-S005', 'unknown-unit': 'MEL-S004', 'unitless-length': 'MEL-S006', 'string-for-quantity': 'MEL-S008', 'quantity-for-string': 'MEL-S008', 'source-kind-case': 'MEL-S001', 'source-kind-unknown': 'MEL-S001', 'mass-is-not-force': 'MEL-S005', 'heights-not-list': 'MEL-S008', 'too-many-heights': 'MEL-S014', 'backend-picogk': 'MEL-S012', 'exports-extra': 'MEL-S013', 'wrong-capability': 'MEL-S011', 'design-name-uppercase': 'MEL-S010'}


@pytest.mark.parametrize('case,edit,code,field,line', SEMANTIC, ids=[c[0] for c in SEMANTIC])
def test_semantic_failure_points_at_the_field(case, edit, code, field, line):
    with pytest.raises(MelError) as excinfo:
        compile_beam(replace(*edit))
    d = excinfo.value.diagnostic
    assert (d.field, d.span.start.line) == (field, line)
    assert d.category == 'semantic'
    mel = MEL_S[case]
    assert d.code == mel
    assert MEL_S_IR_CODES[mel] == code
    assert d.ir_code == code


def test_lexical_and_syntax_errors_stop_before_lowering():
    with pytest.raises(MelError) as excinfo:
        compile_beam(replace('tip_force = 50 N;', 'tip_force = 1e400 N;'))
    assert excinfo.value.diagnostic.code == 'MEL-L012'
    with pytest.raises(MelError) as excinfo:
        compile_beam(replace('  width = 10 mm;\n', '  width = 10 mm;\n  width = 11 mm;\n'))
    assert excinfo.value.diagnostic.code == 'MEL-P006'


@pytest.mark.parametrize('hostile', [
    'language mirum "0.1"; design d { x = __import__("os").system("id"); }',
    'language mirum "0.1"; design d { x = eval("1+1"); }',
    'language mirum "0.1"; design d { x = open("/etc/passwd"); }',
    'import os\nos.system("id")',
    'language mirum "0.1"; design d { x = lambda: 1; }',
    'language mirum "0.1"; design d { for x in y: pass }',
])
def test_python_like_input_is_rejected_without_evaluation(hostile):
    with pytest.raises(MelError):
        compile_beam(hostile)


# ---------------------------------------------------------------- guarantees

BANNED_CALLS = {'eval', 'exec', 'compile', '__import__', 'open', 'input', 'getattr', 'setattr', 'globals',
                'locals', 'vars'}
BANNED_MODULES = {'importlib', 'pickle', 'marshal', 'socket', 'subprocess', 'os', 'shutil', 'ctypes',
                  'urllib', 'http', 'requests', 'httpx'}


def test_language_package_never_evaluates_or_touches_io():
    for path in sorted(LANGUAGE_DIR.glob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in BANNED_CALLS, f'{path.name}:{node.lineno} {node.func.id}'
            if isinstance(node, ast.Attribute):
                assert node.attr not in {'literal_eval', 'loads_python', 'system', 'popen'}, path.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split('.')[0] not in BANNED_MODULES, f'{path.name} imports {alias.name}'
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split('.')[0] not in BANNED_MODULES, f'{path.name} imports {node.module}'


def test_language_sources_are_ascii():
    for path in sorted(LANGUAGE_DIR.glob('*.py')):
        assert path.read_bytes().isascii(), path.name


def test_import_and_compile_load_no_native_provider_or_web_module():
    code = (
        'import sys, json\n'
        'import engineering.language\n'
        'after_import = sorted(m for m in ("cadquery", "OCP", "google.genai", "fastapi", "httpx") if m in sys.modules)\n'
        'from engineering.language.lower_beam import compile_beam\n'
        'compile_beam(open(sys.argv[1], "rb").read())\n'
        'after_compile = sorted(m for m in ("cadquery", "OCP", "google.genai", "fastapi", "httpx") if m in sys.modules)\n'
        'print(json.dumps([after_import, after_compile]))\n')
    env_path = f'{ROOT / "backend"}{";" if sys.platform == "win32" else ":"}{ROOT}'
    import os
    env = dict(os.environ, PYTHONPATH=env_path)
    out = subprocess.run([sys.executable, '-c', code, str(MIR)], capture_output=True, text=True, env=env,
                         timeout=120, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [[], []]


# ---------------------------------------------------------------- security review fixes (PR #133)

@pytest.mark.parametrize('code_point', [0x00, 0x1F, 0x7F, 0x202E, 0x2066, 0x200B, 0x061C, 0x2028, 0xFEFF])
def test_escaped_control_and_format_characters_are_rejected_in_text_fields(code_point):
    escaped = BS + 'u' + format(code_point, '04x')
    with pytest.raises(MelError) as excinfo:
        compile_beam(replace('"ideal_clamp_end_force_linear_elastic"', '"ideal' + escaped + '"'))
    d = excinfo.value.diagnostic
    assert (d.code, d.ir_code, d.field, d.span.start.line) == ('MEL-S009', 'IR010', 'assumptions', 19)


def test_escaped_printable_non_ascii_is_still_a_string():
    escaped = BS + 'u00e9'
    with pytest.raises(MelError) as excinfo:  # still not a known assumptions value, but not a control error
        compile_beam(replace('"ideal_clamp_end_force_linear_elastic"', '"caf' + escaped + '"'))
    assert 'control' not in excinfo.value.diagnostic.message


def test_requirement_map_covers_every_bridge_requirement():
    from engineering.language.lower_beam import REQUIREMENT_FIELDS
    from engineering.program.beam_bridge import _REGION_REQUIREMENTS, _REQUIREMENTS
    assert set(REQUIREMENT_FIELDS) == {rid for rid, _ in _REQUIREMENTS + _REGION_REQUIREMENTS}


@pytest.mark.parametrize('bad', ['', '../x.mir', 'a/../b', '/abs.mir', 'a//b', 'C:' + BS + 'x', 'a b', 'x' * 129,
                                 'a/./b'])
def test_source_id_is_a_validated_label(bad):
    with pytest.raises(ValueError):
        compile_beam(beam_source(), source_id=bad)


def test_source_id_examples_path_is_accepted():
    assert compile_beam(beam_source(), source_id='examples/beam.mir').provenance.source_id == 'examples/beam.mir'
