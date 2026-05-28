"""
T4-11: StructSubagent — wraps main._run_struct_stage().
=========================================================

Runs the structural + flight-envelope analysis (Domain D only) as a
proper subagent.  Unlike AeroSubagent it needs no STEP file because
the computation is pure numerical — ISA atmosphere, FAR-23 load
factors, wing-box bending/shear margins.  That makes it the cheapest
Domain D analysis stage and safe to run in parallel with the aero
stage from the Orchestrator.

No LLM call — ``model`` returns "" (no-LLM entry in ModelRegistry).

Skip behaviour
--------------
StructSubagent skips silently (returns a skipped SubagentMessage) when:
  * ``ctx.extra["domain"]`` is not "D"
  * ``ctx.extra["requirements"]`` is missing aircraft_spec required
    fields (mtow_kg / cruise_alt_m / cruise_speed_ms)
  * wing area cannot be derived (wing_spec.area_m2 absent and
    airfoil_spec.chord_mm × span_mm not available)
  * wing_box_spec required fields are missing

This mirrors the gate logic in ``main._run_struct_stage()`` exactly,
so the Orchestrator can always call StructSubagent without a domain
check.

Output payload
--------------
On success::

    {
        "struct":     dict,   # StructuralReport.model_dump() with NaN/Inf→null
        "status_msg": str,    # human-readable summary
        "skipped":    False,
    }

On skip::

    {
        "struct":     None,
        "status_msg": str,    # reason for skip
        "skipped":    True,
    }

On error the SubagentMessage has status="error"; the Orchestrator
treats this as a non-fatal skip (structural failure must not block GLB
delivery).
"""

from __future__ import annotations

import time

from subagents.base import SubagentBase
from subagents.messages import AgentType, SubagentContext, SubagentMessage


