"""
T4-01: SubagentMessage — the typed envelope that every subagent produces.

A SubagentMessage is the *only* thing one subagent hands to another.
No raw strings, no shared globals — everything flows through this envelope.

Design goals
------------
* Fully JSON-serialisable (no numpy, no datetime objects, no pydantic
  models buried in payload — callers must call .model_dump() first).
* Carries the full distributed trace (trace_id → parent_id chain) so
  telemetry can reconstruct the call tree from JSONL alone.
* Minimal stdlib dependencies — messages.py must be importable without
  pydantic, fastapi, or cadquery, so it can be tested in bare Python.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Agent type literals
# ---------------------------------------------------------------------------

AgentType = Literal[
    "orchestrator",
    "rea",
    "clarifier",
    "planner",
    "machinist",
    "critic",
    "assembly_critic",
    "assembler",
    "aero",
    "refiner",
    "mock",  # used in tests
]

# ---------------------------------------------------------------------------
# SubagentMessage
# ---------------------------------------------------------------------------

class SubagentMessage:
    """Structured output from a single subagent invocation.

    Attributes
    ----------
    trace_id : str
        UUID4 assigned by the Orchestrator at pipeline start.  Propagated
        unchanged to all child subagents so every span belongs to one trace.
    parent_id : str | None
        trace_id of the *spawning* subagent.  None for the root Orchestrator
        span.  Enables tree reconstruction: select all rows WHERE
        parent_id = <orchestrator_trace_id>.
    agent_type : AgentType
        Which subagent produced this message.
    payload : dict[str, Any]
        Agent-specific structured output.  Must be JSON-serialisable.
        Convention: empty dict {} on skip/noop, not None.
    metadata : dict[str, Any]
        Observability fields written by the producing subagent:
          model       — Gemini model_id used (or "none" for non-LLM agents)
          tokens_in   — prompt token count (0 if unavailable)
          tokens_out  — completion token count (0 if unavailable)
          latency_ms  — wall-clock time for this subagent's run()
          timestamp   — Unix epoch (float) when the message was produced
          status      — "ok" | "skipped" | "error"
          skip_reason — human-readable explanation when status == "skipped"
          error       — exception repr when status == "error"
    """

    __slots__ = ("trace_id", "parent_id", "agent_type", "payload", "metadata")

    def __init__(
        self,
        *,
        trace_id: str,
        parent_id: str | None,
        agent_type: AgentType,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.parent_id = parent_id
        self.agent_type = agent_type
        self.payload = payload
        self.metadata: dict[str, Any] = metadata if metadata is not None else {}
        # Stamp timestamp if not already present
        self.metadata.setdefault("timestamp", time.time())
        self.metadata.setdefault("status", "ok")
        self.metadata.setdefault("model", "none")
        self.metadata.setdefault("tokens_in", 0)
        self.metadata.setdefault("tokens_out", 0)
        self.metadata.setdefault("latency_ms", 0)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def ok(
        cls,
        *,
        trace_id: str,
        parent_id: str | None,
        agent_type: AgentType,
        payload: dict[str, Any],
        model: str = "none",
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: float = 0.0,
    ) -> "SubagentMessage":
        return cls(
            trace_id=trace_id,
            parent_id=parent_id,
            agent_type=agent_type,
            payload=payload,
            metadata={
                "status": "ok",
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "latency_ms": latency_ms,
                "timestamp": time.time(),
            },
        )

    @classmethod
    def skipped(
        cls,
        *,
        trace_id: str,
        parent_id: str | None,
        agent_type: AgentType,
        reason: str,
    ) -> "SubagentMessage":
        return cls(
            trace_id=trace_id,
            parent_id=parent_id,
            agent_type=agent_type,
            payload={},
            metadata={
                "status": "skipped",
                "skip_reason": reason,
                "model": "none",
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": 0,
                "timestamp": time.time(),
            },
        )

    @classmethod
    def error(
        cls,
        *,
        trace_id: str,
        parent_id: str | None,
        agent_type: AgentType,
        exc: Exception,
        latency_ms: float = 0.0,
    ) -> "SubagentMessage":
        return cls(
            trace_id=trace_id,
            parent_id=parent_id,
            agent_type=agent_type,
            payload={},
            metadata={
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "model": "none",
                "tokens_in": 0,
                "tokens_out": 0,
                "latency_ms": latency_ms,
                "timestamp": time.time(),
            },
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def status(self) -> str:
        return self.metadata.get("status", "ok")

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def is_skipped(self) -> bool:
        return self.status == "skipped"

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for json.dumps (NaN → None)."""
        return _sanitize_floats({
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "agent_type": self.agent_type,
            "payload": self.payload,
            "metadata": self.metadata,
        })

    def to_json(self, **kwargs: Any) -> str:
        """Serialise to a JSON string.  kwargs forwarded to json.dumps."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SubagentMessage":
        return cls(
            trace_id=d["trace_id"],
            parent_id=d.get("parent_id"),
            agent_type=d["agent_type"],
            payload=d.get("payload", {}),
            metadata=d.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, s: str) -> "SubagentMessage":
        return cls.from_dict(json.loads(s))

    def __repr__(self) -> str:
        return (
            f"SubagentMessage(agent_type={self.agent_type!r}, "
            f"status={self.status!r}, "
            f"trace_id={self.trace_id[:8]}...)"
        )


# ---------------------------------------------------------------------------
# SubagentContext — the immutable input bundle passed into every run()
# ---------------------------------------------------------------------------

class SubagentContext:
    """Read-only bundle of everything a subagent needs to do its work.

    Designed to be cheap to construct and pass around.  Heavy objects
    (AssemblyManifest, PolarReport) are stored by reference; subagents
    must not mutate them in-place.

    Parameters
    ----------
    trace_id : str
        Root trace id for the current pipeline invocation.
    prompt : str
        Original user prompt — unchanged throughout the pipeline.
    domain : str
        Inferred domain code ("A" / "B" / "C" / "D" / "E") or "" if unknown.
    manifest : object | None
        AssemblyManifest produced by the Planner.  None before planning.
    flight_env : dict | None
        FlightEnvelope dict for Domain D.  None for non-aerospace prompts.
    airfoil_spec : object | None
        AirfoilSpec from DesignRequirements.  None if absent.
    history : list[SubagentMessage]
        Messages emitted by preceding subagents in this pipeline run.
        Append-only; never remove or reorder.
    extra : dict[str, Any]
        Arbitrary per-subagent configuration (e.g. max_retries, part_id).
    """

    __slots__ = (
        "trace_id", "prompt", "domain", "manifest", "flight_env",
        "airfoil_spec", "history", "extra",
    )

    def __init__(
        self,
        *,
        trace_id: str | None = None,
        prompt: str = "",
        domain: str = "",
        manifest: Any = None,
        flight_env: dict[str, Any] | None = None,
        airfoil_spec: Any = None,
        history: list[SubagentMessage] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.trace_id: str = trace_id or str(uuid.uuid4())
        self.prompt = prompt
        self.domain = domain
        self.manifest = manifest
        self.flight_env = flight_env
        self.airfoil_spec = airfoil_spec
        self.history: list[SubagentMessage] = history if history is not None else []
        self.extra: dict[str, Any] = extra if extra is not None else {}

    def child(self, **overrides: Any) -> "SubagentContext":
        """Return a new SubagentContext inheriting this one's trace_id and
        fields, with specific fields overridden.  Used by Orchestrator to
        build per-subagent contexts without mutating the root."""
        return SubagentContext(
            trace_id=self.trace_id,
            prompt=overrides.get("prompt", self.prompt),
            domain=overrides.get("domain", self.domain),
            manifest=overrides.get("manifest", self.manifest),
            flight_env=overrides.get("flight_env", self.flight_env),
            airfoil_spec=overrides.get("airfoil_spec", self.airfoil_spec),
            history=overrides.get("history", self.history),
            extra=overrides.get("extra", self.extra),
        )

    def last_payload_from(self, agent_type: AgentType) -> dict[str, Any]:
        """Return the most recent payload from a given agent type.

        Useful for downstream subagents that need the output of a specific
        predecessor without coupling to call order.

        Returns {} if no message of that type is found.
        """
        for msg in reversed(self.history):
            if msg.agent_type == agent_type and msg.is_ok:
                return msg.payload
        return {}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _sanitize_floats(obj: Any) -> Any:
    """Replace NaN/Inf floats with None for safe JSON serialisation."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Convenience: generate a fresh trace_id
# ---------------------------------------------------------------------------

def new_trace_id() -> str:
    """Return a new UUID4 string for use as a pipeline trace root."""
    return str(uuid.uuid4())
