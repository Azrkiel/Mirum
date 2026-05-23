"""
T4-01: SubagentBase — the abstract base class every subagent implements.

Contract
--------
Every subagent:
  1. Has an ``agent_type`` property matching an AgentType literal.
  2. Has a ``model`` property that reads from ModelRegistry (T4-10) or falls
     back to a hardcoded default.  This is the hot-swap hook.
  3. Implements ``async def run(ctx: SubagentContext) -> SubagentMessage``.
     The method MUST NOT raise — all errors are caught and returned as
     SubagentMessage.error(...).  This keeps the Orchestrator's gather()
     loops simple: every completed future holds a message, never an exception.

Implementation helpers
----------------------
``_timed_run`` wraps the core ``_run`` method with timing and error
capture.  Subclasses implement ``_run``; callers call ``run``.

Telemetry
---------
After run() returns, the message is appended to ctx.history so downstream
subagents can inspect it via ctx.last_payload_from(agent_type).  The
Orchestrator is responsible for appending; individual subagents must not
mutate ctx.history.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from subagents.messages import AgentType, SubagentContext, SubagentMessage
from subagents.telemetry_mixin import TelemetryMixin  # noqa: E402

if TYPE_CHECKING:
    pass  # avoid circular imports in type-check-only context


class SubagentBase(TelemetryMixin, ABC):
    """Abstract base for all Mirum subagents.

    Subclasses must implement:
      - ``agent_type`` property (class-level or instance-level)
      - ``_run(ctx)`` — the actual logic, may raise freely

    They inherit:
      - ``run(ctx)`` — wraps ``_run`` with timing + error catch → always
        returns a SubagentMessage, never raises
      - ``model`` property — default implementation returns "none"; override
        to pull from ModelRegistry (T4-10)
    """

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def agent_type(self) -> AgentType:
        """Return the agent type literal for this subagent."""
        ...

    @abstractmethod
    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        """Core logic.  May raise freely — ``run()`` will catch and wrap."""
        ...

    # ------------------------------------------------------------------
    # Model property (override in T4-10 to use ModelRegistry)
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        """Gemini model id for this subagent, read from ModelRegistry (T4-10).

        Returns "" for non-LLM subagents (assembler, aero) and the
        registry's configured model_id for LLM subagents.  Subclasses
        may override this property to bypass the registry during testing.
        """
        try:
            from subagents.model_registry import ModelRegistry  # noqa: PLC0415
            model_id = ModelRegistry.instance().get(self.agent_type).model_id
            # "" is the _NO_LLM sentinel (no LLM calls); expose as "none" to
            # callers so the telemetry / test contract is uniform.
            return model_id or "none"
        except Exception:  # noqa: BLE001
            return "none"

    # ------------------------------------------------------------------
    # Public entry point — wraps _run with timing + error capture
    # ------------------------------------------------------------------

    async def run(self, ctx: SubagentContext) -> SubagentMessage:
        """Run the subagent and always return a SubagentMessage.

        Never raises.  On error, returns SubagentMessage.error(...) with
        the exception repr in metadata["error"].

        The caller (Orchestrator) is responsible for appending the returned
        message to ctx.history so downstream subagents can access it.
        """
        t0 = time.perf_counter()
        try:
            msg = await self._run(ctx)
        except Exception as exc:  # noqa: BLE001 — intentional broad catch
            latency_ms = (time.perf_counter() - t0) * 1000
            msg = SubagentMessage.error(
                trace_id=ctx.trace_id,
                parent_id=None,
                agent_type=self.agent_type,
                exc=exc,
                latency_ms=latency_ms,
            )
        else:
            # Patch latency into metadata if the subagent didn't set it
            latency_ms = (time.perf_counter() - t0) * 1000
            if not msg.metadata.get("latency_ms"):
                msg.metadata["latency_ms"] = latency_ms

        # T4-09: emit a SubagentSpan telemetry record (best-effort)
        try:
            self._emit_span(
                trace_id=msg.trace_id,
                parent_id=msg.parent_id or "",
                agent_type=str(self.agent_type),
                model=self.model,
                latency_ms=msg.metadata.get("latency_ms", 0.0),
                tokens_in=msg.metadata.get("tokens_in", 0),
                tokens_out=msg.metadata.get("tokens_out", 0),
                status=msg.metadata.get("status", "ok"),
            )
        except Exception:  # noqa: BLE001
            pass

        return msg

    # ------------------------------------------------------------------
    # Helper: build a child message with this subagent's trace linkage
    # ------------------------------------------------------------------

    def _ok(
        self,
        ctx: SubagentContext,
        payload: dict,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: float = 0.0,
    ) -> SubagentMessage:
        return SubagentMessage.ok(
            trace_id=ctx.trace_id,
            parent_id=None,
            agent_type=self.agent_type,
            payload=payload,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )

    def _skipped(self, ctx: SubagentContext, reason: str) -> SubagentMessage:
        return SubagentMessage.skipped(
            trace_id=ctx.trace_id,
            parent_id=None,
            agent_type=self.agent_type,
            reason=reason,
        )
