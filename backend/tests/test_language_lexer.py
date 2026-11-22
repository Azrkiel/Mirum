"""F04: MEL v0.1 lexical rules (LANGUAGE.md, frozen in F01): every MEL-L code, positions, budgets."""
from __future__ import annotations

import pytest

from engineering.language import MelError, tokenize
from engineering.language.lexer import MAX_SOURCE_BYTES, MAX_TOKENS

HEADER = 'language mirum "0.1";\n'
# Non-ASCII test characters are built with chr(): this file stays ASCII (no raw bidi controls).
BS = chr(92)
E_ACUTE, GRIN = chr(0xE9), chr(0x1F600)
RLO, LRI, LRM = chr(0x202E), chr(0x2066), chr(0x200E)

# (case id, source, code, line, column)
NEGATIVE = [
    ('L001-invalid-byte', b'language \xff', 'MEL-L001', 1, 10),
    ('L001-overlong', b'a \xc0\xaf', 'MEL-L001', 1, 3),
    ('L001-encoded-surrogate', b'"\xed\xa0\x80"', 'MEL-L001', 1, 2),
    ('L001-truncated', b'x\n"\xe2\x82', 'MEL-L001', 2, 2),
    ('L001-lone-surrogate-str', 'a "\ud800"', 'MEL-L001', 1, 4),
    ('L002-bom', b'\xef\xbb\xbflanguage', 'MEL-L002', 1, 1),
    ('L003-nul', b'language\x00', 'MEL-L003', 1, 9),
    ('L003-nul-in-string', '"a\x00"', 'MEL-L003', 1, 3),
    ('L003-nul-in-comment', '// a\x00', 'MEL-L003', 1, 5),
    ('L004-bare-cr', 'a\rb', 'MEL-L004', 1, 2),
    ('L005-at', 'a @', 'MEL-L005', 1, 3),
    ('L005-non-ascii-outside', 'caf' + E_ACUTE, 'MEL-L005', 1, 4),
    ('L005-single-slash', 'a / b', 'MEL-L005', 1, 3),
    ('L005-python-call', '__import__("os")', 'MEL-L005', 1, 11),
    ('L005-python-attr', 'os.system', 'MEL-L005', 1, 3),
    ('L005-colon', 'a: 1', 'MEL-L005', 1, 2),
    ('L005-paren', 'print(1)', 'MEL-L005', 1, 6),
    ('L006-bidi-in-string', '"' + RLO + '"', 'MEL-L006', 1, 2),
    ('L006-bidi-in-comment', 'a // x' + LRI, 'MEL-L006', 1, 7),
    ('L006-lrm-outside', 'a ' + LRM, 'MEL-L006', 1, 3),
    ('L007-identifier', 'a' * 65, 'MEL-L007', 1, 1),
    ('L008-true', 'x = true', 'MEL-L008', 1, 5),
    ('L008-null', 'null', 'MEL-L008', 1, 1),
    ('L008-false', 'a false', 'MEL-L008', 1, 3),
    ('L009-minus-alone', 'a = -x', 'MEL-L009', 1, 5),
    ('L009-trailing-dot', '5.', 'MEL-L009', 1, 1),
    ('L009-leading-dot', '.5', 'MEL-L009', 1, 1),
    ('L009-leading-plus', '+5', 'MEL-L009', 1, 1),
    ('L009-exponent-sign-only', '1e+', 'MEL-L009', 1, 1),
    ('L009-two-dots', '1.2.3', 'MEL-L009', 1, 1),
    ('L010-leading-zero', '007', 'MEL-L010', 1, 1),
    ('L010-negative-leading-zero', '-01', 'MEL-L010', 1, 1),
    ('L011-long-number', '1' * 33, 'MEL-L011', 1, 1),
    ('L012-overflow', 'x = 1e400', 'MEL-L012', 1, 5),
    ('L012-negative-overflow', '-1e999', 'MEL-L012', 1, 1),
    ('L013-unterminated', '"abc', 'MEL-L013', 1, 1),
    ('L013-unterminated-escape', '"abc\\', 'MEL-L013', 1, 1),
    ('L014-newline-in-string', '"ab\ncd"', 'MEL-L014', 1, 4),
    ('L014-crlf-in-string', '"ab\r\ncd"', 'MEL-L014', 1, 4),
    ('L015-tab-in-string', '"a\tb"', 'MEL-L015', 1, 3),
    ('L015-del-in-string', '"a\x7f"', 'MEL-L015', 1, 3),
    ('L016-bad-escape', '"\\x41"', 'MEL-L016', 1, 2),
    ('L016-single-quote-escape', '"\\\'"', 'MEL-L016', 1, 2),
    ('L017-short-u', '"\\u12"', 'MEL-L017', 1, 2),
    ('L017-non-hex-u', '"\\u12g4"', 'MEL-L017', 1, 2),
    ('L018-lone-high', '"\\ud800"', 'MEL-L018', 1, 2),
    ('L018-lone-low', '"\\udc00"', 'MEL-L018', 1, 2),
    ('L018-high-then-bmp', '"\\ud800\\u0041"', 'MEL-L018', 1, 2),
    ('L019-long-string', '"' + 'a' * 4097 + '"', 'MEL-L019', 1, 1),
    ('L020-block-comment', '/* x */', 'MEL-L020', 1, 1),
    ('L021-control-in-comment', '// a\x01b', 'MEL-L021', 1, 5),
    ('L021-del-in-comment', 'x // \x7f', 'MEL-L021', 1, 6),
    ('L021-bare-cr-in-comment', '// a\rb', 'MEL-L021', 1, 5),
    ('L022-oversize', b'a' * (MAX_SOURCE_BYTES + 1), 'MEL-L022', 1, 1),
    ('L022-oversize-str', 'a' * (MAX_SOURCE_BYTES + 1), 'MEL-L022', 1, 1),
    ('L022-oversize-multibyte-str', '"' + E_ACUTE * 40000 + '"', 'MEL-L022', 1, 1),
    ('L023-tokens', ';' * (MAX_TOKENS + 1), 'MEL-L023', 1, MAX_TOKENS + 1),
]


