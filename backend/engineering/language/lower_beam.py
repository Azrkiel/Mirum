"""Beam lowering of a MEL v0.1 program to ProgramIR: a thin wrapper over the general semantic
compiler (``semantic.compile_source``, F05).

``compile_beam`` and ``lower_program`` keep their F04 signatures and return the same ProgramIR the
semantic compiler produces; the field tables (FIELDS, REQUIREMENT_FIELDS, SOURCE_KINDS) and the
capability constants live in ``semantic`` and are re-exported here. Semantic failures raise
``MelError`` with a MEL-S code; ``Diagnostic.ir_code`` carries the matching F02 IR code.
"""
from __future__ import annotations

from .semantic import (CAPABILITY_ID, CAPABILITY_VERSION, FIELDS, REQUIREMENT_FIELDS, SOURCE_KINDS,
                       compile_source, lower_program)

__all__ = ['CAPABILITY_ID', 'CAPABILITY_VERSION', 'FIELDS', 'REQUIREMENT_FIELDS', 'SOURCE_KINDS',
           'compile_beam', 'lower_program']


def compile_beam(source: bytes | str, *, source_id: str | None = None):
    """Parse and lower MEL source (bytes or str) for the beam capability."""
    return compile_source(source, source_id=source_id).ir
