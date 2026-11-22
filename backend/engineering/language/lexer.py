"""MEL v0.1 lexer (F04): the lexical rules frozen in LANGUAGE.md (MEL-L001..L023).

Deterministic, single pass, no evaluation of source text. The first error stops lexing.
Order of the whole-input checks: size (L022), byte-order mark (L002), UTF-8 (L001). After decoding,
NUL (L003) and bidirectional controls (L006) are found by a pre-scan; the reported error is the one
at the smallest position, and a NUL/bidi error wins a tie with the scanner's error at the same place.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass

from .diagnostics import MelError, Position, Span, make, snippet

MAX_SOURCE_BYTES = 64 * 1024
MAX_TOKENS = 10_000
MAX_IDENTIFIER = 64
MAX_NUMBER = 32
MAX_STRING = 4096

KEYWORDS = frozenset({'language', 'mirum', 'design'})
RESERVED = frozenset({'true', 'false', 'null'})
PUNCTUATION = frozenset('{}[]=;,')
# The frozen set (LANGUAGE.md section 7) plus an F04 tightening (section 10): U+061C (Arabic letter
# mark) and the line/paragraph separators U+2028/U+2029.
BIDI = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F,
                                  0x061C, 0x2028, 0x2029))
_ESCAPES = {'"': '"', '\\': '\\', '/': '/', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t'}
_HEX = frozenset('0123456789abcdefABCDEF')
_DIGITS = frozenset('0123456789')
_ID_START = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_')
_ID_CHAR = _ID_START | _DIGITS


@dataclass(frozen=True)
class Token:
    kind: str  # 'keyword' | 'ident' | 'number' | 'string' | 'punct' | 'eof'
    text: str  # identifier/keyword/punct text, number text exactly as written, decoded string value
    span: Span


class _Source:
    """Character index -> byte offset, line, column."""

    def __init__(self, text: str):
        self.text = text
        offsets = [0] * (len(text) + 1)
        line_starts = [0]
        total = 0
        for i, ch in enumerate(text):
            offsets[i] = total
            code = ord(ch)
            total += 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4
            if ch == '\n':
                line_starts.append(i + 1)
        offsets[len(text)] = total
        self.offsets = offsets
        self.line_starts = line_starts

    def position(self, index: int) -> Position:
        index = max(0, min(index, len(self.text)))
        line = bisect_right(self.line_starts, index)
        return Position(self.offsets[index], line, index - self.line_starts[line - 1] + 1)

    def span(self, start: int, end: int) -> Span:
        """Characters start..end inclusive (end clamped to start)."""
        return Span(self.position(start), self.position(max(start, end)))


def _byte_position(data: bytes, offset: int) -> Position:
    """Position of a byte offset inside data whose prefix data[:offset] is valid UTF-8."""
    prefix = data[:offset].decode('utf-8', errors='replace')
    line = prefix.count('\n') + 1
    column = len(prefix) - (prefix.rfind('\n') + 1) + 1
    return Position(offset, line, column)


def _byte_error(code: str, data: bytes, offset: int, message: str | None = None) -> MelError:
    pos = _byte_position(data, offset)
    return make(code, Span(pos, pos), message)


def decode(source: bytes | str) -> str:
    """Whole-input checks and strict UTF-8 decoding."""
    if isinstance(source, str):
        if len(source) > MAX_SOURCE_BYTES:
            # Every character is at least one byte; avoid encoding an oversized string.
            pos = Position(0, 1, 1)
            raise make('MEL-L022', Span(pos, pos))
        try:
            data = source.encode('utf-8')
        except UnicodeEncodeError as exc:  # lone surrogate in a Python str
            data = source.encode('utf-8', 'surrogatepass')
            prefix = source[:exc.start].encode('utf-8', 'surrogatepass')
            raise _byte_error('MEL-L001', data, len(prefix)) from None
    elif isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
    else:
        raise TypeError('MEL source must be bytes or str')
    if len(data) > MAX_SOURCE_BYTES:
        pos = Position(0, 1, 1)
        raise make('MEL-L022', Span(pos, pos))
    if data.startswith(b'\xef\xbb\xbf'):
        pos = Position(0, 1, 1)
        raise make('MEL-L002', Span(pos, pos))
    try:
        return data.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise _byte_error('MEL-L001', data, exc.start) from None


def _first_global(text: str) -> tuple[int, str] | None:
    for i, ch in enumerate(text):
        if ch == '\x00':
            return i, 'MEL-L003'
        if ch in BIDI:
            return i, 'MEL-L006'
    return None


def tokenize(source: bytes | str) -> list[Token]:
    text = decode(source)
    src = _Source(text)
    found = _first_global(text)
    try:
        tokens = _Scanner(src).run()
    except _ScanError as exc:
        if found is not None and found[0] <= exc.index:
            raise make(found[1], src.span(found[0], found[0])) from None
        raise make(exc.code, src.span(exc.index, exc.end), exc.message) from None
    if found is not None:
        raise make(found[1], src.span(found[0], found[0]))
    return tokens


class _ScanError(Exception):
    def __init__(self, code: str, index: int, end: int | None = None, message: str | None = None):
        super().__init__(code)
        self.code, self.index, self.message = code, index, message
        self.end = index if end is None else end


class _Scanner:
    def __init__(self, src: _Source):
        self.src = src
        self.text = src.text
        self.n = len(self.text)
        self.tokens: list[Token] = []

    def _add(self, kind: str, value: str, start: int, end: int) -> None:
        if kind != 'eof' and len(self.tokens) >= MAX_TOKENS:
            raise _ScanError('MEL-L023', start, end)
        self.tokens.append(Token(kind, value, self.src.span(start, end)))

    def run(self) -> list[Token]:
        text, n, i = self.text, self.n, 0
        while i < n:
            ch = text[i]
            if ch in ' \t\n':
                i += 1
            elif ch == '\r':
                if i + 1 < n and text[i + 1] == '\n':
                    i += 2
                else:
                    raise _ScanError('MEL-L004', i)
            elif ch == '/':
                i = self._comment(i)
            elif ch in PUNCTUATION:
                self._add('punct', ch, i, i)
                i += 1
            elif ch == '"':
                i = self._string(i)
            elif ch in _DIGITS or ch == '-':
                i = self._number(i)
            elif ch in _ID_START:
                i = self._identifier(i)
            elif ch in '+.' and i + 1 < n and text[i + 1] in _DIGITS:
                raise _ScanError('MEL-L009', i, i + 1, 'Malformed number: no leading + or bare decimal point')
            else:
                raise _ScanError('MEL-L005', i, i, f'Disallowed character {snippet(ch)!r}')
        self._add('eof', '', n, n)
        return self.tokens

    def _comment(self, i: int) -> int:
        text, n = self.text, self.n
        if i + 1 < n and text[i + 1] == '*':
            raise _ScanError('MEL-L020', i, i + 1)
        if not (i + 1 < n and text[i + 1] == '/'):
            raise _ScanError('MEL-L005', i, i, "Disallowed character '/'")
        j = i + 2
        while j < n:
            ch = text[j]
            if ch == '\n' or (ch == '\r' and j + 1 < n and text[j + 1] == '\n'):
                return j
            code = ord(ch)
            if (code < 0x20 and ch != '\t') or code == 0x7f:
                raise _ScanError('MEL-L021', j)
            j += 1
        return j

    def _identifier(self, i: int) -> int:
        text, n = self.text, self.n
        j = i + 1
        while j < n and text[j] in _ID_CHAR:
            j += 1
        word = text[i:j]
        if len(word) > MAX_IDENTIFIER:
            raise _ScanError('MEL-L007', i, j - 1)
        if word in RESERVED:
            raise _ScanError('MEL-L008', i, j - 1, f'{word!r} is reserved')
        self._add('keyword' if word in KEYWORDS else 'ident', word, i, j - 1)
        return j

    def _number(self, i: int) -> int:
        text, n = self.text, self.n
        j = i
        if text[j] == '-':
            j += 1
            if j >= n or text[j] not in _DIGITS:
                raise _ScanError('MEL-L009', i, i, "Malformed number: '-' must be followed by a digit")
        int_start = j
        while j < n and text[j] in _DIGITS:
            j += 1
        int_part = text[int_start:j]
        if j < n and text[j] == '.':
            if j + 1 >= n or text[j + 1] not in _DIGITS:
                raise _ScanError('MEL-L009', i, j, 'Malformed number: digits required after the decimal point')
            j += 1
            while j < n and text[j] in _DIGITS:
                j += 1
        if j < n and text[j] in 'eE':
            k = j + 1
            if k < n and text[k] in '+-':
                k += 1
                if k >= n or text[k] not in _DIGITS:
                    raise _ScanError('MEL-L009', i, k - 1, 'Malformed number: exponent needs digits')
            if k < n and text[k] in _DIGITS:
                while k < n and text[k] in _DIGITS:
                    k += 1
                j = k
            # Otherwise the letter starts a unit identifier (e.g. "5 em" or "5em").
        if j < n and (text[j] == '.' or text[j] in _DIGITS):
            raise _ScanError('MEL-L009', i, j)
        literal = text[i:j]
        if len(literal) > MAX_NUMBER:
            raise _ScanError('MEL-L011', i, j - 1)
        if len(int_part) > 1 and int_part[0] == '0':
            raise _ScanError('MEL-L010', i, j - 1)
        if not math.isfinite(float(literal)):
            raise _ScanError('MEL-L012', i, j - 1)
        self._add('number', literal, i, j - 1)
        return j

    def _string(self, i: int) -> int:
        text, n = self.text, self.n
        out: list[str] = []
        count = 0
        j = i + 1
        while True:
            if j >= n:
                raise _ScanError('MEL-L013', i, max(i, n - 1))
            ch = text[j]
            if ch == '"':
                self._add('string', ''.join(out), i, j)
                return j + 1
            if ch in '\n\r':
                raise _ScanError('MEL-L014', j)
            code = ord(ch)
            if code < 0x20 or code == 0x7f:
                raise _ScanError('MEL-L015', j)
            if ch == '\\':
                if j + 1 >= n:
                    raise _ScanError('MEL-L013', i, j)
                esc = text[j + 1]
                if esc in _ESCAPES:
                    out.append(_ESCAPES[esc])
                    j += 2
                elif esc == 'u':
                    value, j = self._unicode_escape(j)
                    out.append(value)
                else:
                    raise _ScanError('MEL-L016', j, j + 1)
            else:
                out.append(ch)
                j += 1
            count += 1
            if count > MAX_STRING:
                raise _ScanError('MEL-L019', i, j - 1)

    def _hex4(self, j: int) -> int:
        """Value of the \\uXXXX escape starting at j (the backslash)."""
        digits = self.text[j + 2:j + 6]
        if len(digits) != 4 or any(c not in _HEX for c in digits):
            raise _ScanError('MEL-L017', j, min(j + 5, self.n - 1))
        return int(digits, 16)

    def _unicode_escape(self, j: int) -> tuple[str, int]:
        code = self._hex4(j)
        if 0xDC00 <= code <= 0xDFFF:
            raise _ScanError('MEL-L018', j, j + 5, 'Unpaired low surrogate escape')
        if 0xD800 <= code <= 0xDBFF:
            k = j + 6
            if not (self.text.startswith('\\u', k)):
                raise _ScanError('MEL-L018', j, j + 5, 'High surrogate escape without a low surrogate')
            low = self._hex4(k)
            if not 0xDC00 <= low <= 0xDFFF:
                raise _ScanError('MEL-L018', j, k + 5, 'High surrogate escape without a low surrogate')
            return chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)), k + 6
        return chr(code), j + 6