@pytest.mark.parametrize('case,source,code,line,column', NEGATIVE, ids=[c[0] for c in NEGATIVE])
def test_lexical_error(case, source, code, line, column):
    with pytest.raises(MelError) as excinfo:
        tokenize(source)
    d = excinfo.value.diagnostic
    assert (d.code, d.span.start.line, d.span.start.column) == (code, line, column)
    assert d.category == 'lexical'
    assert d.hint


def test_every_lexical_code_has_a_negative_case():
    from engineering.language.diagnostics import LEXICAL
    assert {c[2] for c in NEGATIVE} == set(LEXICAL)


def test_diagnostic_never_echoes_raw_control_or_long_source():
    with pytest.raises(MelError) as excinfo:
        tokenize('a \u0080')
    assert '\u0080' not in excinfo.value.diagnostic.message
    assert '\\u0080' in excinfo.value.diagnostic.message


def _kinds(source):
    return [(t.kind, t.text) for t in tokenize(source)]


def test_tokens_of_a_header_and_assignment():
    assert _kinds(HEADER + 'design d { a = -1.5e3 mm; }') == [
        ('keyword', 'language'), ('keyword', 'mirum'), ('string', '0.1'), ('punct', ';'),
        ('keyword', 'design'), ('ident', 'd'), ('punct', '{'), ('ident', 'a'), ('punct', '='),
        ('number', '-1.5e3'), ('ident', 'mm'), ('punct', ';'), ('punct', '}'), ('eof', '')]


@pytest.mark.parametrize('text', ['0', '0.5', '-0', '12', '1e3', '1E-3', '2.5e+10', '1' * 32])
def test_valid_numbers(text):
    assert _kinds(text)[0] == ('number', text)


def test_number_followed_by_unit_without_space():
    assert _kinds('5mm 5em') == [('number', '5'), ('ident', 'mm'), ('number', '5'), ('ident', 'em'), ('eof', '')]


def test_string_escapes_decode():
    value = tokenize('"a' + BS + '"' + BS + BS + BS + '/' + BS + 'b' + BS + 'f' + BS + 'n' + BS + 'r' + BS + 't' + BS + 'u00e9' + BS + 'ud83d' + BS + 'ude00' + BS + 'u202e"')[0].text
    assert value == 'a"' + BS + '/' + chr(8) + chr(12) + chr(10) + chr(13) + chr(9) + E_ACUTE + GRIN + RLO


def test_non_ascii_inside_strings_and_comments():
    tokens = tokenize('// caf' + E_ACUTE + ' ' + GRIN + chr(10) + '"' + E_ACUTE + GRIN + '"')
    assert tokens[0].text == E_ACUTE + GRIN


def test_keywords_are_case_sensitive():
    assert _kinds('Design True')[:2] == [('ident', 'Design'), ('ident', 'True')]


def test_positions_count_scalar_values_and_bytes():
    tokens = tokenize('"' + E_ACUTE + '" x')
    x = tokens[1]
    assert (x.span.start.line, x.span.start.column, x.span.start.offset) == (1, 5, 5)
    with pytest.raises(MelError) as excinfo:
        tokenize('"\U0001f600" @')
    pos = excinfo.value.diagnostic.span.start
    assert (pos.line, pos.column, pos.offset) == (1, 5, 7)


def test_crlf_line_counting():
    tokens = tokenize('a\r\nb\r\n  c')
    assert [(t.span.start.line, t.span.start.column) for t in tokens[:3]] == [(1, 1), (2, 1), (3, 3)]


def test_limits_at_the_boundary_are_accepted():
    assert tokenize('a' * 64)[0].text == 'a' * 64
    assert len(tokenize('"' + 'a' * 4096 + '"')[0].text) == 4096
    assert len(tokenize('"' + '\\ud83d\\ude00' * 4096 + '"')[0].text) == 4096
    assert len(tokenize(';' * MAX_TOKENS)) == MAX_TOKENS + 1  # plus eof
    exact = b'//' + b'a' * (MAX_SOURCE_BYTES - 2)
    assert len(exact) == MAX_SOURCE_BYTES
    assert tokenize(exact)[-1].kind == 'eof'


def test_first_error_wins_by_position():
    # The bare CR comes before the NUL: L004.
    with pytest.raises(MelError) as excinfo:
        tokenize('a\rb\x00')
    assert excinfo.value.diagnostic.code == 'MEL-L004'
    # The NUL comes first: L003 even though the scanner would fail later.
    with pytest.raises(MelError) as excinfo:
        tokenize('\x00 @')
    assert excinfo.value.diagnostic.code == 'MEL-L003'


def test_rejects_non_text_input():
    with pytest.raises(TypeError):
        tokenize(123)  # type: ignore[arg-type]