class StructSubagent(SubagentBase):
    """Subagent wrapping the structural / flight-envelope analysis stage.

    Input ctx.extra keys
    --------------------
    domain : str
        Inferred domain ("A"–"F").  Structural only runs when domain == "D".
    requirements : object | None
        DesignRequirements (Pydantic model instance) or its model_dump()
        dict, or None.  Must expose ``aircraft_spec``, ``wing_box_spec``,
        and either ``wing_spec`` or ``airfoil_spec`` (for area derivation).

    Output payload
    --------------
    See module docstring.
    """

    @property
    def agent_type(self) -> AgentType:
        return "struct"

    @property
    def model(self) -> str:
        return ""  # no LLM

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        # ------------------------------------------------------------------
        # Gather inputs from ctx.extra
        # ------------------------------------------------------------------
        domain: str = (ctx.extra.get("domain") or "").strip().upper()
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
        # Delegate to _run_struct_stage — import lazily to keep module fast
        # ------------------------------------------------------------------
        t0 = time.perf_counter()
        try:
            from main import _run_struct_stage  # noqa: PLC0415
        except ImportError:
            # Fallback: inline the stage using the same logic
            struct_dict, status_msg = await self._run_struct_inline(domain, requirements)
        else:
            # _run_struct_stage reads requirements.inferred_domain internally
            struct_dict, status_msg = await _run_struct_stage(requirements)

        latency_ms = (time.perf_counter() - t0) * 1000

        skipped = struct_dict is None
        return self._ok(ctx, {
            "struct":     struct_dict,
            "status_msg": status_msg,
            "skipped":    skipped,
        }, latency_ms=latency_ms)

    # ------------------------------------------------------------------
    # Inline fallback — mirrors _run_struct_stage() in main.py exactly
    # (used only when StructSubagent is imported outside the FastAPI process)
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_struct_inline(
        domain: str,
        requirements,
    ) -> tuple[dict | None, str]:
        """Inline implementation of the structural stage.

        Mirrors ``main._run_struct_stage()`` verbatim so this subagent can be
        tested and used outside the FastAPI process without importing ``main``.
        """
        if requirements is None:
            return None, "no REA requirements available"
        req_domain = (getattr(requirements, "inferred_domain", "") or "").strip().upper()
        if req_domain != "D":
            return None, f"inferred_domain={req_domain!r} (not Domain D)"

        ac = getattr(requirements, "aircraft_spec", None)
        if ac is None or ac.mtow_kg is None or ac.cruise_alt_m is None or ac.cruise_speed_ms is None:
            return None, "aircraft_spec missing mtow_kg / cruise_alt_m / cruise_speed_ms"

        ws = getattr(requirements, "wing_spec", None)
        af = getattr(requirements, "airfoil_spec", None)
        area_m2 = ws.area_m2 if ws is not None else None
        span_m = ws.span_m if ws is not None else None
        mean_chord_m = ws.mean_chord_m if ws is not None else None
        if area_m2 is None and af is not None and af.chord_mm and af.span_mm:
            chord_m = af.chord_mm / 1000.0
            span_from_af = af.span_mm / 1000.0
            area_m2 = chord_m * span_from_af
            span_m = span_m or span_from_af
            mean_chord_m = mean_chord_m or chord_m
        if not area_m2:
            return None, "wing area unknown (set wing_spec.area_m2 or airfoil_spec.chord_mm + span_mm)"

        wb = getattr(requirements, "wing_box_spec", None)
        if wb is None or not (wb.chord_root_m and wb.thickness_to_chord
                              and wb.cap_area_m2 and wb.spar_web_thickness_m):
            return None, (
                "wing_box_spec missing chord_root_m / thickness_to_chord / "
                "cap_area_m2 / spar_web_thickness_m"
            )

        try:
            import sys as _sys
            from pathlib import Path as _Path
            _repo_root = _Path(__file__).resolve().parent.parent.parent
            if str(_repo_root) not in _sys.path:
                _sys.path.insert(0, str(_repo_root))
            from analysis.structures import analyze_wing_structure  # noqa: PLC0415
            from schemas import StructuralReport  # noqa: PLC0415
        except ImportError as e:
            return None, f"analysis module unavailable ({e})"

        spec_dict: dict = {
            "mtow_kg":         float(ac.mtow_kg),
            "cruise_alt_m":    float(ac.cruise_alt_m),
            "cruise_speed_ms": float(ac.cruise_speed_ms),
        }
        if ac.category:
            spec_dict["category"] = ac.category
        for key in ("cl_max", "lift_curve_slope", "gust_velocity_ms", "vne_factor"):
            val = getattr(ac, key, None)
            if val is not None:
                spec_dict[key] = float(val)

        wing_dict: dict = {"area_m2": float(area_m2)}
        if span_m is not None:
            wing_dict["span_m"] = float(span_m)
        if mean_chord_m is not None:
            wing_dict["mean_chord_m"] = float(mean_chord_m)

        box_dict: dict = {
            "chord_root_m":         float(wb.chord_root_m),
            "thickness_to_chord":   float(wb.thickness_to_chord),
            "cap_area_m2":          float(wb.cap_area_m2),
            "spar_web_thickness_m": float(wb.spar_web_thickness_m),
        }
        if wb.n_spars is not None:
            box_dict["n_spars"] = int(wb.n_spars)

        material = (getattr(requirements, "material_class", None) or "AL2024-T3")

        import asyncio as _asyncio
        try:
            report = await _asyncio.to_thread(
                analyze_wing_structure,
                spec_dict, wing_dict, box_dict,
                material=material,
            )
        except KeyError as e:
            return None, f"unknown material: {e}"
        except Exception as e:
            return None, f"structural analysis failed: {type(e).__name__}: {e}"

        try:
            cruise = report["cruise"]
            env = report["envelope"]
            bending = report["bending"]
            shear = report["shear"]
            flat = StructuralReport(
                altitude_m=cruise["altitude_m"],
                density_kgm3=cruise["density_kgm3"],
                dynamic_pressure_pa=report["dynamic_pressure_pa"],
                cl_cruise=report["cl_cruise"],
                wing_loading_pa=report["wing_loading_pa"],
                category=env["category"],
                n_maneuver_pos=env["n_maneuver_pos"],
                n_maneuver_neg=env["n_maneuver_neg"],
                n_gust_pos=env["n_gust_pos"],
                n_gust_neg=env["n_gust_neg"],
                n_limit_pos=env["n_limit_pos"],
                n_limit_neg=env["n_limit_neg"],
                n_ultimate_pos=env["n_ultimate_pos"],
                n_ultimate_neg=env["n_ultimate_neg"],
                v_stall_ms=env["v_stall_ms"],
                v_corner_ms=env["v_corner_ms"],
                v_ne_ms=env["v_ne_ms"],
                root_bending_moment_nm=report["root_bending_moment_nm"],
                root_shear_force_n=report["root_shear_force_n"],
                bending_stress_pa=bending["stress_pa"],
                bending_allowable_pa=bending["allowable_pa"],
                bending_margin_of_safety=bending["margin_of_safety"],
                bending_passed=bending["passed"],
                shear_stress_pa=shear["stress_pa"],
                shear_allowable_pa=shear["allowable_pa"],
                shear_margin_of_safety=shear["margin_of_safety"],
                shear_passed=shear["passed"],
                material=report["material"],
                passed=report["passed"],
                warnings=report["warnings"],
            )
        except Exception as e:
            return None, f"struct report mapping failed: {type(e).__name__}: {e}"

        msg = (
            f"n_ult={flat.n_ultimate_pos:.2f}; CL_cruise={flat.cl_cruise:.3f}; "
            f"bending MS={flat.bending_margin_of_safety:.3f}; "
            f"shear MS={flat.shear_margin_of_safety:.3f}"
        )

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

        return _sanitize(flat.model_dump()), msg
