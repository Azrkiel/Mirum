"""Mirum program IR (F02). Pure records and contracts, no execution.

Imports: pydantic, stdlib, cem.core (canonical JSON) and, for the beam bridge only, the pure
record modules engineering.contracts and engineering.capabilities. It never imports cadquery, OCP,
google.genai, fastapi or httpx (a test pins this).
"""
from __future__ import annotations

from .versions import (  # noqa: F401
    ARTIFACT_GRAPH_VERSION, CONSTRUCTION_GRAPH_VERSION, EXECUTION_LOCK_VERSION,
    OP_STREAM_VERSION, PROGRAM_IR_VERSION, REGISTRY_VERSION, SEMANTICS_VERSION,
)
from .beam_bridge import (  # noqa: F401
    beam_capability_ref, brief_content_from_program, program_from_brief_content,
)
from .registry import (  # noqa: F401
    DEFAULT_REGISTRY, ArgSpec, ArtifactGraph, ArtifactNode, OpStream, OpStreamEntry,
    OperationDescriptor, OperationRegistry, OutputSpec, load_artifact_graph_json,
    validate_op_stream,
)
