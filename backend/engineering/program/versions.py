"""Version identifiers for the Mirum program IR (F02)."""
from __future__ import annotations

PROGRAM_IR_VERSION = 'mirum_program_ir_v0_1'
SEMANTICS_VERSION = 'mirum_semantics_v0_1'
REGISTRY_VERSION = 'mirum_operation_registry_v0_2'
# The v0.1 (beam-only) registry stays addressable as REGISTRY_V0_1 with this version string.
REGISTRY_VERSION_V0_1 = 'mirum_operation_registry_v0_1'
EXECUTION_LOCK_VERSION = 'mirum_execution_lock_v0_1'
EXECUTION_LOCK_VERSION_V0_2 = 'mirum_execution_lock_v0_2'
# Reserved: construction graphs must be empty in v0.1 programs.
CONSTRUCTION_GRAPH_VERSION = 'mirum_construction_graph_v0_1'
OP_STREAM_VERSION = 'mirum_op_stream_v0_1'
ARTIFACT_GRAPH_VERSION = 'mirum_artifact_graph_v0_1'
# MEL (F05): language version and compiler version recorded by the MEL semantic compiler.
MEL_LANGUAGE_VERSION = '0.1'
MEL_COMPILER_VERSION = 'mirum_mel_compiler_v0_1'
# F06: transactional program revisions, journal and compare records.
PROGRAM_REVISION_VERSION = 'mirum_program_revision_v0_1'
PROGRAM_JOURNAL_VERSION = 'mirum_program_journal_v0_1'
REVISION_DIFF_VERSION = 'mirum_revision_diff_v0_1'
