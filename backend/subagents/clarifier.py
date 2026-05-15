"""
T4-07b: ClarifierSubagent — wraps agents.run_clarifier().
==========================================================

Pre-Planner clarification: asks the LLM to identify the most critical
missing specifications in a design prompt. Returns up to 4 clarifying
questions as a list[str] in the SubagentMessage payload.

Uses GEMINI_MODEL_LITE by default (fast, cheap) — configurable via
ModelRegistry (T4-10).

Empty list behaviour
--------------------
``run_clarifier()`` already returns ``[]`` when the prompt is
sufficiently detailed. The Orchestrator treats an empty list as a signal
to skip the clarification gate and proceed directly to REA + Planner.
Clarification is always optional — ClarifierSubagent never raises.
"""

from __future__ import annotations

import time

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class ClarifierSubagent(SubagentBase):
    """Subagent wrapping ``agents.run_clarifier()``.

    Input
    -----
    ctx.prompt : str
        The natural-language design prompt to analyse.

    Output payload
    --------------
    {
        "questions":    list[str],  # up to 4 clarifying questions; [] if none
        "needs_clarification": bool,  # True when questions is non-empty
    }

    Failure behaviour
    -----------------
    On any error (Gemini unavailable, JSON parse failure), the payload
    carries an empty list with ``needs_clarification=False`` — the
    Orchestrator proceeds as if no clarification is needed.  Clarification
    is advisory only.
    """

    @property
    def agent_type(self) -> AgentType:
        return "clarifier"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        from agents import run_clarifier  # noqa: PLC0415

        prompt = ctx.prompt
        if not prompt:
            raise ValueError("ClarifierSubagent: ctx.prompt is empty")

        t0 = time.perf_counter()
        questions: list[str] = await run_clarifier(prompt)
        latency_ms = (time.perf_counter() - t0) * 1000

        # Ensure we always return a list[str] — guard against unexpected types
        if not isinstance(questions, list):
            questions = []
        questions = [str(q) for q in questions if q][:4]

        return self._ok(ctx, {
            "questions": questions,
            "needs_clarification": len(questions) > 0,
        }, latency_ms=latency_ms)
