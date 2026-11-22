"""Mirum Engineering Language (MEL) v0.1 frontend (F04): lexer, parser, syntax tree, formatter and a
semantic compiler to ProgramIR (compile_source). Pure Python (stdlib); never evaluates source text.
Importing this package loads no native CAD, provider or web module; the lowering imports the
engineering contracts lazily.
"""
from .diagnostics import Diagnostic, MelError, Position, Span
from .formatter import format_program, format_source
from .lexer import Token, tokenize
from .parser import parse
from .semantic import CompiledProgram, compile_source
from .syntax import Assignment, ListValue, Program, Quantity, String

__all__ = ['Diagnostic', 'MelError', 'Position', 'Span', 'Token', 'tokenize', 'parse', 'format_program',
           'format_source', 'compile_source', 'CompiledProgram', 'Program', 'Assignment', 'Quantity', 'String', 'ListValue']
