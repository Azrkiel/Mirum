"""F05: the general MEL v0.1 semantic compiler (compile_source): MEL-S codes, deadline, K32, K34, P03 pins."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from engineering.language import CompiledProgram, MelError, compile_source
from engineering.language.diagnostics import HINTS, MEL_S_IR_CODES, SEMANTIC
from engineering.language.lower_beam import compile_beam
from engineering.program.hashing import execution_lock_sha256

ROOT = Path(__file__).resolve().parents[2]
P03_MIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'program' / 'p03' / 'beam.mir'
DOC_MIR = ROOT / 'docs' / 'plans' / 'checked-engineering-v1' / 'phase-3-framework-language' / 'examples' / 'beam.mir'
BS = chr(92)


def beam_source() -> str:
    return P03_MIR.read_text(encoding='utf-8')


def replace(old: str, new: str) -> str:
    text = beam_source()
    assert old in text
    return text.replace(old, new, 1)


def fail(source, **kw) -> MelError:
    with pytest.raises(MelError) as excinfo:
        compile_source(source, **kw)
    return excinfo.value


# ---------------------------------------------------------------- table

def test_table_is_complete_and_ascii():
    assert set(SEMANTIC) == set(MEL_S_IR_CODES) == {f'MEL-S{i:03d}' for i in range(1, 20)}
    for code in SEMANTIC:
        assert code in HINTS and HINTS[code].isascii() and SEMANTIC[code].isascii()
    assert MEL_S_IR_CODES['MEL-S016'] is None and MEL_S_IR_CODES['MEL-S018'] is None
    with pytest.raises(TypeError):
        MEL_S_IR_CODES['MEL-S001'] = 'x'


CASES = [
    ('MEL-S001', ('"illustrative";', '"certified";'), 'material_source_kind', 18),
    ('MEL-S002', ('mode = ', 'colour = "red";\n  mode = '), 'colour', 20),
    ('MEL-S003', ('  tip_force = 50 N;\n', ''), 'tip_force', 4),
    ('MEL-S004', ('tip_force = 50 N;', 'tip_force = 50 lbf;'), 'tip_force', 13),
    ('MEL-S005', ('tip_force = 50 N;', 'tip_force = 5 kg;'), 'tip_force', 13),
    ('MEL-S006', ('length = 200 mm;', 'length = 200;'), 'length', 10),
    ('MEL-S008', ('width = 10 mm;', 'width = "10 mm";'), 'width', 11),
    ('MEL-S009', ('"ideal_clamp_end_force_linear_elastic"', '"a' + BS + 'u202e"'), 'assumptions', 19),
    ('MEL-S010', ('design reference_beam', 'design Reference_beam'), 'design', 4),
    ('MEL-S011', ('"rectangular_cantilever"', '"rectangular_plate"'), 'capability', 5),
    ('MEL-S012', ('backend = "occt";', 'backend = "picogk";'), 'backend', 7),
    ('MEL-S013', ('["step", "glb"]', '["step", "glb", "zip"]'), 'exports', 22),
    ('MEL-S014', ('[5 mm, 10 mm, 15 mm]', '[5 mm, 5 mm]'), 'candidate_heights', 21),
    ('MEL-S014', ('[5 mm, 10 mm, 15 mm]', '[]'), 'candidate_heights', 21),
]


@pytest.mark.parametrize('mel,edit,field,line', CASES)
def test_mel_s_code(mel, edit, field, line):
    d = fail(replace(*edit)).diagnostic
    assert (d.code, d.category, d.field, d.span.start.line) == (mel, 'semantic', field, line)
    assert d.ir_code == MEL_S_IR_CODES[mel] and d.hint == HINTS[mel]


def test_s007_non_finite_literal_is_lexical_and_unit_overflow_is_semantic():
    assert fail(replace('tip_force = 50 N;', 'tip_force = 1e400 N;')).diagnostic.code == 'MEL-L012'
    d = fail(replace('length = 200 mm;', 'length = 1e308 m;')).diagnostic
    assert (d.code, d.ir_code, d.field, d.span.start.line) == ('MEL-S007', 'IR007', 'length', 10)


def test_s015_value_rejected_by_brief_validation():
    d = fail(replace('width = 10 mm;', 'width = 100000 mm;')).diagnostic
    assert d.code == 'MEL-S015' and d.ir_code == 'IR010' and d.category == 'semantic'
    assert d.span.start.line >= 4


def test_s014_envelope_from_brief_validation():
    d = fail(replace('[5 mm, 10 mm, 15 mm]', '[5 mm, 10 mm, 99999 mm]')).diagnostic
    assert (d.code, d.field) == ('MEL-S014', 'candidate_heights')


def test_s016_bad_source_id_in_span_zero():
    d = fail(beam_source(), source_id='../x').diagnostic
    assert (d.code, d.field, d.ir_code) == ('MEL-S016', 'source_id', None)
    assert (d.span.start.offset, d.span.start.line, d.span.start.column) == (0, 1, 1)


@pytest.mark.parametrize('bad', ['', '../x.mir', 'a/../b', '/abs.mir', 'a//b', 'C:' + BS + 'x', 'a b', 'x' * 129,
                                 'a/./b'])
def test_k34_bad_source_id_raises_mel_error(bad):
    with pytest.raises(MelError) as excinfo:
        compile_source(beam_source(), source_id=bad)
    assert excinfo.value.diagnostic.code == 'MEL-S016'


@pytest.mark.parametrize('cap', ['regen_chamber', 'glider_cem', 'rectangular_plate', 'co2_dragster'])
def test_unsupported_capabilities(cap):
    d = fail(replace('"rectangular_cantilever"', f'"{cap}"')).diagnostic
    assert (d.code, d.field, d.span.start.line) == ('MEL-S011', 'capability', 5)


def test_unsupported_capability_version():
    d = fail(replace('capability_version = "1.0.0"', 'capability_version = "2.0.0"')).diagnostic
    assert (d.code, d.field) == ('MEL-S011', 'capability_version')


def test_missing_capability_is_s003_and_wrong_kind_is_s008():
    text = beam_source()
    line = next(row for row in text.split('\n') if row.strip().startswith('capability ='))
    assert fail(text.replace(line + '\n', '')).diagnostic.code == 'MEL-S003'
    assert fail(text.replace('"rectangular_cantilever"', '3')).diagnostic.code == 'MEL-S008'


def test_missing_or_non_string_material_source_kind_is_s001():
    text = beam_source()
    line = next(row for row in text.split('\n') if row.strip().startswith('material_source_kind ='))
    d = fail(text.replace(line + '\n', '')).diagnostic
    assert (d.code, d.field, d.ir_code) == ('MEL-S001', 'material_source_kind', 'IR010')
    d = fail(replace('"illustrative";', '3;')).diagnostic
    assert (d.code, d.field) == ('MEL-S001', 'material_source_kind')


def test_nested_list_where_scalar_expected_is_s008():
    assert fail(replace('width = 10 mm;', 'width = [[1 mm]];')).diagnostic.code == 'MEL-S008'


# ---------------------------------------------------------------- K32 and S019

def test_k32_round_trip_mismatch_fails_closed_before_any_hash(monkeypatch):
    import engineering.program.beam_bridge as bridge
    import engineering.program.hashing as hashing
    real = bridge.brief_content_from_program

    def other(ir, **kw):
        content = real(ir, **kw)
        return content.model_copy(update={'candidate_heights_mm': (1.0,)})

    def boom(*a, **k):
        raise AssertionError('hash computed')
    monkeypatch.setattr(bridge, 'brief_content_from_program', other)
    monkeypatch.setattr(hashing, 'source_sha256', boom)
    monkeypatch.setattr(hashing, 'semantic_sha256', boom)
    d = fail(beam_source()).diagnostic
    assert (d.code, d.ir_code) == ('MEL-S017', 'IR010')


def test_s019_defensive_bridge_error(monkeypatch):
    import engineering.program.beam_bridge as bridge
    from engineering.program.errors import ProgramError

    def bad(*a, **k):
        raise ProgramError('IR010', 'boom', 'design')
    monkeypatch.setattr(bridge, 'program_from_brief_content', bad)
    assert fail(beam_source()).diagnostic.code == 'MEL-S019'


# ---------------------------------------------------------------- deadline

@pytest.mark.parametrize('bad', [0, -1, 2.0001, float('nan'), float('inf'), True, '1'])
def test_bad_deadline_is_value_error(bad):
    with pytest.raises(ValueError) as excinfo:
        compile_source(beam_source(), deadline_s=bad)
    assert not isinstance(excinfo.value, MelError)


class Scripted:
    """Clock: the first call (start) returns 0; call number `at` (1-based after start) and later return `late`."""

    def __init__(self, at=None, late=0.0):
        self.at, self.late, self.n = at, late, 0

    def __call__(self):
        self.n += 1
        if self.n == 1 or self.at is None or self.n < self.at + 1:
            return 0.0
        return self.late


def test_exactly_at_the_limit_passes():
    clock = Scripted(at=1, late=2.0)
    assert compile_source(beam_source(), clock=clock).ir is not None
    assert clock.n > 10


def test_every_check_point_fails_just_over_the_limit():
    counter = Scripted()
    compile_source(beam_source(), clock=counter)
    total = counter.n
    assert total > 40  # stages, every assignment (twice) and every list element
    lines = set()
    for at in range(1, total):
        d = fail(beam_source(), clock=Scripted(at=at, late=2.0000001)).diagnostic
        assert (d.code, d.category, d.ir_code) == ('MEL-S018', 'resource', None)
        lines.add(d.span.start.line)
    assert 21 in lines  # the candidate_heights assignment or its elements
    assert len(lines) > 10  # per-assignment checks point at the assignment


def test_deadline_span_is_design_span_at_stage_boundary():
    d = fail(beam_source(), clock=Scripted(at=1, late=3.0)).diagnostic
    assert d.code == 'MEL-S018' and d.span.start.line == 4


def test_time_spent_in_parse_counts_against_the_deadline(monkeypatch):
    # Security review LOW-1 (PR #138): the deadline is checked right after parsing.
    import engineering.language.semantic as semantic
    state = {'now': 0.0}
    real_parse = semantic.parse

    def slow_parse(data):
        result = real_parse(data)
        state['now'] = 2.5
        return result

    monkeypatch.setattr(semantic, 'parse', slow_parse)
    d = fail(beam_source(), clock=lambda: state['now']).diagnostic
    assert d.code == 'MEL-S018' and d.category == 'resource'


def test_deadline_per_list_element():
    longer = Scripted()
    compile_source(replace('[5 mm, 10 mm, 15 mm]', '[5 mm, 10 mm, 15 mm, 20 mm]'), clock=longer)
    base = Scripted()
    compile_source(beam_source(), clock=base)
    assert longer.n == base.n + 1


def test_host_worst_case_is_fast():
    elems = ', '.join(f'{i + 1} mm' for i in range(256))
    big = replace('[5 mm, 10 mm, 15 mm]', f'[{elems}]')
    pad = '//' + 'x' * 70 + '\n'
    near_limit = pad * 850 + beam_source()
    assert len(near_limit.encode()) < 64 * 1024
    many = 'language mirum "0.1"; design d { ' + ' '.join(['a = [1 mm];'] * 5000) + '}'
    for src in (big, near_limit, many):
        t = time.monotonic()
        try:
            compile_source(src)
        except MelError as exc:
            assert exc.diagnostic.code != 'MEL-S018'
        assert time.monotonic() - t < 2.0


# ---------------------------------------------------------------- P03 pins

# The native CI image ships backend/ but not docs/; there the byte-identical p03 copy is pinned alone
# (test_program_beam_bridge.py::test_fixture_beam_mir_is_the_documented_example ties the two together).
@pytest.mark.parametrize('path', [P03_MIR] + ([DOC_MIR] if DOC_MIR.exists() else []))
def test_p03_pins(path):
    raw = path.read_bytes()
    out = compile_source(raw)
    assert isinstance(out, CompiledProgram) and out.diagnostics == ()
    assert out.semantic_sha256 == '4d81808ade568a03fd196034652f3c4d1257b94623be178707202f91cd36c068'
    assert execution_lock_sha256(out.ir) == '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e'
    assert out.brief_sha256 == 'f5a1f7e246eeef16f097335779bfeef9f3dbdb903016b31b49f1d757d65a9a29'
    assert out.source_sha256 == 'b79bff31b61c50864c32d498ea99bebfbf58d3fd2af7206cc85900db4c3ce69f'
    assert out.language_version == '0.1' and out.compiler_version == 'mirum_mel_compiler_v0_1'
    assert out.brief_content.brief_sha256 == out.brief_sha256
    assert out.spans[-1][0] == 'design' and out.spans[0][0] == 'capability'
    assert compile_beam(raw) == out.ir


def test_layout_edits_keep_semantic_and_change_source_hash():
    a = compile_source(beam_source())
    b = compile_source(beam_source().replace('\n', '  // note\n').replace(' = ', '='))
    assert a.semantic_sha256 == b.semantic_sha256 and a.source_sha256 != b.source_sha256
