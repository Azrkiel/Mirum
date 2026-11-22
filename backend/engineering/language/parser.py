"""MEL v0.1 recursive-descent parser (F04).

    program    = "language" "mirum" string ";" "design" identifier "{" { assignment } "}" EOF
    assignment = identifier "=" value ";"
    value      = quantity | string | list
    quantity   = number [ identifier on the same line ]
    list       = "[" [ value { "," value } ] "]"

Budgets (FRAMEWORK.md v0.1): syntax depth 16 (program 1, assignment 2, value 3, each list level +1),
2,000 syntax nodes (program, assignments and every value), 256 elements per list. Depth is checked
before descending, so recursion is bounded at 16 levels whatever the input. The first error stops.
"""
from __future__ import annotations

from .diagnostics import Span, make, snippet
from .lexer import Token, tokenize
from .syntax import Assignment, ListValue, Program, Quantity, String, Value

SUPPORTED_VERSIONS = frozenset({'0.1'})
MAX_DEPTH = 16
MAX_NODES = 2000
MAX_LIST = 256


def parse(source: bytes | str) -> Program:
    return _Parser(tokenize(source)).program()


def _describe(token: Token) -> str:
    if token.kind == 'eof':
        return 'end of input'
    if token.kind == 'string':
        return 'a string'
    if token.kind == 'number':
        return f'number {snippet(token.text)}'
    return repr(snippet(token.text))


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.i = 0
        self.nodes = 0

    # ------------------------------------------------------------ helpers
    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def _advance(self) -> Token:
        token = self.tokens[self.i]
        if token.kind != 'eof':
            self.i += 1
        return token

    def _node(self, token: Token) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise make('MEL-P009', token.span)

    def _unexpected(self, expected: str):
        token = self.tok
        if token.kind == 'eof':
            return make('MEL-P011', token.span, f'Unexpected end of input; expected {expected}')
        if token.kind == 'keyword' and expected.startswith('a name'):
            return make('MEL-L008', token.span, f'{token.text!r} is reserved')
        return make('MEL-P004', token.span, f'Expected {expected}, found {_describe(token)}')

    def _punct(self, ch: str, expected: str | None = None) -> Token:
        if self.tok.kind == 'punct' and self.tok.text == ch:
            return self._advance()
        if ch == ';' and self.tok.kind != 'eof':
            previous = self.tokens[self.i - 1]
            raise make('MEL-P005', previous.span, f'Missing ; after {_describe(previous)}')
        raise self._unexpected(expected or repr(ch))

    def _keyword(self, word: str) -> Token:
        if self.tok.kind == 'keyword' and self.tok.text == word:
            return self._advance()
        raise self._unexpected(repr(word))

    def _name(self) -> Token:
        if self.tok.kind == 'ident':
            return self._advance()
        raise self._unexpected('a name')

    # ------------------------------------------------------------ grammar
    def program(self) -> Program:
        first = self.tok
        if first.kind == 'eof':
            raise make('MEL-P001', first.span)
        if not (first.kind == 'keyword' and first.text == 'language'):
            raise make('MEL-P002', first.span, f'Expected language mirum "0.1"; found {_describe(first)}')
        self._advance()
        if not (self.tok.kind == 'keyword' and self.tok.text == 'mirum'):
            raise make('MEL-P002', self.tok.span, f'Expected mirum after language; found {_describe(self.tok)}')
        self._advance()
        if self.tok.kind != 'string':
            raise self._unexpected('the language version string')
        version = self._advance()
        if version.text not in SUPPORTED_VERSIONS:
            raise make('MEL-P003', version.span, f'Unsupported language version {snippet(version.text)!r}; '
                       'supported: "0.1"')
        self._punct(';')
        self._node(first)
        design = self._keyword('design')
        name = self._name()
        self._punct('{')
        assignments: list[Assignment] = []
        seen: set[str] = set()
        while not (self.tok.kind == 'punct' and self.tok.text == '}'):
            if self.tok.kind == 'eof':
                raise make('MEL-P011', self.tok.span, 'Unexpected end of input; expected } to close the design')
            assignment = self._assignment()
            if assignment.name in seen:
                raise make('MEL-P006', assignment.name_span, f'{assignment.name!r} is assigned more than once')
            seen.add(assignment.name)
            assignments.append(assignment)
        close = self._advance()
        if self.tok.kind != 'eof':
            raise make('MEL-P007', self.tok.span)
        return Program(version.text, name.text, tuple(assignments), Span(first.span.start, close.span.end),
                       name.span, Span(design.span.start, close.span.end))

    def _assignment(self) -> Assignment:
        name = self._name()
        self._node(name)
        self._punct('=', "'='")
        value = self._value(depth=3)
        end = self._punct(';')
        return Assignment(name.text, value, Span(name.span.start, end.span.end), name.span)

    def _value(self, depth: int) -> Value:
        token = self.tok
        if depth > MAX_DEPTH:
            raise make('MEL-P008', token.span)
        if token.kind == 'number':
            self._advance()
            self._node(token)
            unit = None
            end = token.span.end
            if self.tok.kind == 'ident' and self.tok.span.start.line == token.span.end.line:
                unit_token = self._advance()
                unit, end = unit_token.text, unit_token.span.end
            return Quantity(token.text, unit, Span(token.span.start, end))
        if token.kind == 'string':
            self._advance()
            self._node(token)
            return String(token.text, token.span)
        if token.kind == 'punct' and token.text == '[':
            return self._list(depth)
        raise self._unexpected('a number, string or list')

    def _list(self, depth: int) -> ListValue:
        open_ = self._advance()
        self._node(open_)
        items: list[Value] = []
        if not (self.tok.kind == 'punct' and self.tok.text == ']'):
            while True:
                if len(items) >= MAX_LIST:
                    raise make('MEL-P010', self.tok.span)
                items.append(self._value(depth + 1))
                if self.tok.kind == 'punct' and self.tok.text == ',':
                    self._advance()
                    continue
                break
        close = self._punct(']', "',' or ']'")
        return ListValue(tuple(items), Span(open_.span.start, close.span.end))
