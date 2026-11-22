"""Typed program errors, the IR base model and source spans (F02).

Codes (exact strings):
  IR001 unknown_version, IR002 unknown_field, IR003 duplicate_field,
  IR004 unresolved_reference, IR005 unknown_unit, IR006 unit_mismatch,
  IR007 nonfinite_number, IR008 unknown_operation, IR009 not_executable,
  IR010 invalid_value, IR011 duplicate_id, IR012 budget_exceeded.
"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Free-text bound shared by every IR string field (the legacy contracts Text limit).
Str240 = Annotated[str, StringConstraints(max_length=240)]

CODES = {
    'IR001': 'unknown_version',
    'IR002': 'unknown_field',
    'IR003': 'duplicate_field',
    'IR004': 'unresolved_reference',
    'IR005': 'unknown_unit',
    'IR006': 'unit_mismatch',
    'IR007': 'nonfinite_number',
    'IR008': 'unknown_operation',
    'IR009': 'not_executable',
    'IR010': 'invalid_value',
    'IR011': 'duplicate_id',
    'IR012': 'budget_exceeded',
}


class IRModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class ProgramError(ValueError):
    """Typed program error. `field` is a dotted path; `span` the nearest SourceSpan."""

    def __init__(self, code: str, message: str, field: str | None = None, span: 'SourceSpan | None' = None):
        super().__init__(f'{code}: {message}')
        self.code = code
        self.message = message
        self.field = field
        self.span = span

    def diagnostic(self) -> 'Diagnostic':
        return Diagnostic(code=self.code, message=self.message, field=self.field, span=self.span)


# Largest line or column a span may name: a 64 KiB source has at most 65537 lines (K30).
MAX_POSITION = 64 * 1024 + 1
Position = Annotated[int, Field(le=MAX_POSITION)]


class SourceSpan(IRModel):
    """1-based, end inclusive. Scalar fields only, so it needs no budget pre-pass (K30)."""
    line: Position
    column: Position
    end_line: Position
    end_column: Position
    source_id: Str240 | None = None

    @model_validator(mode='after')
    def _check(self):
        if min(self.line, self.column, self.end_line, self.end_column) < 1:
            raise ProgramError('IR010', 'span positions are 1-based and must be >= 1')
        if (self.end_line, self.end_column) < (self.line, self.column):
            raise ProgramError('IR010', 'span end is before its start')
        return self


class Diagnostic(IRModel):
    code: str
    message: str
    field: str | None = None
    span: SourceSpan | None = None
