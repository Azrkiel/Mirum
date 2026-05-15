"""
T4-07a: REASubagent — wraps agents.run_rea().
==============================================

Requirements Engineering Agent (REA) extracts structured design requirements
(DesignRequirements) from a natural-language prompt using the Gemini Lite model.

Uses GEMINI_MODEL_LITE by default (fast, cheap) — configurable via ModelRegistry.
"""

from __future__ import annotations

import time

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class REASubagent(SubagentBase):
    """Subagent wrapping ``agents.run_rea()``.

    Input
    -----
    ctx.prompt : str
        The natural-language prompt to analyse.

    Output payload
    --------------
    {
        "requirements": dict | None,   # DesignRequirements.model_dump() or None
        "domain":       str,           # inferred_domain ("A"–"E") or ""
    }

    On failure (Gemini unavailable, parse error) the payload has
    ``requirements=None`` and ``domain=""`` — the Planner handles
    this gracefully by planning without requirements.
    """

    @property
    def agent_type(self) -> AgentType:
        return "rea"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        from agents import run_rea  # noqa: PLC0415

        prompt = ctx.prompt
        if not prompt:
            raise ValueError("REASubagent: ctx.prompt is empty")

        t0 = time.perf_counter()
        requirements = await run_rea(prompt)
        latency_ms = (time.perf_counter() - t0) * 1000

        req_dict = requirements.model_dump() if requirements is not None else None
        domain = ""
        if requirements is not None:
            domain = (getattr(requirements, "inferred_domain", "") or "").strip().upper()

        return self._ok(ctx, {
            "requirements": req_dict,
            "domain": domain,
        }, latency_ms=latency_ms)
