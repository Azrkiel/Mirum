"""
T4-03: PlannerSubagent — wraps agents.run_planner().
=====================================================

Extracts the Planner into a proper SubagentBase subclass with:
  * Isolated context window (system + user prompt only, no history bleed)
  * Model read from ModelRegistry (hot-swappable via POST /admin/model-config)
  * SubagentMessage output carrying the full AssemblyManifest.model_dump()
  * Backward-compatible shim: run_planner() in agents.py can call this

Context window isolation
------------------------
The Planner's LLM call has always been self-contained (no shared state
with Machinist or Critic).  The subagent wrapper makes this explicit:
only ``ctx.prompt`` and the REA payload (if present in ``ctx.history``)
are passed to the Planner — no critic loop history, no prior machinist
code, no clarifier transcript.

REA payload injection
---------------------
If a REASubagent has already run, its output is in ctx.history.  The
Planner subagent retrieves it via ``ctx.last_payload_from("rea")`` and
passes it as the ``requirements`` keyword argument to run_planner() —
exactly what the original Orchestrator did in _stage_plan().
"""

from __future__ import annotations

import time

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class PlannerSubagent(SubagentBase):
    """Subagent that wraps ``agents.run_planner()``.

    Parameters
    ----------
    prompt : str | None
        Override the planning prompt.  When None, ``ctx.prompt`` is used.
        Providing an explicit override lets the Orchestrator inject a
        clarification-enriched prompt without mutating ctx.

    Input ctx.extra keys
    --------------------
    (none required — prompt comes from ctx.prompt; requirements from history)

    Output payload
    --------------
    {
        "manifest":      dict,    # AssemblyManifest.model_dump()
        "is_single_part": bool,
        "part_count":    int,
        "assembly_name": str,
    }
    """

    def __init__(self, prompt: str | None = None) -> None:
        self._prompt_override = prompt

    @property
    def agent_type(self) -> AgentType:
        return "planner"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        from agents import run_planner  # noqa: PLC0415 — backward compat

        prompt = self._prompt_override or ctx.prompt
        if not prompt:
            raise ValueError("PlannerSubagent: ctx.prompt is empty and no override given")

        # Retrieve REA requirements from history if available
        rea_payload = ctx.last_payload_from("rea")
        requirements = None
        if rea_payload:
            # rea_payload["requirements"] is a DesignRequirements.model_dump() dict.
            # run_planner() accepts a DesignRequirements object OR None; we need to
            # reconstruct it from the dict.
            try:
                from schemas import DesignRequirements  # noqa: PLC0415
                requirements = DesignRequirements.model_validate(
                    rea_payload.get("requirements") or rea_payload
                )
            except Exception:  # noqa: BLE001
                requirements = None  # graceful degradation

        t0 = time.perf_counter()
        manifest = await run_planner(prompt, requirements=requirements)
        latency_ms = (time.perf_counter() - t0) * 1000

        from agents import is_single_part  # noqa: PLC0415
        return self._ok(ctx, {
            "manifest": manifest.model_dump(),
            "is_single_part": is_single_part(manifest),
            "part_count": len(manifest.parts),
            "assembly_name": manifest.assembly_name,
        }, latency_ms=latency_ms)
