"""Four independent hashes for the program IR (F02, D2).

Canonical JSON rule: UTF-8 of json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False); floats rendered by Python repr (shortest round trip); all physical numbers are floats already normalized to canonical units; -0.0 rendered as 0.0; non-finite rejected (IR007); tuples as arrays. Equivalent to cem.core.canonical_json for finite input (reuse it after the checks).

The hashes are independent: editing a span, provenance or design name changes no
semantic hash; editing backend, exports or policy versions changes the execution
lock hash only; editing a requirement or candidate changes the semantic hash.
"""
from __future__ import annotations

import hashlib
import math

from pydantic import BaseModel

from cem.core import canonical_json as _cem_canonical_json

from .errors import ProgramError
from .ir import ExecutionLock, ProgramIR, execution_lock
from .versions import SEMANTICS_VERSION


def _prepare(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return _clean(value)


def _clean(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProgramError('IR007', 'non-finite number cannot be hashed')
        return 0.0 if value == 0 else value
    if isinstance(value, str):
        _utf8(value)
        return value
    if isinstance(value, dict):
        return {_clean(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _utf8(text: str) -> bytes:
    try:
        return text.encode('utf-8')
    except UnicodeEncodeError:
        raise ProgramError('IR010', 'text contains a lone surrogate and cannot be hashed') from None


def canonical_json(value) -> str:
    text = _cem_canonical_json(_prepare(value))
    _utf8(text)
    return text


def _sha(text: str) -> str:
    return hashlib.sha256(_utf8(text)).hexdigest()


def source_sha256(raw: str | bytes | dict | list) -> str:
    if isinstance(raw, str):
        return hashlib.sha256(_utf8(raw)).hexdigest()
    if isinstance(raw, (bytes, bytearray)):
        return hashlib.sha256(bytes(raw)).hexdigest()
    if isinstance(raw, (dict, list)):
        return _sha(canonical_json(raw))
    raise TypeError('source_sha256 takes str, bytes, dict or list')


def semantic_payload(ir: ProgramIR) -> dict:
    def dump(m):
        return m.model_dump(mode='json')

    def value_key(v):
        # numeric order for quantities, text order for text; values are unique per parameter (IR010 otherwise)
        return (v['kind'], v.get('dimension', ''), v.get('value', 0.0), v.get('text', ''))

    return {
        'semantics_version': SEMANTICS_VERSION,
        'capability': {'id': ir.capability.id, 'version': ir.capability.version},
        'requirements': sorted(
            ({'id': r.id, 'value': dump(r.value)} for r in ir.requirements.requirements),
            key=lambda x: x['id']),
        'candidates': {
            'mode': ir.candidates.mode,
            'parameters': sorted(
                ({'id': p.id, 'values': sorted((dump(v) for v in p.values), key=value_key)}
                 for p in ir.candidates.parameters),
                key=lambda x: x['id']),
        },
        'analysis': sorted(
            ({'id': a.id, 'check': a.check, 'requirement_refs': sorted(a.requirement_refs)}
             for a in ir.analysis), key=lambda x: x['id']),
        'evidence': sorted(
            ({'id': e.id, 'analysis_ref': e.analysis_ref, 'required': e.required} for e in ir.evidence),
            key=lambda x: x['id']),
        'construction': [
            {k: v for k, v in dump(s).items() if k != 'span'} for s in ir.construction.steps],
    }


def semantic_sha256(ir: ProgramIR) -> str:
    return _sha(canonical_json(semantic_payload(ir)))


def execution_lock_sha256(lock_or_ir: ExecutionLock | ProgramIR) -> str:
    lock = execution_lock(lock_or_ir) if isinstance(lock_or_ir, ProgramIR) else lock_or_ir
    return _sha(canonical_json(lock))


def artifact_sha256(data: bytes) -> str:
    """Reserved: hash of produced artifact bytes. Bytes only."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError('artifact_sha256 takes bytes')
    return hashlib.sha256(bytes(data)).hexdigest()
