"""F04: MEL v0.1 parser: grammar, MEL-P codes, budgets (FRAMEWORK.md v0.1) and the 2 s compile bound."""
from __future__ import annotations

import time

import pytest

from engineering.language import ListValue, MelError, Quantity, String, parse
from engineering.language.parser import MAX_DEPTH, MAX_LIST, MAX_NODES

HEADER = 'language mirum "0.1";\n'


def program(body: str) -> str:
    return HEADER + 'design d {\n' + body + '\n}\n'


NEGATIVE = [
    ('P001-empty', '', 'MEL-P001', 1, 1),
    ('P001-only-comment', '  // nothing\n', 'MEL-P001', 2, 1),
    ('P002-no-header', 'design d {}', 'MEL-P002', 1, 1),
    ('P002-wrong-language', 'language other "0.1";', 'MEL-P002', 1, 10),
    ('P003-version', 'language mirum "0.2";\ndesign d {}', 'MEL-P003', 1, 16),
    ('P004-double-equals', program('a = = 1;'), 'MEL-P004', 3, 5),
    ('P004-identifier-value', program('a = mm;'), 'MEL-P004', 3, 5),
    ('P004-missing-name', HEADER + 'design {}', 'MEL-P004', 2, 8),
    ('P004-list-separator', program('a = [1 2];'), 'MEL-P004', 3, 8),
    ('P004-trailing-comma', program('a = [1, ];'), 'MEL-P004', 3, 9),
    ('P005-missing-semicolon', program('a = 1\nb = 2;'), 'MEL-P005', 3, 5),
    ('P005-header-semicolon', 'language mirum "0.1" design d {}', 'MEL-P005', 1, 16),
    ('P006-duplicate', program('a = 1;\na = 2;'), 'MEL-P006', 4, 1),
    ('P007-second-design', program('') + 'design e {}', 'MEL-P007', 5, 1),
    ('P007-trailing-semicolon', program('') + ';', 'MEL-P007', 5, 1),
    ('P011-unclosed-design', HEADER + 'design d {\na = 1;', 'MEL-P011', 3, 7),
    ('P011-unclosed-list', HEADER + 'design d {' + chr(10) + 'a = [1,', 'MEL-P011', 3, 8),
    ('P004-unclosed-list-then-brace', program('a = [1,'), 'MEL-P004', 4, 1),
    ('L008-keyword-as-design-name', HEADER + 'design design {}', 'MEL-L008', 2, 8),
    ('L008-keyword-as-field', program('mirum = 1;'), 'MEL-L008', 3, 1),
]


@pytest.mark.parametrize('case,source,code,line,column', NEGATIVE, ids=[c[0] for c in NEGATIVE])
def test_syntax_error(case, source, code, line, column):
    with pytest.raises(MelError) as excinfo:
        parse(source)
    d = excinfo.value.diagnostic
    assert (d.code, d.span.start.line, d.span.start.column) == (code, line, column)
    assert d.hint


def test_every_syntax_code_has_a_negative_case():
    from engineering.language.diagnostics import SYNTAX
    budget_codes = {'MEL-P008', 'MEL-P009', 'MEL-P010'}  # covered by the budget tests below
    assert {c[2] for c in NEGATIVE if c[2].startswith('MEL-P')} | budget_codes == set(SYNTAX)


def test_parse_values_and_spans():
    p = parse(program('a = 12 mm;\nb = "x";\nc = [1, [2 N], "s"];\nd = 3;\ne = [];'))
    assert p.version == '0.1' and p.design == 'd'
    a, b, c, d, e = p.assignments
    assert a.value == Quantity('12', 'mm', None)
    assert b.value == String('x', None)
    assert c.value == ListValue((Quantity('1', None, None), ListValue((Quantity('2', 'N', None),), None),
                                 String('s', None)), None)
    assert d.value == Quantity('3', None, None)
    assert e.value == ListValue((), None)
    assert (a.span.start.line, a.span.start.column, a.span.end.column) == (3, 1, 10)
    assert (p.design_span.start.line, p.design_span.start.column) == (2, 8)


def test_unit_must_be_on_the_same_line():
    with pytest.raises(MelError) as excinfo:
        parse(program('a = 1\nmm;'))
    assert excinfo.value.diagnostic.code == 'MEL-P005'


def test_empty_design_is_syntactically_valid():
    assert parse(HEADER + 'design d {}').assignments == ()


def test_crlf_and_comments_are_layout_only():
    plain = parse(program('a = 1 mm;\nb = ["x"];'))
    noisy = parse(('// lead\r\n' + HEADER + 'design d { // open\r\n  a=1mm; // one\r\n'
                   '\tb = [ "x" ] ;\r\n}\r\n// end').replace('\n', '\n'))
    assert plain == noisy


# ---------------------------------------------------------------- budgets

def nested(levels: int) -> str:
    return '[' * levels + '1' + ']' * levels


def test_depth_budget():
    # program 1, assignment 2, value 3, each list level +1: 13 lists put the innermost number at 16.
    assert parse(program('a = ' + nested(MAX_DEPTH - 3) + ';'))
    with pytest.raises(MelError) as excinfo:
        parse(program('a = ' + nested(MAX_DEPTH - 2) + ';'))
    assert excinfo.value.diagnostic.code == 'MEL-P008'


def test_depth_budget_holds_for_hostile_nesting_without_recursion_error():
    with pytest.raises(MelError) as excinfo:
        parse(program('a = ' + '[' * 5000 + ';'))
    assert excinfo.value.diagnostic.code == 'MEL-P008'


def test_list_budget():
    assert len(parse(program('a = [' + ', '.join(['1'] * MAX_LIST) + '];')).assignments[0].value.items) == MAX_LIST
    with pytest.raises(MelError) as excinfo:
        parse(program('a = [' + ', '.join(['1'] * (MAX_LIST + 1)) + '];'))
    assert excinfo.value.diagnostic.code == 'MEL-P010'


def nodes_program(total: int) -> str:
    """A program with exactly `total` syntax nodes: program 1, each assignment 1 + list 1 + items."""
    remaining = total - 1
    lines, k = [], 0
    while remaining > 0:
        items = min(MAX_LIST, remaining - 2)
        assert items >= 0
        lines.append(f'a{k} = [' + ', '.join(['1'] * items) + '];')
        remaining -= 2 + items
        k += 1
    assert remaining == 0
    return program('\n'.join(lines))


def test_node_budget():
    assert parse(nodes_program(MAX_NODES))
    with pytest.raises(MelError) as excinfo:
        parse(nodes_program(MAX_NODES + 1))
    assert excinfo.value.diagnostic.code == 'MEL-P009'


@pytest.mark.parametrize('name,source', [
    ('max-nodes', lambda: nodes_program(MAX_NODES)),
    ('max-depth', lambda: program('a = ' + nested(MAX_DEPTH - 3) + ';')),
    ('max-tokens-lexed', lambda: ';' * 10_000),
    ('max-strings', lambda: program('\n'.join(f's{i} = "' + 'a' * 4096 + '";' for i in range(15)))),
    ('max-escapes', lambda: program('s = "' + chr(92) + 'u0041' * 4096 + '";')),
    ('max-comment', lambda: HEADER + 'design d {}\n//' + 'x' * 65000),
])
def test_worst_case_inputs_compile_within_two_seconds(name, source):
    text = source()
    assert len(text.encode()) <= 64 * 1024
    start = time.perf_counter()
    try:
        parse(text)
    except MelError:
        pass
    assert time.perf_counter() - start < 2.0
