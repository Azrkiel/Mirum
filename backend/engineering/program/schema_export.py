"""Committed JSON Schemas and operation-registry export for the F02 program IR.

Pure: pydantic + stdlib only. Nothing is written at import time.

    cd backend && python -m engineering.program.schema_export           # write
    cd backend && python -m engineering.program.schema_export --check   # verify
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .errors import Diagnostic
from .evidence import CheckPlan, DeliveredEvidence
from .ir import ExecutionLock, ProgramIR
from .kernel import ExecutionLockV0_2
from .interface import API_SCHEMA_MODELS
from .revisions import JournalEntry, ProgramRevision, RevisionDiff
from .registry import (
    BAY_REGISTRY, DEFAULT_REGISTRY, NATIVE_REGISTRY, PLATE_REGISTRY, REGISTRY_V0_1, THERMAL_REGISTRY,
    ArtifactGraph, OperationDescriptor, OpStream,
)
from .versions import REGISTRY_VERSION, REGISTRY_VERSION_V0_1

__all__ = ['SCHEMA_DIR', 'SCHEMA_MODELS', 'REGISTRY_EXPORT_NAME_V0_1', 'REGISTRY_EXPORT_NAME_V0_2',
           'generated', 'write', 'check', 'main']

SCHEMA_DIR = Path(__file__).with_name('schemas')

SCHEMA_MODELS = {
    'mirum_program_ir_v0_1.schema.json': ProgramIR,
    'mirum_execution_lock_v0_1.schema.json': ExecutionLock,
    'mirum_execution_lock_v0_2.schema.json': ExecutionLockV0_2,
    'mirum_operation_descriptor_v0_1.schema.json': OperationDescriptor,
    'mirum_op_stream_v0_1.schema.json': OpStream,
    'mirum_artifact_graph_v0_1.schema.json': ArtifactGraph,
    'mirum_program_diagnostic_v0_1.schema.json': Diagnostic,
    'mirum_program_revision_v0_1.schema.json': ProgramRevision,
    'mirum_program_journal_v0_1.schema.json': JournalEntry,
    'mirum_revision_diff_v0_1.schema.json': RevisionDiff,
    'mirum_check_plan_v0_1.schema.json': CheckPlan,
    'mirum_delivered_evidence_v0_1.schema.json': DeliveredEvidence,
    # F08: program API request/response models and the interface error envelope.
    **API_SCHEMA_MODELS,
}

REGISTRY_EXPORT_NAME_V0_1 = 'mirum_operation_registry_v0_1.json'
REGISTRY_EXPORT_NAME_V0_2 = 'mirum_operation_registry_v0_2.json'


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=True) + '\n'


def generated() -> dict[str, str]:
    """File name -> exact file text."""
    out = {name: _dump(model.model_json_schema(mode='validation'))
           for name, model in SCHEMA_MODELS.items()}
    # v0_1 stays byte-identical to the pre-F03 export (K35); v0_2 is the registry the planner uses.
    out[REGISTRY_EXPORT_NAME_V0_1] = _dump({
        'registry_version': REGISTRY_VERSION_V0_1,
        'operations': [d.model_dump(mode='json') for d in REGISTRY_V0_1.descriptors()],
    })
    out[REGISTRY_EXPORT_NAME_V0_2] = _dump({
        'registry_version': REGISTRY_VERSION,
        'operations': [d.model_dump(mode='json') for d in DEFAULT_REGISTRY.descriptors()],
    })
    for registry in (BAY_REGISTRY, PLATE_REGISTRY, THERMAL_REGISTRY, NATIVE_REGISTRY):
        out[f'{registry.version}.json'] = _dump({
            'registry_version': registry.version,
            'operations': [d.model_dump(mode='json') for d in registry.descriptors()],
        })
    return out


def write() -> list[str]:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    files = generated()
    for name, text in files.items():
        with open(SCHEMA_DIR / name, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
    return sorted(files)


def check() -> list[str]:
    """Names of missing, different or extra files (empty when in sync)."""
    files = generated()
    bad = []
    for name, text in files.items():
        path = SCHEMA_DIR / name
        if not path.is_file() or path.read_bytes() != text.encode('utf-8'):
            bad.append(name)
    if SCHEMA_DIR.is_dir():
        bad.extend(sorted(p.name for p in SCHEMA_DIR.glob('*.json') if p.name not in files))
    return bad


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if '--check' in args:
        bad = check()
        for name in bad:
            print(f'schema out of date: {name}', file=sys.stderr)
        return 1 if bad else 0
    for name in write():
        print(f'wrote {name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
