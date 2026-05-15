"""
T4-02: Orchestrator — central state machine routing the Mirum pipeline.

The Orchestrator wraps the existing ``_pipeline()`` logic from ``main.py``
into a class with:
  - An explicit ``PipelineState`` enum tracking progression
  - ``SubagentContext`` / ``SubagentMessage`` for tracing every stage
  - Delegation to the existing ``agents.py`` functions (backward compat)
  - Hooks for future subagent extractions (T4-03 through T4-08)

Usage (from main.py)
--------------------
    from subagents.orchestrator import Orchestrator
    from subagents.messages import SubagentContext

    async def _pipeline(prompt, run_aero=False):
        ctx = SubagentContext(prompt=prompt)
        async for sse_event in Orchestrator(ctx, run_aero=run_aero).run():
            yield sse_event

SSE event format is byte-identical to the previous inline _pipeline().
No frontend changes required.

State machine
-------------
    INIT → REA → PLAN → MACHINE → ASSEMBLE → AERO → DONE
                                                   └──── FAILED (any stage)

Notes on backward compatibility
--------------------------------
All existing ``agents.py`` functions are called directly.  As individual
subagents are extracted (T4-03 through T4-08), replace each delegation
call with the corresponding SubagentBase.run() invocation.  The
SubagentMessage from each call is appended to ctx.history, enabling
downstream subagents to use ctx.last_payload_from() rather than shared
mutable state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import time
import uuid
from collections.abc import AsyncGenerator
from enum import Enum, auto
from typing import TYPE_CHECKING

from subagents.messages import SubagentContext, SubagentMessage, new_trace_id

if TYPE_CHECKING:
    pass

logger = logging.getLogger("mirum.orchestrator")

# Writable scratch directory — mirrors the one in main.py
_WORK_DIR = pathlib.Path(os.environ.get("MIRUM_WORK_DIR", "/tmp/mirum/work"))


# ---------------------------------------------------------------------------
# Pipeline state machine
# ---------------------------------------------------------------------------

class PipelineState(Enum):
    INIT       = auto()
    REA        = auto()   # Requirements Engineering Agent
    PLAN       = auto()   # Planner → AssemblyManifest
    MACHINE    = auto()   # Machinist × N (concurrent) + Critic loops
    ASSEMBLE   = auto()   # Deterministic assembler → STEP + GLB
    AERO       = auto()   # XFOIL polar (Domain D only, non-blocking)
    DONE       = auto()
    FAILED     = auto()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """State-machine orchestrator for the text-to-CAD pipeline.

    Parameters
    ----------
    ctx : SubagentContext
        Root context for this pipeline run.  The Orchestrator stamps a
        fresh ``trace_id`` if one is not already present.
    run_aero : bool
        When True and domain is D, run XFOIL after the assembler.
    work_dir : pathlib.Path | None
        Override the work directory for GLB output.  Defaults to
        ``$MIRUM_WORK_DIR`` or ``/tmp/mirum/work``.
    """

    def __init__(
        self,
        ctx: SubagentContext,
        *,
        run_aero: bool = False,
        work_dir: pathlib.Path | None = None,
    ) -> None:
        self.ctx = ctx
        self.run_aero = run_aero
        self.work_dir = work_dir or _WORK_DIR
        self.state = PipelineState.INIT
        self._output_filename: str | None = None
        self._manifest = None
        self._requirements = None
        self._step_files: dict[str, str] = {}
        self._final_script: str = ""
        self._param_dict: dict = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> AsyncGenerator[str, None]:
        """Run the full pipeline, yielding SSE progress events.

        Delegates to the existing agents.py functions for backward
        compatibility.  Each stage appends a SubagentMessage to
        ctx.history for distributed tracing.

        Yields
        ------
        str
            SSE event strings (``data: {...}\n\n``).
        """
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._output_filename = str(
            self.work_dir / (uuid.uuid4().hex + ".glb")
        )

        # --- Stage: REA + PLAN ---
        self.state = PipelineState.REA
        async for event in self._stage_plan():
            yield event
        if self.state == PipelineState.FAILED:
            return

        # --- Stage: MACHINE ---
        self.state = PipelineState.MACHINE
        async for event in self._stage_machine():
            yield event
        if self.state == PipelineState.FAILED:
            return

        # --- Stage: ASSEMBLE (multi-part only) ---
        # Single-part path assembles inline inside _stage_machine
        if self._manifest and not self._is_single_part():
            self.state = PipelineState.ASSEMBLE
            async for event in self._stage_assemble():
                yield event
            if self.state == PipelineState.FAILED:
                return

        # --- Stage: GLB export ---
        async for event in self._stage_export():
            yield event
        if self.state == PipelineState.FAILED:
            return

        # --- Stage: AERO (optional, non-blocking) ---
        aero_report: dict | None = None
        if self.run_aero:
            self.state = PipelineState.AERO
            aero_report, aero_detail = await self._stage_aero()
            yield _event("aero", "done" if aero_report else "skipped", aero_detail)

        # --- DONE ---
        self.state = PipelineState.DONE
        yield self._complete_event(aero_report=aero_report)

    # ------------------------------------------------------------------
    # Stage: REA → Planner
    # ------------------------------------------------------------------

    async def _stage_plan(self) -> AsyncGenerator[str, None]:
        from agents import run_rea, run_planner, is_single_part  # T4-03 hook

        yield _event("planner", "running")
        t0 = time.perf_counter()
        try:
            self._requirements = await run_rea(self.ctx.prompt)
            self._manifest = await run_planner(
                self.ctx.prompt, requirements=self._requirements
            )
        except Exception as exc:
            logger.exception("Planner failed")
            self.state = PipelineState.FAILED
            msg = _safe_err(exc)
            # Record failure in history
            self.ctx.history.append(SubagentMessage.error(
                trace_id=self.ctx.trace_id,
                parent_id=None,
                agent_type="planner",
                exc=exc,
                latency_ms=(time.perf_counter() - t0) * 1000,
            ))
            yield _event("planner", "error", msg)
            yield _error_complete(msg, script="")
            return

        latency_ms = (time.perf_counter() - t0) * 1000
        self.ctx.history.append(SubagentMessage.ok(
            trace_id=self.ctx.trace_id,
            parent_id=None,
            agent_type="planner",
            payload={"manifest": self._manifest.model_dump()},
            latency_ms=latency_ms,
        ))

        part_names = [p.part_id for p in self._manifest.parts]
        single = is_single_part(self._manifest)
        mode = "single-part" if single else f"{len(self._manifest.parts)}-part assembly"
        yield _event("planner", "done", f"Planned {mode}: {', '.join(part_names)}")

    # ------------------------------------------------------------------
    # Stage: Machinist (single-part or multi-part concurrent)
    # ------------------------------------------------------------------

    async def _stage_machine(self) -> AsyncGenerator[str, None]:
        from agents import (  # T4-04 hook
            run_critic_loop,
            run_single_part_export,
            ScriptError,
        )
        from compiler import execute_cad_script
        from postprocessing import parameterize_script

        manifest = self._manifest
        single = self._is_single_part()

        if single:
            # --- Single-part fast path ---
            part = manifest.parts[0]
            label = f"machinist:{part.part_id}"
            yield _event(label, "running", "Manufacturing single part")
            t0 = time.perf_counter()
            try:
                code, _ = await run_critic_loop(part, save_step=False)
            except Exception as exc:
                latency_ms = (time.perf_counter() - t0) * 1000
                self.ctx.history.append(SubagentMessage.error(
                    trace_id=self.ctx.trace_id, parent_id=None,
                    agent_type="machinist", exc=exc, latency_ms=latency_ms,
                ))
                self.state = PipelineState.FAILED
                msg = _safe_err(exc)
                yield _event(label, "error", msg)
                yield _error_complete(msg, script=getattr(exc, "script", ""))
                return

            self.ctx.history.append(SubagentMessage.ok(
                trace_id=self.ctx.trace_id, parent_id=None,
                agent_type="machinist",
                payload={"part_id": part.part_id, "code_len": len(code)},
                latency_ms=(time.perf_counter() - t0) * 1000,
            ))
            yield _event(label, "done", f"Part '{part.part_id}' validated")

            # Direct export (no assembler)
            yield _event("export", "running", "Exporting to .glb")
            self._final_script = await run_single_part_export(
                code, self._output_filename
            )
            self._final_script, self._param_dict = parameterize_script(
                self._final_script
            )
            yield _event("script", "done", self._final_script)

            try:
                result = await execute_cad_script(self._final_script)
            except Exception as exc:
                result = {"status": "error", "traceback": str(exc)}

            if result["status"] == "error":
                self.state = PipelineState.FAILED
                safe_tb = _safe_err(result["traceback"])
                yield _event("export", "error", safe_tb)
                yield _error_complete(safe_tb, script=self._final_script)
                return

        else:
            # --- Multi-part concurrent path ---
            parts = manifest.parts
            labels = {p.part_id: f"machinist:{p.part_id}" for p in parts}
            for i, part in enumerate(parts, 1):
                yield _event(labels[part.part_id], "running",
                             f"Manufacturing part {i}/{len(parts)}")

            async def _build_part(part):
                code, step_path = await run_critic_loop(part)
                return part.part_id, code, step_path

            # Semaphore: cap concurrent Machinist instances (T4-04 config hook)
            max_concurrent = int(os.environ.get("MIRUM_MAX_MACHINIST", "4"))
            sem = asyncio.Semaphore(max_concurrent)

            async def _guarded_build(part):
                async with sem:
                    return await _build_part(part)

            part_scripts: dict[str, str] = {}
            tasks = [_guarded_build(part) for part in parts]
            for coro in asyncio.as_completed(tasks):
                t0 = time.perf_counter()
                try:
                    part_id, code, step_path = await coro
                    part_scripts[part_id] = code
                    self._step_files[part_id] = step_path
                    self.ctx.history.append(SubagentMessage.ok(
                        trace_id=self.ctx.trace_id, parent_id=None,
                        agent_type="machinist",
                        payload={"part_id": part_id, "step_path": step_path},
                        latency_ms=(time.perf_counter() - t0) * 1000,
                    ))
                    yield _event(labels[part_id], "done",
                                 f"Part '{part_id}' validated")
                except Exception as exc:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    self.ctx.history.append(SubagentMessage.error(
                        trace_id=self.ctx.trace_id, parent_id=None,
                        agent_type="machinist", exc=exc, latency_ms=latency_ms,
                    ))
                    self.state = PipelineState.FAILED
                    msg = _safe_err(exc)
                    yield _event("machinist", "error", msg)
                    yield _error_complete(msg, script=getattr(exc, "script", ""))
                    return

    # ------------------------------------------------------------------
    # Stage: Assembler (multi-part only)
    # ------------------------------------------------------------------

    async def _stage_assemble(self) -> AsyncGenerator[str, None]:
        from agents import run_assembly_critic_loop, ScriptError  # T4-06 hook
        from compiler import execute_cad_script

        yield _event("assembler", "running", "Assembling from pre-compiled .step files")
        t0 = time.perf_counter()
        try:
            self._final_script = await run_assembly_critic_loop(
                self._manifest, self._step_files
            )
            self._final_script = self._final_script.replace(
                "output.glb", self._output_filename
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            self.ctx.history.append(SubagentMessage.error(
                trace_id=self.ctx.trace_id, parent_id=None,
                agent_type="assembler", exc=exc, latency_ms=latency_ms,
            ))
            self.state = PipelineState.FAILED
            msg = _safe_err(exc)
            yield _event("assembler", "error", msg)
            yield _error_complete(msg, script=getattr(exc, "script", ""))
            return

        self.ctx.history.append(SubagentMessage.ok(
            trace_id=self.ctx.trace_id, parent_id=None,
            agent_type="assembler",
            payload={"script_len": len(self._final_script)},
            latency_ms=(time.perf_counter() - t0) * 1000,
        ))
        yield _event("script", "done", self._final_script)

        try:
            result = await execute_cad_script(self._final_script)
        except Exception as exc:
            result = {"status": "error", "traceback": str(exc)}

        if result["status"] == "error":
            self.state = PipelineState.FAILED
            safe_tb = _safe_err(result["traceback"])
            yield _event("assembler", "error", safe_tb)
            yield _error_complete(safe_tb, script=self._final_script)
            return

        yield _event("assembler", "done")

    # ------------------------------------------------------------------
    # Stage: file check + GLB delivery (single-part only; multi-part
    # does this inside _stage_machine already for single, and inside
    # _stage_assemble for multi).
    # ------------------------------------------------------------------

    async def _stage_export(self) -> AsyncGenerator[str, None]:
        if self._is_single_part():
            # single-part: export already ran in _stage_machine; just check
            if os.path.exists(self._output_filename):
                yield _event("export", "done")
            else:
                self.state = PipelineState.FAILED
                msg = "Script succeeded but .glb file was not created."
                yield _event("export", "error", msg)
                yield _error_complete(msg, script=self._final_script)
        else:
            # multi-part: file check after assembler
            if os.path.exists(self._output_filename):
                yield _event("assembler", "done")  # already emitted in _stage_assemble
            else:
                self.state = PipelineState.FAILED
                msg = "Assembler script succeeded but .glb file was not created."
                yield _event("export", "error", msg)
                yield _error_complete(msg, script=self._final_script)
        return
        yield  # make this an async generator even on empty paths

    # ------------------------------------------------------------------
    # Stage: XFOIL aero analysis (optional, never raises)
    # ------------------------------------------------------------------

    async def _stage_aero(self) -> tuple[dict | None, str]:
        """Delegate to _run_aero_stage in main.py.  Returns (polar_dict|None, msg)."""
        try:
            # Import at call time to avoid circular import (orchestrator ↔ main)
            from main import _run_aero_stage  # noqa: PLC0415
            t0 = time.perf_counter()
            polar_dict, msg = await _run_aero_stage(
                self._step_files, self._requirements
            )
            latency_ms = (time.perf_counter() - t0) * 1000
            status = "ok" if polar_dict is not None else "skipped"
            self.ctx.history.append(SubagentMessage(
                trace_id=self.ctx.trace_id,
                parent_id=None,
                agent_type="aero",
                payload=polar_dict or {},
                metadata={
                    "status": status,
                    "skip_reason": msg if polar_dict is None else "",
                    "model": "none",
                    "latency_ms": latency_ms,
                },
            ))
            return polar_dict, msg
        except Exception as exc:
            return None, f"aero stage error: {type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_single_part(self) -> bool:
        if self._manifest is None:
            return True
        from agents import is_single_part  # noqa: PLC0415
        return is_single_part(self._manifest)

    def _complete_event(self, aero_report: dict | None = None) -> str:
        import base64  # noqa: PLC0415
        try:
            glb_bytes = open(self._output_filename, "rb").read()
            glb_b64 = base64.b64encode(glb_bytes).decode()
        except Exception:
            glb_b64 = ""

        from exporters import get_available_formats  # noqa: PLC0415
        from postprocessing import has_tolerance_callouts  # noqa: PLC0415

        complete_payload: dict = {
            "step": "complete",
            "status": "done",
            "glb": glb_b64,
            "script": self._final_script,
            "parameters": self._param_dict,
            "export_formats": get_available_formats(),
            "aero_report": aero_report,
            "pmi_detected": has_tolerance_callouts(self.ctx.prompt),
            "trace_id": self.ctx.trace_id,
        }
        return f"data: {json.dumps(complete_payload)}\n\n"


# ---------------------------------------------------------------------------
# Module-level helpers (mirrors equivalents in main.py)
# ---------------------------------------------------------------------------

def _event(step: str, status: str, detail: str = "") -> str:
    payload: dict = {"step": step, "status": status}
    if detail:
        payload["detail"] = detail
    return f"data: {json.dumps(payload)}\n\n"


def _error_complete(message: str, script: str) -> str:
    return f"data: {json.dumps({'step': 'complete', 'status': 'error', 'message': message, 'script': script})}\n\n"


def _safe_err(exc_or_str) -> str:
    """Thin wrapper — defer to main._safe_error if available, else basic redact."""
    import re  # noqa: PLC0415
    msg = str(exc_or_str)
    msg = re.sub(r'File ".*?"', 'File "<script>"', msg)
    msg = re.sub(r"(/app/|/opt/|[A-Za-z]:(?:/|\\{1,2}))[^\s\"']+", "<internal>", msg)
    return msg
