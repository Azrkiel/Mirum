"""
T4-08: AeroSubagent — wraps main._run_aero_stage().
=====================================================

Runs XFOIL aerodynamic analysis (Domain D only) as a proper subagent.
Designed to run in parallel with AssemblerSubagent via asyncio.gather()
in the Orchestrator, since the wing STEP file is available before full
GLB assembly completes.

No LLM call — the ``model`` property returns "" (no-LLM entry in
ModelRegistry). Analysis is a deterministic subprocess (XFOIL) with
fixed inputs.

Skip behaviour
--------------
AeroSubagent skips silently (returns a skipped SubagentMessage) when:
  * ``ctx.extra["domain"]`` is not "D"
  * ``ctx.extra["airfoil_spec"]`` is None or missing required fields
  * ``ctx.extra["step_files"]`` is empty

This mirrors the gate logic in ``main._run_aero_stage()`` exactly, so
the Orchestrator can always call AeroSubagent without a domain check.

Output payload
--------------
On success::

    {
        "polar":        dict,   # PolarReport.model_dump() with NaN→null
        "status_msg":   str,    # human-readable summary ("converged N α-points …")
        "skipped":      False,
    }

On skip::

    {
        "polar":        None,
        "status_msg":   str,    # reason for skip
        "skipped":      True,
    }

On error the SubagentMessage has status="error"; the Orchestrator
treats this as a non-fatal skip (aero failure must not block GLB
delivery).
"""

from __future__ import annotations

import time

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class AeroSubagent(SubagentBase):
    """Subagent wrapping the XFOIL aero analysis pipeline.

    Input ctx.extra keys
    --------------------
    domain : str
        Inferred domain ("A"–"E").  Aero only runs when domain == "D".
    step_files : dict[str, str]
        part_id → .step file path.  At least one entry required.
    requirements : object | None
        DesignRequirements (Pydantic model instance) or its model_dump()
        dict, or None.  Must expose ``airfoil_spec`` and optionally
        ``flight_env``.

    Output payload
    --------------
    See module docstring.
    """

    @property
    def agent_type(self) -> AgentType:
        return "aero"

    @property
    def model(self) -> str:
        return ""  # no LLM

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        # ------------------------------------------------------------------
        # Gather inputs from ctx.extra
        # ------------------------------------------------------------------
        domain: str = (ctx.extra.get("domain") or "").strip().upper()
        step_files: dict[str, str] = ctx.extra.get("step_files") or {}
        requirements = ctx.extra.get("requirements")  # DesignRequirements or None

        # If requirements came in as a raw dict (e.g. reconstructed from
        # REA payload), wrap it back into the Pydantic model.
        if isinstance(requirements, dict):
            try:
                from schemas import DesignRequirements  # noqa: PLC0415
                requirements = DesignRequirements.model_validate(requirements)
            except Exception:  # noqa: BLE001
                requirements = None

        # ------------------------------------------------------------------
        # Delegate to _run_aero_stage — import lazily to keep module fast
        # ------------------------------------------------------------------
        t0 = time.perf_counter()
        try:
            from main import _run_aero_stage  # noqa: PLC0415
        except ImportError:
            # Fallback: inline the stage using the same logic
            polar_dict, status_msg = await self._run_aero_inline(
                domain, step_files, requirements
            )
        else:
            # Patch domain onto requirements so the gate check works
            # _run_aero_stage reads requirements.inferred_domain internally
            polar_dict, status_msg = await _run_aero_stage(step_files, requirements)

        latency_ms = (time.perf_counter() - t0) * 1000

        skipped = polar_dict is None
        return self._ok(ctx, {
            "polar":      polar_dict,
            "status_msg": status_msg,
            "skipped":    skipped,
        }, latency_ms=latency_ms)

    # ------------------------------------------------------------------
    # Inline fallback — mirrors _run_aero_stage() in main.py exactly
    # (used only when AeroSubagent is imported outside the FastAPI process)
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_aero_inline(
        domain: str,
        step_files: dict[str, str],
        requirements,
    ) -> tuple[dict | None, str]:
        """Inline implementation of the aero stage.

        Mirrors ``main._run_aero_stage()`` verbatim so that this subagent
        can be tested and used outside the FastAPI process without importing
        ``main``.
        """
        import asyncio as _asyncio  # noqa: PLC0415

        # Gate 1: Domain D only
        if requirements is None:
            return None, "no REA requirements available"
        req_domain = (getattr(requirements, "inferred_domain", "") or "").strip().upper()
        if req_domain != "D":
            return None, f"inferred_domain={req_domain!r} (not Domain D)"

        # Gate 2: airfoil_spec must be populated
        spec = getattr(requirements, "airfoil_spec", None)
        if spec is None or not spec.naca_code or not spec.chord_mm or not spec.span_mm:
            return None, "airfoil_spec missing naca_code / chord_mm / span_mm"

        # Gate 3: need at least one STEP file
        if not step_files:
            return None, "no STEP file (single-part path not supported in v1)"

        # Pick the wing OML STEP
        step_path: str | None = None
        for pid, path in step_files.items():
            if any(kw in pid.lower() for kw in ("wing", "oml", "skin")):
                step_path = path
                break
        if step_path is None:
            step_path = next(iter(step_files.values()))

        # Lazy import analysis module
        try:
            import sys as _sys
            from pathlib import Path as _Path
            _repo_root = _Path(__file__).resolve().parent.parent.parent
            if str(_repo_root) not in _sys.path:
                _sys.path.insert(0, str(_repo_root))
            from analysis.airfoil import analyze_wing_step  # noqa: PLC0415
            from schemas import PolarReport  # noqa: PLC0415
        except ImportError as e:
            return None, f"analysis module unavailable ({e})"

        # Build FlightEnvelope dict
        flight_env: dict = {}
        fe = getattr(requirements, "flight_env", None)
        if fe is not None:
            for key in ("alpha_start", "alpha_end", "alpha_step", "reynolds", "mach", "n_crit"):
                val = getattr(fe, key, None)
                if val is not None:
                    flight_env[key] = float(val)

        # Run XFOIL off the event loop
        try:
            result = await _asyncio.to_thread(
                analyze_wing_step,
                step_path,
                spec.naca_code,
                float(spec.span_mm),
                float(spec.chord_mm),
                flight_env,
            )
        except Exception as e:
            return None, f"XFOIL failed: {type(e).__name__}: {e}"

        try:
            polar = PolarReport(
                naca_code=result["naca_code"],
                chord_mm=result["chord_mm"],
                reynolds=result["reynolds"],
                mach=result["mach"],
                n_crit=result["n_crit"],
                alpha=result["alpha"],
                Cl=result["Cl"],
                Cd=result["Cd"],
                Cm=result["Cm"],
                L_D=result["L_D"],
                converged_count=result["converged_count"],
                stall_alpha=result["stall_alpha"],
                Cl_max=result["Cl_max"],
            )
        except Exception as e:
            return None, f"polar parse failed: {type(e).__name__}: {e}"

        cl_max_str = f"{polar.Cl_max:.3f}" if polar.Cl_max is not None else "n/a"
        sa_str = f"{polar.stall_alpha:.1f}°" if polar.stall_alpha is not None else "n/a"
        msg = f"converged {polar.converged_count} α-points; Cl_max={cl_max_str}; stall α={sa_str}"

        # Sanitize NaN/Inf → null for JSON serialisation
        import math as _math  # noqa: PLC0415

        def _sanitize(obj):
            if isinstance(obj, float) and (not _math.isfinite(obj)):
                return None
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            return obj

        return _sanitize(polar.model_dump()), msg
