"""
T4-04: MachinistSubagent — parallel-safe subagent wrapping the Critic Loop.

Each MachinistSubagent instance handles ONE part definition.  Instances are
completely stateless and can be gathered concurrently:

    tasks = [MachinistSubagent().run(ctx_for_part) for part in manifest.parts]
    messages = await asyncio.gather(*tasks)

The existing ``run_critic_loop()`` in ``agents.py`` is called unchanged.
MachinistSubagent adds:

  * Proper SubagentBase ABC compliance (agent_type, _run, model property)
  * SubagentMessage wrapping with timing + token metadata
  * trace_id propagation from SubagentContext → message
  * A ``MAX_MACHINIST_CONCURRENCY`` env-var cap on top of the existing
    per-process semaphore inside ``run_critic_loop``
  * Backward-compat shim: ``run_machinist_batch`` updated to gather
    MachinistSubagent instances

Parallel execution proof
------------------------
  Before T4-04 (sequential, Orchestrator's _stage_machine already does
  asyncio.as_completed): individual parts were dispatched via as_completed
  but the internal ``_MACHINIST_SEMAPHORE(3)`` inside run_critic_loop
  was the only concurrency guard.

  After T4-04: the same internal semaphore still applies (it's inside
  agents.py), but the outer Orchestrator now uses MachinistSubagent.run()
  which is a proper async method — enabling easy future swap to a real
  subprocess-per-part model without touching Orchestrator code.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


# ---------------------------------------------------------------------------
# Concurrency cap (outer guard; inner _MACHINIST_SEMAPHORE in agents.py
# provides the fine-grained cap per-process)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_CONCURRENT = 4

def _get_sem() -> asyncio.Semaphore:
    """Return a Semaphore sized from MIRUM_MAX_MACHINIST env var (default 4)."""
    n = int(os.environ.get("MIRUM_MAX_MACHINIST", _DEFAULT_MAX_CONCURRENT))
    return asyncio.Semaphore(n)


# ---------------------------------------------------------------------------
# MachinistSubagent
# ---------------------------------------------------------------------------

class MachinistSubagent(SubagentBase):
    """Subagent that manufactures a single part via the Critic Loop.

    Parameters
    ----------
    max_retries : int
        Maximum Machinist-retry attempts per part.  Forwarded to
        ``run_critic_loop``.  Defaults to 3.
    save_step : bool
        Whether to export a .step file for the deterministic assembler.
        Single-part requests set this to False.  Defaults to True.
    """

    def __init__(
        self,
        *,
        max_retries: int = 3,
        save_step: bool = True,
    ) -> None:
        self.max_retries = max_retries
        self.save_step = save_step

    @property
    def agent_type(self) -> AgentType:
        return "machinist"

    @property
    def model(self) -> str:
        # T4-10 hook: replace with ModelRegistry.instance().get("machinist").model_id
        from config import GEMINI_MODEL_HEAVY  # noqa: PLC0415
        return GEMINI_MODEL_HEAVY

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        """Run the Critic Loop for the part described in ctx.extra.

        Expected ctx.extra keys
        -----------------------
        part_def : PartDefinition
            The part to manufacture.  Required.

        Returns
        -------
        SubagentMessage
            payload = {
                "part_id":   str,
                "code":      str,   # validated CadQuery source
                "step_path": str,   # path to pre-compiled .step (or "" if save_step=False)
                "code_len":  int,
            }
        """
        from agents import run_critic_loop  # noqa: PLC0415 — backward compat

        part_def = ctx.extra.get("part_def")
        if part_def is None:
            raise ValueError(
                "MachinistSubagent requires ctx.extra['part_def'] to be set."
            )

        t0 = time.perf_counter()
        code, step_path, geometry_certificate = await run_critic_loop(
            part_def,
            max_retries=self.max_retries,
            save_step=self.save_step,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        return self._ok(
            ctx,
            payload={
                "part_id": part_def.part_id,
                "code": code,
                "step_path": step_path,
                "code_len": len(code),
                "geometry_certificate": geometry_certificate,
            },
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# Parallel batch helper
# ---------------------------------------------------------------------------

async def run_parts_parallel(
    parts: list[Any],
    *,
    root_ctx: SubagentContext,
    max_retries: int = 3,
    save_step: bool = True,
    semaphore: asyncio.Semaphore | None = None,
) -> list[SubagentMessage]:
    """Run MachinistSubagent for each part concurrently.

    Uses an asyncio.Semaphore to cap concurrent instances at
    MIRUM_MAX_MACHINIST (default 4).  The internal
    ``_MACHINIST_SEMAPHORE`` inside ``run_critic_loop`` provides
    additional fine-grained control.

    Parameters
    ----------
    parts : list[PartDefinition]
        Parts to manufacture in parallel.
    root_ctx : SubagentContext
        The pipeline root context.  Each part gets a child context with
        ``extra={"part_def": part}``.
    max_retries : int
        Forwarded to MachinistSubagent.
    save_step : bool
        Forwarded to MachinistSubagent.
    semaphore : asyncio.Semaphore | None
        Optional external semaphore.  If None, creates one sized from
        ``MIRUM_MAX_MACHINIST`` env var.

    Returns
    -------
    list[SubagentMessage]
        One message per part, in the same order as ``parts``.
        Messages may be ``is_error=True`` — callers must check.
    """
    sem = semaphore or _get_sem()

    async def _build_one(part) -> SubagentMessage:
        async with sem:
            ctx = root_ctx.child(extra={"part_def": part})
            msg = await MachinistSubagent(
                max_retries=max_retries, save_step=save_step
            ).run(ctx)
            # Append to shared history (thread-safe: asyncio is single-threaded)
            root_ctx.history.append(msg)
            return msg

    return list(await asyncio.gather(*[_build_one(p) for p in parts]))


# ---------------------------------------------------------------------------
# Backward-compat shim: updated run_machinist_batch
# ---------------------------------------------------------------------------

async def run_machinist_batch_subagent(
    parts: list[Any],
    root_ctx: SubagentContext | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Drop-in for ``agents.run_machinist_batch`` using MachinistSubagent.

    Deduplicates parts with identical descriptions (same behaviour as the
    original).  Returns (part_scripts, step_files).

    If ``root_ctx`` is None, creates a minimal context with a fresh
    trace_id so the shim can be called without a full pipeline context.
    """
    if root_ctx is None:
        from subagents.messages import SubagentContext as _Ctx  # noqa: PLC0415
        root_ctx = _Ctx()

    # Deduplicate
    desc_to_parts: dict[str, list] = {}
    for part in parts:
        desc_to_parts.setdefault(part.description, []).append(part)
    unique_parts = [group[0] for group in desc_to_parts.values()]

    messages = await run_parts_parallel(unique_parts, root_ctx=root_ctx)

    # Map description → result
    desc_to_msg: dict[str, SubagentMessage] = {
        part.description: msg
        for part, msg in zip(unique_parts, messages)
    }

    part_scripts: dict[str, str] = {}
    step_files: dict[str, str] = {}
    for part in parts:
        msg = desc_to_msg[part.description]
        if msg.is_error:
            raise RuntimeError(
                f"Part '{part.part_id}' failed: {msg.metadata.get('error')}"
            )
        part_scripts[part.part_id] = msg.payload["code"]
        step_files[part.part_id] = msg.payload["step_path"]

    return part_scripts, step_files
