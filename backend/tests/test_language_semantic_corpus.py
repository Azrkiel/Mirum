"""Table-driven regression tests for the MEL v0.1 semantic diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering.language import MelError
from engineering.language.diagnostics import HINTS, MEL_S_IR_CODES
from engineering.language.semantic import compile_source

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'f05' / 'negative'
CORPUS = json.loads((FIXTURES / 'cases.json').read_text(encoding='ascii'))
CASES = CORPUS['cases']
BASE = FIXTURES / CORPUS['base']


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_semantic_diagnostic(case):
    source = BASE.read_text(encoding='utf-8')
    for old, new in case['edits']:
        assert old in source, f"{case['id']}: edit target {old!r} is missing"
        source = source.replace(old, new, 1)

    with pytest.raises(MelError) as excinfo:
        compile_source(source)

    d = excinfo.value.diagnostic
    code, ir_code = case['code'], case['ir_code']
    assert (d.code, d.ir_code, d.category, d.field, d.span.start.line) == (
        code, ir_code, case['category'], case['field'], case['line'],
    )
    assert MEL_S_IR_CODES[code] == ir_code
    assert d.hint == HINTS[code]


def test_case_ids_are_unique():
    assert len({case['id'] for case in CASES}) == len(CASES)


def test_corpus_covers_source_semantic_codes():
    # S007: a finite literal that overflows binary64 during unit normalization (1e308 m -> mm).
    expected = {f'MEL-S{n:03d}' for n in range(1, 16)}
    assert {case['code'] for case in CASES} == expected


def test_invalid_source_id():
    with pytest.raises(MelError) as excinfo:
        compile_source(BASE.read_bytes(), source_id='../x')

    d = excinfo.value.diagnostic
    assert (d.code, d.ir_code, d.category, d.field, d.span.start.line) == (
        'MEL-S016', None, 'semantic', 'source_id', 1,
    )
    assert MEL_S_IR_CODES['MEL-S016'] is None
    assert d.hint == HINTS['MEL-S016']


def test_unedited_base_compiles():
    result = compile_source(BASE.read_bytes())
    assert result.ir is not None
    assert result.diagnostics == ()
