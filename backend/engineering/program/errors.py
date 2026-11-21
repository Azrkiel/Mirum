"""Typed program errors, the IR base model and source spans (F02).

Codes (exact strings):
  IR001 unknown_version, IR002 unknown_field, IR003 duplicate_field,
  IR004 unresolved_reference, IR005 unknown_unit, IR006 unit_mismatch,
  IR007 nonfinite_number, IR008 unknown_operation, IR009 not_executable,
  IR010 invalid_value, IR011 duplicate_id.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

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


class SourceSpan(IRModel):
    """1-based, end inclusive."""
    line: int
    column: int
    end_line: int
    end_column: int
    source_id: str | None = None

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
