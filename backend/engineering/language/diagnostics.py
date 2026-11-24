"""MEL diagnostics and source positions (F04).

Positions: ``offset`` is a 0-based byte offset into the UTF-8 source; ``line`` and ``column`` are
1-based, the column counted in Unicode scalar values. Spans are end inclusive, like
``engineering.program.errors.SourceSpan`` (``to_source_span`` converts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

# Lexical codes are frozen in LANGUAGE.md (F01); syntax/structure codes are frozen in F04.
LEXICAL = {
    'MEL-L001': 'Invalid UTF-8',
    'MEL-L002': 'Byte-order mark at start of input',
    'MEL-L003': 'NUL byte',
    'MEL-L004': 'Bare CR (not followed by LF)',
    'MEL-L005': 'Disallowed character outside string/comment',
    'MEL-L006': 'Bidirectional control character',
    'MEL-L007': 'Identifier longer than 64 characters',
    'MEL-L008': 'Reserved word used as identifier',
    'MEL-L009': 'Malformed number',
    'MEL-L010': 'Leading zero in number',
    'MEL-L011': 'Number longer than 32 characters',
    'MEL-L012': 'Non-finite number after binary64 conversion',
    'MEL-L013': 'Unterminated string',
    'MEL-L014': 'Newline in string',
    'MEL-L015': 'Raw control character in string',
    'MEL-L016': 'Invalid escape sequence',
    'MEL-L017': 'Malformed \\u escape',
    'MEL-L018': 'Unpaired surrogate escape',
    'MEL-L019': 'String longer than 4096 code points',
    'MEL-L020': 'Block comment not supported',
    'MEL-L021': 'Control character in comment',
    'MEL-L022': 'Source larger than 64 KiB',
    'MEL-L023': 'More than 10,000 tokens',
}
SYNTAX = {
    'MEL-P001': 'Empty source',
    'MEL-P002': 'Missing language header',
    'MEL-P003': 'Unsupported language version',
    'MEL-P004': 'Unexpected token',
    'MEL-P005': 'Missing semicolon',
    'MEL-P006': 'Duplicate assignment',
    'MEL-P007': 'Content after the design block',
    'MEL-P008': 'Nesting deeper than 16',
    'MEL-P009': 'More than 2,000 syntax nodes',
    'MEL-P010': 'List longer than 256 elements',
    'MEL-P011': 'Unexpected end of input',
}

SEMANTIC = {
    'MEL-S001': 'material_source_kind is missing or not "illustrative", "user_supplied" or "documented"',
    'MEL-S002': 'Unknown field for this capability and version',
    'MEL-S003': 'Missing required field (no defaults)',
    'MEL-S004': 'Unknown unit',
    'MEL-S005': 'Unit of the wrong dimension',
    'MEL-S006': 'Physical quantity without a unit',
    'MEL-S007': 'Non-finite value',
    'MEL-S008': 'Wrong kind of value for this field',
    'MEL-S009': 'Text contains a control, format or separator character',
    'MEL-S010': 'Invalid design name',
    'MEL-S011': 'Unsupported capability or capability version',
    'MEL-S012': 'Backend not supported for this capability',
    'MEL-S013': 'Export not supported by this backend and capability',
    'MEL-S014': 'Candidate policy violation',
    'MEL-S015': 'Value rejected by the reviewed brief validation',
    'MEL-S016': 'Invalid source_id label',
    'MEL-S017': 'Reviewed round trip mismatch',
    'MEL-S018': 'Compile deadline exceeded',
    'MEL-S019': 'Invalid program record',
    'MEL-S020': 'Dangling authored reference',
    'MEL-S021': 'Invalid plate dimension',
}
# MEL-S code -> F02 IR code (None: no IR equivalent).
MEL_S_IR_CODES = MappingProxyType({
    'MEL-S001': 'IR010', 'MEL-S002': 'IR002', 'MEL-S003': 'IR010', 'MEL-S004': 'IR005',
    'MEL-S005': 'IR006', 'MEL-S006': 'IR006', 'MEL-S007': 'IR007', 'MEL-S008': 'IR010',
    'MEL-S009': 'IR010', 'MEL-S010': 'IR010', 'MEL-S011': 'IR010', 'MEL-S012': 'IR010',
    'MEL-S013': 'IR010', 'MEL-S014': 'IR010', 'MEL-S015': 'IR010', 'MEL-S016': None,
    'MEL-S017': 'IR010', 'MEL-S018': None, 'MEL-S019': 'IR010', 'MEL-S020': 'IR004', 'MEL-S021': 'IR010',
})

HINTS = {
    'MEL-L001': 'Save the file as UTF-8.',
    'MEL-L002': 'Remove the byte-order mark at the start of the file.',
    'MEL-L003': 'Remove the NUL byte.',
    'MEL-L004': 'Use LF or CRLF line endings.',
    'MEL-L005': 'Outside strings and comments only ASCII letters, digits, _ { } [ ] = ; , and a number sign are allowed.',
    'MEL-L006': 'Remove the bidirectional control character; write it as a \\u escape inside a string if needed.',
    'MEL-L007': 'Use an identifier of at most 64 characters.',
    'MEL-L008': 'language, mirum, design, true, false and null are reserved; choose another name.',
    'MEL-L009': 'Write numbers like 12, -0.5 or 1.5e3.',
    'MEL-L010': 'Remove the leading zero.',
    'MEL-L011': 'Use a number of at most 32 characters.',
    'MEL-L012': 'Use a finite number.',
    'MEL-L013': 'Close the string with a double quote.',
    'MEL-L014': 'Strings are single line; write \\n for a newline.',
    'MEL-L015': 'Escape control characters (for example \\t or \\u0001).',
    'MEL-L016': 'Allowed escapes: \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX.',
    'MEL-L017': 'Write \\u followed by exactly four hex digits.',
    'MEL-L018': 'Write a surrogate pair as a high \\u escape immediately followed by a low \\u escape.',
    'MEL-L019': 'Use a string of at most 4096 characters.',
    'MEL-L020': 'Use // line comments.',
    'MEL-L021': 'Remove the control character from the comment.',
    'MEL-L022': 'Keep the source at or below 64 KiB.',
    'MEL-L023': 'Keep the source at or below 10,000 tokens.',
    'MEL-P001': 'Start with: language mirum "0.1";',
    'MEL-P002': 'Start with: language mirum "0.1";',
    'MEL-P003': 'Use language mirum "0.1";',
    'MEL-P004': 'Check the syntax: design name { field = value; }',
    'MEL-P005': 'End the statement with ;',
    'MEL-P006': 'Assign each field once.',
    'MEL-P007': 'A file holds exactly one design; remove what follows its closing brace.',
    'MEL-P008': 'Reduce list nesting.',
    'MEL-P009': 'Reduce the number of values.',
    'MEL-P010': 'Use at most 256 elements per list.',
    'MEL-P011': 'Complete the design block and close it with }.',
    'MEL-S001': 'Set material_source_kind to exactly "illustrative", "user_supplied" or "documented".',
    'MEL-S002': 'Remove the field or use one of the fields this capability defines.',
    'MEL-S003': 'Add the missing field; no field has a default.',
    'MEL-S004': 'Use a supported unit such as mm, m, N, kN or MPa.',
    'MEL-S005': 'Use a unit of the dimension this field needs (length, force or pressure).',
    'MEL-S006': 'Write the number with a unit, for example 10 mm.',
    'MEL-S007': 'Use a finite number.',
    'MEL-S008': 'Use the kind of value this field expects: a quantity, a string or a list.',
    'MEL-S009': 'Remove control, invisible format and line separator characters from the text.',
    'MEL-S010': 'Use a design name of lower case letters, digits and _, starting with a letter (at most 64).',
    'MEL-S011': 'Use capability "rectangular_cantilever" with capability_version "1.0.0".',
    'MEL-S012': 'Use backend "occt".',
    'MEL-S013': 'Set exports to exactly ["step", "glb"].',
    'MEL-S014': 'List one to four unique candidate heights inside the capability envelope.',
    'MEL-S015': 'Change the value to one the reviewed brief validation accepts.',
    'MEL-S016': 'Use a relative label of [A-Za-z0-9._-] segments, at most 128 characters, without . or .. segments.',
    'MEL-S017': 'Report this compiler defect; the reviewed round trip did not reproduce the validated brief.',
    'MEL-S018': 'Shorten or simplify the source so it compiles within 2 seconds.',
    'MEL-S019': 'Check the field values; the program record built from them was invalid.',
}

_SNIPPET = 64


def snippet(text: str) -> str:
    """At most 64 characters of user source, non-printables escaped (never echo raw control text)."""
    short = text[:_SNIPPET]
    out = []
    for ch in short:
        code = ord(ch)
        if 0x20 <= code < 0x7f:
            out.append(ch)
        else:
            out.append(f'\\u{code:04x}' if code <= 0xffff else f'\\U{code:08x}')
    return ''.join(out) + ('...' if len(text) > _SNIPPET else '')


@dataclass(frozen=True)
class Position:
    offset: int
    line: int
    column: int


@dataclass(frozen=True)
class Span:
    start: Position
    end: Position  # inclusive

    def to_source_span(self, source_id: str | None = None):
        from engineering.program.errors import SourceSpan
        return SourceSpan(line=self.start.line, column=self.start.column, end_line=self.end.line,
                          end_column=self.end.column, source_id=source_id)


@dataclass(frozen=True)
class Diagnostic:
    code: str
    category: str  # 'lexical' | 'syntax' | 'semantic' | 'resource'
    message: str
    hint: str
    span: Span
    field: str | None = field(default=None)
    ir_code: str | None = None


class MelError(ValueError):
    """Raised with exactly one diagnostic (the first error stops compilation, v0.1)."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(f'{diagnostic.code} at {diagnostic.span.start.line}:{diagnostic.span.start.column}: '
                         f'{diagnostic.message}')
        self.diagnostic = diagnostic


def make(code: str, span: Span, message: str | None = None, *, field_name: str | None = None) -> MelError:
    if code in LEXICAL:
        category, base = 'lexical', LEXICAL[code]
    elif code in SYNTAX:
        category, base = 'syntax', SYNTAX[code]
    elif code in SEMANTIC:
        category, base = ('resource' if code == 'MEL-S018' else 'semantic'), SEMANTIC[code]
    else:
        category, base = 'semantic', 'Invalid program'
    hint = HINTS.get(code, 'Fix the value of this field.')
    return MelError(Diagnostic(code, category, message or base, hint, span, field_name,
                               MEL_S_IR_CODES.get(code)))
