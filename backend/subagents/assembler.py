"""
T4-06: AssemblerSubagent — wraps the three assembler code paths.
================================================================

Three modes, selected automatically from the manifest:
  1. Single-part export  → agents.run_single_part_export()
  2. Translation-based   → agents.run_assembler()
  3. Constraint-based    → agents.run_assembler_constraint()

Mode 2/3 selection mirrors the existing Orchestrator logic: if all
mating rules have only translation vectors (no constraint types), mode 2;
otherwise mode 3.

Post-assembly, interpenetration is checked (T1-02) before the output
SubagentMessage is emitted — a failed check sets ``payload["interp_ok"]``
to False with a description, but does NOT cause an error message.  The
Orchestrator / Critic decides whether to retry.

No LLM call in the assembler — the ``model`` property returns "" (no-LLM
entry in ModelRegistry).
"""

from __future__ import annotations

import time

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class AssemblerSubagent(SubagentBase):
    """Subagent covering all three assembly modes.

    Input ctx.extra keys
    --------------------
    manifest : AssemblyManifest
        Full assembly manifest.  Required for multi-part modes.
    step_files : dict[str, str]
        part_id → .step path.  Required for multi-part modes.
    output_filename : str
        Destination .glb path.  Default: "output.glb".
    part_script : str
        Validated CadQuery source for single-part mode.
    mode : "auto" | "single" | "translation" | "constraint"
        Force a specific mode.  "auto" (default) infers from manifest.

    Output payload
    --------------
    {
        "final_script":  str,    # CadQuery script that was executed
        "output_filename": str,
        "mode":          str,    # "single" | "translation" | "constraint"
        "interp_ok":     bool,   # True if interpenetration check passed
        "interp_detail": str,    # "" when ok; description when failed
    }
    """

    def __init__(self, *, mode: str = "auto") -> None:
        self._mode = mode

    @property
    def agent_type(self) -> AgentType:
        return "assembler"

    @property
    def model(self) -> str:
        return ""  # no LLM

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        manifest = ctx.extra.get("manifest")
        step_files: dict[str, str] = ctx.extra.get("step_files", {})
        output_filename: str = ctx.extra.get("output_filename", "output.glb")
        part_script: str = ctx.extra.get("part_script", "")

        mode = self._mode
        if mode == "auto":
            if not manifest or (manifest and len(manifest.parts) == 1):
                mode = "single"
            elif self._is_translation_only(manifest):
                mode = "translation"
            else:
                mode = "constraint"

        t0 = time.perf_counter()
        final_script = await self._dispatch(
            mode, manifest, step_files, output_filename, part_script
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        # Replace placeholder output path (some code paths use "output.glb")
        final_script = final_script.replace("output.glb", output_filename)

        # T1-02: interpenetration check (advisory, non-blocking)
        interp_ok, interp_detail = self._check_interpenetration(step_files)

        return self._ok(ctx, {
            "final_script":  final_script,
            "output_filename": output_filename,
            "mode":          mode,
            "interp_ok":     interp_ok,
            "interp_detail": interp_detail,
        }, latency_ms=latency_ms)

    # ------------------------------------------------------------------
    # Dispatch helpers
    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        mode: str,
        manifest,
        step_files: dict[str, str],
        output_filename: str,
        part_script: str,
    ) -> str:
        if mode == "single":
            from agents import run_single_part_export  # noqa: PLC0415
            if not part_script:
                raise ValueError(
                    "AssemblerSubagent (single mode): ctx.extra['part_script'] is required."
                )
            return await run_single_part_export(part_script, output_filename)

        if manifest is None:
            raise ValueError(
                "AssemblerSubagent (multi-part mode): ctx.extra['manifest'] is required."
            )

        if mode == "translation":
            from agents import run_assembler  # noqa: PLC0415
            script = run_assembler(manifest, step_files)
            return script.replace("output.glb", output_filename)

        if mode == "constraint":
            from agents import run_assembler_constraint  # noqa: PLC0415
            script = run_assembler_constraint(manifest, step_files)
            return script.replace("output.glb", output_filename)

        raise ValueError(f"AssemblerSubagent: unknown mode {mode!r}")

    @staticmethod
    def _is_translation_only(manifest) -> bool:
        """Return True when all mating rules use only translation (no constraint types)."""
        if manifest is None:
            return True
        for rule in manifest.mating_rules:
            if rule.mate_type is not None:
                return False
        return True

    @staticmethod
    def _check_interpenetration(step_files: dict[str, str]) -> tuple[bool, str]:
        """Run the PMC interpenetration check (T1-02).  Best-effort — never raises."""
        if len(step_files) < 2:
            return True, ""
        try:
            # The function is `check_assembly_interpenetration`; importing the
            # non-existent `check_interpenetration` meant the ImportError below
            # fired on every call and this check ALWAYS reported "clean".
            from agents import check_assembly_interpenetration  # noqa: PLC0415
            violations = check_assembly_interpenetration(step_files)
            if violations:
                pairs = ", ".join(f"{a}<->{b}" for a, b in violations)
                return False, f"interpenetration detected between: {pairs}"
            return True, ""
        except ImportError as exc:
            # Genuinely unavailable — say so rather than implying a clean check.
            return True, f"interp_check_unavailable: {exc}"
        except Exception as exc:  # noqa: BLE001
            return True, f"interp_check_error: {exc}"
