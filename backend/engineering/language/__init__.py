"""Mirum Engineering Language (MEL) v0.1 frontend (F04): lexer, parser, syntax tree, formatter and a
provisional beam lowering to ProgramIR. Pure Python (stdlib); never evaluates source text.
Importing this package loads no native CAD, provider or web module; the lowering imports the
engineering contracts lazily.
"""
from .diagnostics import Diagnostic, MelError, Position, Span
from .formatter import format_program, format_source
from .lexer import Token, tokenize
from .parser import parse
from .syntax import Assignment, ListValue, Program, Quantity, String

__all__ = ['Diagnostic', 'MelError', 'Position', 'Span', 'Token', 'tokenize', 'parse', 'format_program',
           'format_source', 'Program', 'Assignment', 'Quantity', 'String', 'ListValue']
