"""
T4-05: CriticSubagent + AssemblyCriticSubagent.
================================================

Wraps the two Critic Loop functions from agents.py:
  * CriticSubagent          → run_critic_loop()
  * AssemblyCriticSubagent  → run_assembly_critic_loop()

Design notes
------------
Neither subagent makes direct LLM calls — they delegate entirely to the
existing agent functions which manage their own Gemini calls internally.
The ``model`` property therefore returns "" (no-LLM entry in ModelRegistry).

CriticSubagent is called inside MachinistSubagent (T4-04), not directly
by the Orchestrator.  AssemblyCriticSubagent is called by AssemblerSubagent
(T4-06).

Per-attempt telemetry
---------------------
The Critic Loop already writes per-attempt records to critic_loop.jsonl via
telemetry.py.  The SubagentBase.run() wrapper adds a single span record to
subagent_spans.jsonl covering the entire critic loop for this part (including
all retry attempts), with the final status (ok / error) and total wall-clock
latency.  The two sinks are complementary — critic_loop.jsonl has per-attempt
detail; subagent_spans.jsonl has per-part summary.
"""

from __future__ import annotations

import time
from typing import Any

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class CriticSubagent(SubagentBase):
    """Subagent wrapping ``agents.run_critic_loop()`` for one part.

    Input ctx.extra keys
    --------------------
    part_def : PartDefinition
        The part to compile + validate.  Required.
    max_retries : int
        Override the Critic Loop retry budget (default: from config.py).
    save_step : bool
        Whether to export a .step file (default True for multi-part).

    Output payload
    --------------
    {
        "part_id":   str,
        "code":      str,   # validated CadQuery source
        "step_path": str,   # "" when save_step=False
        "code_len":  int,
    }
    """

    def __init__(
        self,
        *,
        max_retries: int | None = None,
        save_step: bool = True,
    ) -> None:
        self.max_retries = max_retries
        self.save_step = save_step

    @property
    def agent_type(self) -> AgentType:
        return "critic"

    @property
    def model(self) -> str:
        # CriticSubagent orchestrates run_critic_loop() which calls Gemini
        # internally; expose the same registry entry so hot-swap works.
        try:
            from subagents.model_registry import ModelRegistry  # noqa: PLC0415
            return ModelRegistry.instance().get("critic").model_id
        except Exception:  # noqa: BLE001
            return "none"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        from agents import run_critic_loop  # noqa: PLC0415
        from config import CRITIC_MAX_RETRIES  # noqa: PLC0415

        part_def = ctx.extra.get("part_def")
        if part_def is None:
            raise ValueError(
                "CriticSubagent requires ctx.extra['part_def'] (a PartDefinition)."
            )

        max_retries = self.max_retries if self.max_retries is not None else CRITIC_MAX_RETRIES

        t0 = time.perf_counter()
        code, step_path, _report = await run_critic_loop(
            part_def,
            max_retries=max_retries,
            save_step=self.save_step,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        return self._ok(ctx, {
            "part_id":   part_def.part_id,
            "code":      code,
            "step_path": step_path,
            "code_len":  len(code),
        }, latency_ms=latency_ms)


class AssemblyCriticSubagent(SubagentBase):
    """Subagent wrapping ``agents.run_assembly_critic_loop()``.

    Input ctx.extra keys
    --------------------
    manifest : AssemblyManifest
        The manifest to assemble.  Required.
    step_files : dict[str, str]
        Map of part_id → .step file path.  Required.
    output_filename : str
        Destination .glb path.  Required.

    Output payload
    --------------
    {
        "final_script": str,   # validated assembly CadQuery script
        "output_filename": str,
    }
    """

    @property
    def agent_type(self) -> AgentType:
        return "critic"  # shares the "critic" registry entry (same model, same billing)

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        from agents import run_assembly_critic_loop  # noqa: PLC0415

        manifest = ctx.extra.get("manifest")
        step_files: dict[str, str] = ctx.extra.get("step_files", {})
        output_filename: str = ctx.extra.get("output_filename", "output.glb")

        if manifest is None:
            raise ValueError(
                "AssemblyCriticSubagent requires ctx.extra['manifest'] (AssemblyManifest)."
            )

        t0 = time.perf_counter()
        final_script, _asm_report = await run_assembly_critic_loop(manifest, step_files)
        final_script = final_script.replace("output.glb", output_filename)
        latency_ms = (time.perf_counter() - t0) * 1000

        return self._ok(ctx, {
            "final_script": final_script,
            "output_filename": output_filename,
        }, latency_ms=latency_ms)
