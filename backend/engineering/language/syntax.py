"""MEL v0.1 syntax tree (F04). Spans never take part in equality: two trees are equal when they
mean the same source text modulo layout and comments (used by the formatter round trip)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from .diagnostics import Span


@dataclass(frozen=True)
class Quantity:
    number: str  # exactly as written; converted to binary64 only by the lowering
    unit: str | None
    span: Span = field(compare=False, repr=False)


@dataclass(frozen=True)
class String:
    value: str  # decoded
    span: Span = field(compare=False, repr=False)


@dataclass(frozen=True)
class ListValue:
    items: tuple['Value', ...]
    span: Span = field(compare=False, repr=False)


Value = Union[Quantity, String, ListValue]


@dataclass(frozen=True)
class Assignment:
    name: str
    value: Value
    span: Span = field(compare=False, repr=False)  # name through the closing ';'
    name_span: Span = field(compare=False, repr=False)


@dataclass(frozen=True)
class Program:
    version: str
    design: str
    assignments: tuple[Assignment, ...]
    span: Span = field(compare=False, repr=False)  # the whole program
    design_span: Span = field(compare=False, repr=False)  # the design name
    block_span: Span = field(compare=False, repr=False)  # 'design' through the closing '}'

    def get(self, name: str) -> Assignment | None:
        return next((a for a in self.assignments if a.name == name), None)
