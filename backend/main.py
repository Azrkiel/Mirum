"""
FastAPI Orchestration Layer for Text-to-CAD Pipeline.

Exposes the hierarchical subagent pipeline as a streaming SSE endpoint.
Each pipeline step emits a JSON progress event so the frontend can render
a live checklist.
"""

import base64
import hmac
import json
import logging
import os
import pathlib
import re
import time
import uuid
from collections.abc import AsyncGenerator

from contextlib import asynccontextmanager

import google.generativeai as genai
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agents import (
    _remove_quiet,
    ScriptError,
    group_parts_by_description,
    apply_manifest_diff,
    cleanup_tmp_parts,
    extract_kinematic_metadata,
    is_single_part,
    run_assembly_critic_loop,
    run_clarifier,
    run_critic_loop,
    run_planner,
    run_rea,
    run_refiner,
    run_single_part_export,
    validate_dof_ranges,
)
from compiler import execute_cad_script
from debug import log_error as debug_log_error
from debug import log_event as debug_log_event
import artifacts
from exporters import get_available_formats
from postprocessing import parameterize_script, has_tolerance_callouts, extract_pmi_from_prompt, attach_pmi_to_step  # T3-08

logger = logging.getLogger("mirum.api")

# Writable scratch directory for .glb outputs. Must be writable by the
# caduser the backend runs as; /app is read-only, Docker's named
# volumes (/app/tmp_parts, /app/telemetry_logs) are root-owned, so we
# use the /tmp tmpfs. Override via MIRUM_WORK_DIR when a persistent
# location is available (e.g. in local dev on a host filesystem).
_WORK_DIR = pathlib.Path(os.environ.get("MIRUM_WORK_DIR", "/tmp/mirum/work"))
_WORK_DIR.mkdir(parents=True, exist_ok=True)

_GEMINI_KEY_HEAVY = os.environ.get("GEMINI_API_KEY_HEAVY")
if not _GEMINI_KEY_HEAVY:
    raise RuntimeError(
        "GEMINI_API_KEY_HEAVY must be set. The orchestrator uses this key for "
        "Machinist (gemini-3.5-flash), Critic, Refiner, and Lite-tier calls. "
        "Set GEMINI_API_KEY_PRO separately if your Pro key differs from Heavy."
    )

# Pro key is optional — falls back to the Heavy key if not set.
# Used by Planner and REA (gemini-3.1-pro-preview). If your Google Cloud
# project exposes all model tiers under one API key, leave PRO unset.
_GEMINI_KEY_PRO = os.environ.get("GEMINI_API_KEY_PRO") or _GEMINI_KEY_HEAVY

# Configure the global genai client. We use the Pro key when it differs so
# that the heavier Pro quota bucket is charged for Planner/REA calls.
# The Heavy key is still active for Machinist/Critic/Refiner via the same
# global client — both keys must belong to the same Google Cloud project.
genai.configure(api_key=_GEMINI_KEY_PRO)


def _safe_error(detail: str | Exception) -> str:
    """Sanitize an error message before returning it to clients.

    Redacts:
    - Source filenames in tracebacks (`File "..."`)
    - Absolute paths that leak the host filesystem layout. Covers POSIX
      (/app/, /opt/), Windows forward-slash (C:/), repr-escaped Windows
      (C:\\\\) that come from str(PermissionError), AND plain single-
      backslash Windows paths (C:\\) that come from Python launcher
      stderr like `python.exe: can't open file 'C:\\path\\x.py'`.
    """
    msg = str(detail)
    msg = re.sub(r'File ".*?"', 'File "<script>"', msg)
    # Match /app/, /opt/, C:/, C:\ (single bs), or C:\\ (repr'd double bs),
    # then greedily consume non-whitespace/non-quote chars.
    msg = re.sub(
        r"(/app/|/opt/|[A-Za-z]:(?:/|\\{1,2}))[^\s\"']+",
        "<internal>",
        msg,
    )
    return msg

# ---------------------------------------------------------------------------
# Endpoint authentication
# ---------------------------------------------------------------------------
#
# Mirum executes LLM-generated Python. Every generation endpoint is therefore
# a code-execution surface and MUST NOT be reachable by anonymous callers on a
# hosted deployment. MIRUM_API_KEY is a shared secret between the Streamlit
# frontend and this backend.
#
# When MIRUM_API_KEY is unset the check is disabled so `docker compose up`
# still works out of the box for local development. A startup warning is
# emitted so an unset key in production is loud rather than silent. This is
# defence in depth: the deployed topology also keeps port 8000 off the host
# entirely (see docker-compose.yml), so the backend is only reachable from
# the frontend container on the internal Docker network.
_API_KEY = os.environ.get("MIRUM_API_KEY", "")

# Paths reachable without a key. Deliberately minimal — /docs and
# /openapi.json are NOT exempt, since they enumerate the attack surface.
_AUTH_EXEMPT_PATHS = frozenset({"/health"})

# /run takes an arbitrary CadQuery script straight into the sandbox. It is a
# development affordance, not a product feature, so it is opt-in per
# deployment and returns 404 (not 403) when disabled — a disabled endpoint
# should not advertise its own existence.
# Default ON when authentication is configured, OFF when it is not.
#
# /run executes a caller-supplied CadQuery script in the sandbox. It is also
# what the UI's "Run" and "Apply Parameters" buttons call, so hard-disabling it
# by default broke parameter editing in every shipped deployment — the buttons
# returned 404 and surfaced as "Script returned no geometry".
#
# With MIRUM_API_KEY set, a caller reaching /run has already passed the API-key
# middleware and (in the compose topology) Caddy basic auth, and the backend is
# not internet-routable. Such a caller can already drive the same sandbox
# through /generate on every request, so refusing their own script buys
# nothing. Without an API key the service is unauthenticated, and the endpoint
# stays off. MIRUM_ENABLE_RUN_ENDPOINT overrides either way.
_RUN_ENDPOINT_ENV = os.environ.get("MIRUM_ENABLE_RUN_ENDPOINT", "").strip().lower()
if _RUN_ENDPOINT_ENV in ("1", "true", "yes"):
    _ENABLE_RUN_ENDPOINT = True
elif _RUN_ENDPOINT_ENV in ("0", "false", "no"):
    _ENABLE_RUN_ENDPOINT = False
else:
    _ENABLE_RUN_ENDPOINT = bool(_API_KEY)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    cleanup_tmp_parts()
    logger.info("Startup: cleared stale tmp_parts")
    if not _API_KEY:
        logger.warning(
            "MIRUM_API_KEY is not set — endpoint authentication is DISABLED. "
            "This is fine for local development. On any host reachable from "
            "the network, set MIRUM_API_KEY: every generation endpoint "
            "executes LLM-authored code and spends Gemini quota."
        )
    if _ENABLE_RUN_ENDPOINT:
        logger.warning(
            "MIRUM_ENABLE_RUN_ENDPOINT is on — /run accepts arbitrary "
            "CadQuery scripts for sandboxed execution. Keep this off in "
            "production unless you specifically need script editing."
        )
    yield


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="Text-to-CAD API", version="0.1.0", lifespan=_lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:8501,http://frontend:8501"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Reject requests without a valid X-API-Key header.

    Registered last, so it is the OUTERMOST middleware and runs before any
    request handling. No-ops when MIRUM_API_KEY is unset (local dev).
    """
    if not _API_KEY:
        return await call_next(request)
    if request.url.path in _AUTH_EXEMPT_PATHS or request.method == "OPTIONS":
        return await call_next(request)

    provided = request.headers.get("X-API-Key", "")
    # compare_digest keeps the comparison constant-time. Both operands must
    # be str; a missing header yields "" which fails the length check first.
    if not provided or not hmac.compare_digest(provided, _API_KEY):
        logger.warning(
            "Rejected unauthenticated %s %s from %s",
            request.method,
            request.url.path,
            request.client.host if request.client else "-",
        )
        return JSONResponse(
            status_code=401,
            content={"status": "error", "detail": "Invalid or missing API key."},
        )
    return await call_next(request)


@app.middleware("http")
async def audit_log(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    logger.info(
        "%s %s %s %d %.3fs",
        request.client.host if request.client else "-",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
    )
    return response


class GenerateRequest(BaseModel):
    prompt: str = Field(..., max_length=5000)
    run_aero: bool = Field(
        default=False,
        description=(
            "Phase 3 opt-in: when True and the assembly is Domain D (aerospace), "
            "run XFOIL on the multi-part STEP after export and attach a polar "
            "report. Failure is non-blocking — GLB delivery never depends on it."
        ),
    )
    run_struct: bool = Field(
        default=False,
        description=(
            "Phase 4 opt-in: when True and the assembly is Domain D, run the "
            "structural + flight-envelope analysis (ISA atmosphere, FAR-23 "
            "load factors, wing-box bending/shear margins) and attach a "
            "structural report. Pure-number computation — no subprocess, no "
            "STEP file, no LLM call. Failure is non-blocking."
        ),
    )


class RunRequest(BaseModel):
    script: str = Field(..., max_length=50000)


def _sanitize_report_payload(payload: dict | None) -> dict | None:
    """Run every failure string in a CheckReport payload through _safe_error.

    Check failures normally carry only measured geometry data, but error
    paths inside the checkers can interpolate exceptions or absolute paths
    (e.g. "STEP file not found: '/tmp/mirum/parts/...'") — sanitize them the
    same way every other client-visible error string is.
    """
    if payload is None:
        return None
    for check in payload.get("checks", []):
        check["failures"] = [_safe_error(f) for f in check.get("failures", [])]
    return payload


def _event(step: str, status: str, detail: str = "") -> str:
    """Format a server-sent event line."""
    payload = {"step": step, "status": status}
    if detail:
        payload["detail"] = detail
    return f"data: {json.dumps(payload)}\n\n"


def _sanitize_floats(obj: object) -> object:
    """Recursively replace NaN/Inf floats with None so json.dumps never raises.

    XFOIL polar data can contain NaN for unconverged alpha points.  JSON does
    not support NaN, so these must be replaced with null before the dict is
    serialised into the SSE payload.
    """
    import math  # noqa: PLC0415 — stdlib, always available
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


async def _run_aero_stage(
    step_files: dict[str, str],
    requirements,
) -> tuple[dict | None, str]:
    """Phase 3: XFOIL polar after export. Always returns; never raises.

    Returns (polar_dict, status_msg). polar_dict is a PolarReport.model_dump()
    on success, or None when aero is skipped (status_msg explains why).

    Aero failure must never block GLB delivery — every failure path returns
    None with a human-readable reason that the frontend can surface.
    """
    # --- Gate 1: requirements must indicate Domain D ---
    if requirements is None:
        return None, "no REA requirements available"
    domain = (getattr(requirements, "inferred_domain", "") or "").strip().upper()
    if domain != "D":
        return None, f"inferred_domain={domain!r} (not Domain D)"

    # --- Gate 2: airfoil_spec must name a wing ---
    spec = getattr(requirements, "airfoil_spec", None)
    if spec is None or not spec.naca_code or not spec.chord_mm or not spec.span_mm:
        return None, "airfoil_spec missing naca_code / chord_mm / span_mm"

    # --- Gate 3: need a STEP file (multi-part path only in v1) ---
    if not step_files:
        return None, "no STEP file (single-part path not supported in v1)"

    # Pick the wing OML STEP: prefer a part whose id contains 'wing'/'oml'/'skin',
    # else fall back to the first part (planner-ordered: base/foundation first).
    step_path = None
    for pid, path in step_files.items():
        if any(kw in pid.lower() for kw in ("wing", "oml", "skin")):
            step_path = path
            break
    if step_path is None:
        step_path = next(iter(step_files.values()))

    # --- Lazy import: analysis/ lives at repo root, may not be packaged everywhere ---
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _repo_root = _Path(__file__).resolve().parent.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from analysis.airfoil import analyze_wing_step  # noqa: PLC0415
        from schemas import PolarReport  # noqa: PLC0415
    except ImportError as e:
        return None, f"analysis module unavailable ({_safe_error(str(e))})"

    # --- Build FlightEnvelope dict from REA spec; absent fields fall back to defaults ---
    flight_env: dict = {}
    fe = getattr(requirements, "flight_env", None)
    if fe is not None:
        for key in ("alpha_start", "alpha_end", "alpha_step", "reynolds", "mach", "n_crit"):
            val = getattr(fe, key, None)
            if val is not None:
                flight_env[key] = float(val)

    # --- Run XFOIL off the event loop (blocking subprocess) ---
    import asyncio as _asyncio
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
        return None, f"XFOIL failed: {type(e).__name__}: {_safe_error(str(e))}"

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
        return None, f"polar parse failed: {type(e).__name__}: {_safe_error(str(e))}"

    cl_max_str = f"{polar.Cl_max:.3f}" if polar.Cl_max is not None else "n/a"
    sa_str = f"{polar.stall_alpha:.1f}°" if polar.stall_alpha is not None else "n/a"
    msg = f"converged {polar.converged_count} α-points; Cl_max={cl_max_str}; stall α={sa_str}"
    # Sanitize NaN/Inf → null before JSON serialisation (XFOIL can emit NaN
    # for unconverged alpha points; JSON has no NaN literal).
    return _sanitize_floats(polar.model_dump()), msg


async def _run_struct_stage(
    requirements,
) -> tuple[dict | None, str]:
    """Phase 4: structural + flight-envelope analysis. Always returns; never raises.

    Pure-number computation — no subprocess, no STEP file, no LLM call, no
    user-controlled filesystem paths. Returns (struct_dict, status_msg).
    struct_dict is a ``StructuralReport.model_dump()`` on success, or None
    when skipped (status_msg explains why).

    Structural failure must never block GLB delivery — every failure path
    returns None with a human-readable, _safe_error-redacted reason.
    """
    # --- Gate 1: requirements must indicate Domain D ---
    if requirements is None:
        return None, "no REA requirements available"
    domain = (getattr(requirements, "inferred_domain", "") or "").strip().upper()
    if domain != "D":
        return None, f"inferred_domain={domain!r} (not Domain D)"

    # --- Gate 2: aircraft_spec required fields ---
    ac = getattr(requirements, "aircraft_spec", None)
    if ac is None or ac.mtow_kg is None or ac.cruise_alt_m is None or ac.cruise_speed_ms is None:
        return None, "aircraft_spec missing mtow_kg / cruise_alt_m / cruise_speed_ms"

    # --- Gate 3: wing area — from wing_spec, else derive from airfoil_spec ---
    ws = getattr(requirements, "wing_spec", None)
    af = getattr(requirements, "airfoil_spec", None)
    area_m2: float | None = None
    span_m: float | None = None
    mean_chord_m: float | None = None
    if ws is not None:
        area_m2 = ws.area_m2
        span_m = ws.span_m
        mean_chord_m = ws.mean_chord_m
    if area_m2 is None and af is not None and af.chord_mm and af.span_mm:
        chord_m = af.chord_mm / 1000.0
        span_from_af = af.span_mm / 1000.0
        area_m2 = chord_m * span_from_af   # rectangular approximation
        span_m = span_m or span_from_af
        mean_chord_m = mean_chord_m or chord_m
    if not area_m2:
        return None, "wing area unknown (set wing_spec.area_m2 or airfoil_spec.chord_mm + span_mm)"

    # --- Gate 4: wing_box_spec required fields ---
    wb = getattr(requirements, "wing_box_spec", None)
    if wb is None or not (wb.chord_root_m and wb.thickness_to_chord
                          and wb.cap_area_m2 and wb.spar_web_thickness_m):
        return None, (
            "wing_box_spec missing chord_root_m / thickness_to_chord / "
            "cap_area_m2 / spar_web_thickness_m"
        )

    # --- Lazy import: analysis/ lives at repo root, may not be packaged everywhere ---
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _repo_root = _Path(__file__).resolve().parent.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from analysis.structures import analyze_wing_structure  # noqa: PLC0415
        from schemas import StructuralReport  # noqa: PLC0415
    except ImportError as e:
        return None, f"analysis module unavailable ({_safe_error(str(e))})"

    # --- Build inputs for the analysis module ---
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

    # --- Run analysis off the event loop (defensive — analysis is fast & pure-Python) ---
    import asyncio as _asyncio
    try:
        report = await _asyncio.to_thread(
            analyze_wing_structure,
            spec_dict, wing_dict, box_dict,
            material=material,
        )
    except KeyError as e:
        return None, f"unknown material: {_safe_error(str(e))}"
    except Exception as e:
        return None, f"structural analysis failed: {type(e).__name__}: {_safe_error(str(e))}"

    # --- Flatten the nested TypedDict into the flat Pydantic StructuralReport ---
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
        return None, f"struct report mapping failed: {type(e).__name__}: {_safe_error(str(e))}"

    msg = (
        f"n_ult={flat.n_ultimate_pos:.2f}; CL_cruise={flat.cl_cruise:.3f}; "
        f"bending MS={flat.bending_margin_of_safety:.3f}; "
        f"shear MS={flat.shear_margin_of_safety:.3f} "
        f"[sizing-grade, from stated inputs]"
    )

    # Provenance, stated explicitly because the distinction is easy to miss and
    # expensive to get wrong: these margins are computed from spec numbers the
    # REA extracted from the PROMPT — spar-cap area, web thickness, t/c — not
    # measured from the generated solid. Nothing reconciles the two, so a part
    # whose modelled caps are thinner than the stated ones can still show a
    # positive margin. Closed-form beam idealisation, not FEA.
    # Flat scalars only — the report contract is a flat JSON object (see
    # tests/test_struct_stage.py), so provenance is prefixed rather than nested.
    _flat = _sanitize_floats(flat.model_dump())
    if isinstance(_flat, dict):
        _flat["provenance_inputs"] = "stated"
        _flat["provenance_source"] = (
            "REA extraction from the prompt (aircraft_spec / wing_spec / wing_box_spec)"
        )
        _flat["provenance_measured_from_geometry"] = False
        _flat["provenance_method"] = (
            "closed-form beam idealisation (two point-area caps, average web shear)"
        )
        _flat["provenance_grade"] = "sizing-grade estimate — not FEA, not a stress analysis"
    return _flat, msg


async def _pipeline(
    prompt: str, run_aero: bool = False, run_struct: bool = False
) -> AsyncGenerator[str, None]:
    """Run the full pipeline, yielding SSE progress events.

    Thin wrapper around _pipeline_inner whose sole job is guaranteeing that
    the single-part STEP file is deleted. The inner generator has nine early
    returns between creating that file and finishing, and a `finally` here
    also fires on GeneratorExit — i.e. when a client disconnects mid-stream,
    which no amount of return-site cleanup would cover.

    Without this, every failed single-part request leaks a multi-MB STEP into
    /tmp/mirum/parts, which is a 256 MB tmpfs cleared only at startup. Domain D
    is hit hardest: the planner rule forces wings and stabilizers into a single
    part, so aero requests always take this path.
    """
    state: dict[str, str] = {"step_path": "", "run_id": ""}
    try:
        async for event in _pipeline_inner(prompt, run_aero, run_struct, state):
            yield event
    finally:
        _remove_quiet(state["step_path"])
        # A failed generation leaves an empty (or partial) run directory that
        # nothing will ever download. Reclaim it now instead of leaving it for
        # the next request's TTL sweep — an idle deployment would otherwise
        # accumulate them on the 256MB tmpfs.
        run_id = state.get("run_id", "")
        if run_id and not any(artifacts.available(run_id).values()):
            artifacts.discard(run_id)


async def _pipeline_inner(
    prompt: str,
    run_aero: bool,
    run_struct: bool,
    state: dict[str, str],
) -> AsyncGenerator[str, None]:
    """The pipeline proper. `state["step_path"]` is the caller's cleanup hook."""

    # --- Step 1: REA (parallel with Planner) + Planner ---
    yield _event("planner", "running")
    try:
        import asyncio as _asyncio
        # Run REA and Planner concurrently (T2-12)
        rea_task = _asyncio.create_task(run_rea(prompt))
        requirements = await rea_task  # Fire-and-forget; result fed to Planner
        manifest = await run_planner(prompt, requirements=requirements)
    except Exception as e:
        logger.exception("Planner failed")
        debug_log_error("planner", e, {"prompt": prompt[:500]})
        yield _event("planner", "error", _safe_error(e))
        return
    part_names = [p.part_id for p in manifest.parts]
    single = is_single_part(manifest)
    mode = "single-part" if single else f"{len(manifest.parts)}-part assembly"
    yield _event("planner", "done", f"Planned {mode}: {', '.join(part_names)}")

    # --- Step 2: Machinist + Critic Loop ---
    # Absolute path under _WORK_DIR so both the subprocess script (which
    # embeds this string via .replace/.format) and our own os.path.exists
    # / open / os.remove all resolve to the same writable tmpfs location.
    # Relative names like "output.glb" would land in whatever cwd the
    # subprocess happens to have, which is /app (read-only).
    # Per-run artifact directory. The GLB feeds the browser viewer; the STEP
    # and STL written beside it are what the engineer actually takes into
    # SolidWorks / NX / Onshape. Before this the STEP was produced, used for
    # verification, and deleted — the product could not hand over a CAD file.
    run_id, _run_dir = artifacts.create_run()
    state["run_id"] = run_id
    output_filename = str(_run_dir / artifacts.FORMATS["glb"][0])
    step_filename = str(_run_dir / artifacts.FORMATS["step"][0])
    stl_filename = str(_run_dir / artifacts.FORMATS["stl"][0])
    param_dict = {}  # T1-06: Initialize for both paths
    # Phase 3: hoisted so the post-export aero stage can see the per-part
    # STEP files. Populated only in the multi-part path; empty for single-part.
    step_files: dict[str, str] = {}
    # Per-part verification reports (CheckReport) — both paths populate this.
    _part_reports: dict[str, "object"] = {}
    # Assembly-level verification report (multi-part path only).
    _asm_report = None
    # Single-part: the critic loop's verified STEP, reused for the cert and
    # aero stages instead of re-exporting. Held in `state` so the _pipeline
    # wrapper can delete it from a finally: on every exit path.
    _single_step_path = ""

    if single:
        # Single-part fast path: one machinist call, direct export, no assembler.
        # The critic loop already exported and verified a STEP — keep it so the
        # certificate and aero stages can reuse it (two fewer subprocess runs).
        part = manifest.parts[0]
        label = f"machinist:{part.part_id}"
        yield _event(label, "running", "Manufacturing single part")
        try:
            code, _single_step_path, _part_report = await run_critic_loop(
                part, save_step=True, requirements=requirements,
            )
            # Register for cleanup immediately — everything after this point
            # has early-return paths.
            state["step_path"] = _single_step_path
            _part_reports[part.part_id] = _part_report
        except ScriptError as e:
            logger.exception("Machinist failed (single-part)")
            debug_log_error(
                "machinist_single",
                e,
                {"part_id": part.part_id, "script_excerpt": (e.script or "")[:1500]},
            )
            yield _event(
                label, "error",
                _safe_error(getattr(e, "user_message", "") or str(e)),
            )
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": _safe_error(
                    getattr(e, "user_message", "") or str(e)
                ),
                "detail": _safe_error(e),   # operator-grade: class, hints, traceback
                "script": e.script,
            }
            yield f"data: {json.dumps(error_complete)}\n\n"
            return
        except Exception as e:
            logger.exception("Machinist failed (single-part)")
            debug_log_error(
                "machinist_single",
                e,
                {"part_id": part.part_id},
            )
            yield _event(label, "error", _safe_error(e))
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": _safe_error(e),
                "script": getattr(e, "script", ""),
            }
            yield f"data: {json.dumps(error_complete)}\n\n"
            return
        if _part_report.degraded:
            yield _event(
                label, "done",
                f"Part '{part.part_id}' delivered with warnings: "
                + _safe_error("; ".join(_part_report.failure_messages())[:400]),
            )
        else:
            yield _event(label, "done", f"Part '{part.part_id}' validated")

        # Direct export — no assembler needed
        yield _event("export", "running", "Exporting to .glb")
        final_script = await run_single_part_export(
            code, output_filename, step_filename, stl_filename,
        )
        # Parameterize the script for the UI (T1-06)
        final_script, param_dict = parameterize_script(final_script)
        yield _event("script", "done", final_script)
        try:
            result = await execute_cad_script(final_script)
        except Exception as e:
            debug_log_error("export_single", e, {"part_id": part.part_id})
            result = {"status": "error", "traceback": str(e)}
        if result["status"] == "error":
            debug_log_event(
                "export_single",
                "subprocess returned error",
                {
                    "part_id": part.part_id,
                    "output_filename": output_filename,
                    "raw_traceback": result["traceback"][:4000],
                },
            )
            safe_tb = _safe_error(result["traceback"])
            yield _event("export", "error", safe_tb)
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": safe_tb,
                "script": final_script,
            }
            yield f"data: {json.dumps(error_complete)}\n\n"
            return
    else:
        # Multi-part path: concurrent manufacturing + assembler
        import asyncio

        # Launch all machinist jobs concurrently
        parts = manifest.parts
        labels = {p.part_id: f"machinist:{p.part_id}" for p in parts}

        # Emit "running" for all parts
        for i, part in enumerate(parts, 1):
            yield _event(labels[part.part_id], "running", f"Manufacturing part {i}/{len(parts)}")

        # Identical parts are built ONCE. The Planner is told to emit
        # `leg_1`..`leg_4` separately, so without this a four-legged table
        # pays 4x classify + Machinist + STEP export for one geometry. The
        # STEP path is shared across the group: the assembler emits its own
        # importStep + assembly.add per part_id, so one file backs N
        # instances at their own positions.
        groups = group_parts_by_description(parts)
        n_saved = len(parts) - len(groups)
        if n_saved:
            logger.info(
                "Deduplicated %d identical part(s): building %d unique geometries "
                "for %d parts", n_saved, len(groups), len(parts),
            )

        async def _build_group(rep, part_ids):
            code, step_path, report = await run_critic_loop(
                rep, requirements=requirements, dims_part_scoped=True,
            )
            return part_ids, code, step_path, report

        # ensure_future so in-flight siblings can be cancelled when the first
        # part fails — otherwise they keep burning Gemini calls and
        # subprocesses producing output that is guaranteed to be discarded.
        tasks = [asyncio.ensure_future(_build_group(rep, pids)) for rep, pids in groups]

        async def _cancel_siblings():
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        part_scripts: dict[str, str] = {}
        for coro in asyncio.as_completed(tasks):
            try:
                part_ids, code, step_path, report = await coro
                for part_id in part_ids:
                    part_scripts[part_id] = code
                    step_files[part_id] = step_path
                    _part_reports[part_id] = report
                    shared = (
                        "" if len(part_ids) == 1
                        else f" (shared build with {len(part_ids) - 1} identical part(s))"
                    )
                    if report.degraded:
                        yield _event(
                            labels[part_id], "done",
                            f"Part '{part_id}' delivered with warnings: "
                            + _safe_error("; ".join(report.failure_messages())[:400]),
                        )
                    else:
                        yield _event(
                            labels[part_id], "done",
                            f"Part '{part_id}' validated{shared}",
                        )
            except ScriptError as e:
                logger.exception("Machinist failed (multi-part)")
                await _cancel_siblings()
                debug_log_error(
                    "machinist_multi",
                    e,
                    {"script_excerpt": (e.script or "")[:1500]},
                )
                yield _event(
                    "machinist", "error",
                    _safe_error(getattr(e, "user_message", "") or str(e)),
                )
                error_complete = {
                    "step": "complete",
                    "status": "error",
                    "message": _safe_error(
                        getattr(e, "user_message", "") or str(e)
                    ),
                    "detail": _safe_error(e),   # operator-grade
                    "script": e.script,
                }
                yield f"data: {json.dumps(error_complete)}\n\n"
                return
            except Exception as e:
                logger.exception("Machinist failed (multi-part)")
                await _cancel_siblings()
                debug_log_error("machinist_multi", e, None)
                yield _event("machinist", "error", _safe_error(e))
                error_complete = {
                    "step": "complete",
                    "status": "error",
                    "message": _safe_error(e),
                    "script": getattr(e, "script", ""),
                }
                yield f"data: {json.dumps(error_complete)}\n\n"
                return

        # Deterministic assembler (no LLM; one deterministic fallback on
        # solver failure — see run_assembly_critic_loop)
        yield _event("assembler", "running", "Assembling from pre-compiled .step files")
        try:
            final_script, _asm_report = await run_assembly_critic_loop(
                manifest, step_files, requirements=requirements,
            )
            final_script = (
                final_script
                .replace("output.glb", output_filename)
                .replace("output.step", step_filename)
                .replace("output.stl", stl_filename)
            )
        except ScriptError as e:
            logger.exception("Assembler failed")
            debug_log_error(
                "assembler",
                e,
                {"script_excerpt": (e.script or "")[:1500]},
            )
            yield _event("assembler", "error", _safe_error(e))
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": _safe_error(
                    getattr(e, "user_message", "") or str(e)
                ),
                "detail": _safe_error(e),   # operator-grade: class, hints, traceback
                "script": e.script,
            }
            yield f"data: {json.dumps(error_complete)}\n\n"
            return
        except Exception as e:
            logger.exception("Assembler failed")
            debug_log_error("assembler", e, None)
            yield _event("assembler", "error", _safe_error(e))
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": _safe_error(e),
                "script": getattr(e, "script", ""),
            }
            yield f"data: {json.dumps(error_complete)}\n\n"
            return

        yield _event("script", "done", final_script)
        try:
            result = await execute_cad_script(final_script)
        except Exception as e:
            debug_log_error("assembler_exec", e, None)
            result = {"status": "error", "traceback": str(e)}
        if result["status"] == "error":
            debug_log_event(
                "assembler_exec",
                "subprocess returned error",
                {
                    "output_filename": output_filename,
                    "raw_traceback": result["traceback"][:4000],
                },
            )
            safe_tb = _safe_error(result["traceback"])
            yield _event("assembler", "error", safe_tb)
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": safe_tb,
                "script": final_script,
            }
            yield f"data: {json.dumps(error_complete)}\n\n"
            return
        if _asm_report is not None and _asm_report.degraded:
            yield _event(
                "assembler", "done",
                "Assembled with warnings: "
                + _safe_error("; ".join(_asm_report.failure_messages())[:400]),
            )
        else:
            yield _event("assembler", "done")

    # --- Final: Check file and deliver ---
    if not os.path.exists(output_filename):
        # Dump directory contents so we can tell whether the subprocess wrote
        # somewhere unexpected vs didn't write at all. Best-effort only.
        try:
            work_dir_listing = sorted(p.name for p in _WORK_DIR.iterdir())
        except OSError as listing_exc:
            work_dir_listing = [f"<iterdir failed: {listing_exc}>"]
        debug_log_event(
            "deliver",
            "expected .glb not found",
            {
                "output_filename": output_filename,
                "work_dir": str(_WORK_DIR),
                "work_dir_listing": work_dir_listing[:50],
                "cwd": os.getcwd(),
            },
        )
        msg = "Script succeeded but .glb file was not created."
        yield _event("export", "error", msg)
        error_complete = {
            "step": "complete",
            "status": "error",
            "message": msg,
            "script": final_script,
        }
        yield f"data: {json.dumps(error_complete)}\n\n"
        return

    if single:
        yield _event("export", "done")

    with open(output_filename, "rb") as f:
        glb_b64 = base64.b64encode(f.read()).decode("utf-8")
    # NOT deleted: the artifact store owns this file now and serves it (plus
    # the STEP and STL beside it) from /download until the TTL sweep.

    # Get parameters for single-part flow; multi-part doesn't have them (param_dict is undefined)
    param_dict_final = param_dict if single else {}

    # T3-08: Check if prompt has tolerance callouts (inform frontend to offer /pmi/annotate)
    pmi_detected = has_tolerance_callouts(prompt)

    # Phase 3 (XFOIL): optional aerodynamic polar. Gated on the opt-in flag;
    # every failure path inside _run_aero_stage returns (None, reason) so this
    # block can never prevent the "complete" event from being emitted.
    aero_report: dict | None = None
    if run_aero:
        # Single-part path: reuse the critic loop's verified STEP for XFOIL's
        # geometry extraction — no re-export needed. Fall back to an on-the-fly
        # export only if the loop's STEP is unavailable.
        if single and not step_files:
            if _single_step_path and os.path.exists(_single_step_path):
                step_files = {manifest.parts[0].part_id: _single_step_path}
            else:
                _aero_step_path = str(_WORK_DIR / (uuid.uuid4().hex + "_aero.step"))
                _aero_script = (
                    f"{code}\n\n"
                    f"import cadquery as cq\n"
                    f"cq.exporters.export(result, r'{_aero_step_path}')\n"
                )
                _aero_exec = await execute_cad_script(_aero_script)
                if _aero_exec["status"] == "success" and os.path.exists(_aero_step_path):
                    step_files = {manifest.parts[0].part_id: _aero_step_path}
                # If export fails step_files stays empty and _run_aero_stage will
                # return a skip message — the GLB has already been sent so this is
                # non-fatal.

        yield _event("aero", "running", "Running XFOIL polar analysis")
        aero_report, aero_msg = await _run_aero_stage(step_files, requirements)
        # Clean up the temporary STEP file generated for single-part aero.
        if single and "_aero_step_path" in locals():
            try:
                os.remove(_aero_step_path)
            except OSError:
                pass
        if aero_report is not None:
            yield _event("aero", "done", aero_msg)
        else:
            yield _event("aero", "skipped", aero_msg)

    # Phase 4: structural + flight-envelope analysis (opt-in, Domain D only).
    # Pure-number computation — no subprocess, no STEP file, no LLM call. Every
    # failure path inside _run_struct_stage returns (None, reason) so this
    # block can never prevent the "complete" event from being emitted.
    struct_report: dict | None = None
    if run_struct:
        yield _event("struct", "running", "Running structural & envelope analysis")
        struct_report, struct_msg = await _run_struct_stage(requirements)
        if struct_report is not None:
            yield _event("struct", "done", struct_msg)
        else:
            yield _event("struct", "skipped", struct_msg)

    # Geometry certificate — aggregate across all parts. The critic loop
    # already certified every part in-loop (CheckReport.geometry_cert), so no
    # geometry is re-exported or re-measured here.
    geometry_certificate: dict | None = None
    bom_payload: dict | None = None
    try:
        from verification.geometry import _propulsion_fields
        from verification.material import resolve_material, mass_properties as _mass_props

        # Domain F: pass the propulsion_spec through so the cert gains the
        # throat/exit area, geometric expansion ratio, and channel-count fields.
        _prop_spec = getattr(requirements, "propulsion_spec", None) if requirements else None

        _part_certs = {
            pid: rep.geometry_cert for pid, rep in _part_reports.items()
        }

        if single:
            _single_cert = _part_certs.get(manifest.parts[0].part_id)
            if _single_cert is not None:
                geometry_certificate = dict(_single_cert)
                if _prop_spec is not None:
                    geometry_certificate.update(_propulsion_fields(_prop_spec))
        else:
            # Multi-part: merge per-part certs — aggregate volume + surface area,
            # overall passed = all parts passed.
            if _part_certs:
                all_passed = all(
                    c is not None and c.get("passed", False)
                    for c in _part_certs.values()
                )
                total_vol = sum(
                    (c["volume_mm3"] or 0.0)
                    for c in _part_certs.values()
                    if c is not None and c["volume_mm3"] is not None
                )
                total_sa = sum(
                    (c["surface_area_mm2"] or 0.0)
                    for c in _part_certs.values()
                    if c is not None and c["surface_area_mm2"] is not None
                )
                all_failures: list[str] = []
                for pid, c in _part_certs.items():
                    if c:
                        for f in c.get("failures", []):
                            all_failures.append(f"{pid}: {f}")
                geometry_certificate = {
                    "passed": all_passed,
                    "volume_mm3": total_vol if total_vol > 0 else None,
                    "surface_area_mm2": total_sa if total_sa > 0 else None,
                    "bounding_box": None,  # undefined for multi-part aggregate
                    "checks": {},
                    "failures": all_failures,
                    "part_count": len(_part_certs),
                    "per_part": {
                        pid: c for pid, c in _part_certs.items()
                    },
                }
                # Domain F: derive aggregate throat/exit area + expansion ratio
                # from propulsion_spec when available, so the cert card surfaces
                # the same fields for multi-part thrust-chamber assemblies.
                if _prop_spec is not None:
                    geometry_certificate.update(_propulsion_fields(_prop_spec))

        # Augment cert with material mass if we have volume + requirements
        _mat_name = None
        if geometry_certificate and geometry_certificate.get("volume_mm3"):
            _mat_class = getattr(requirements, "material_class", None) if requirements else None
            _mat_name, _density = resolve_material(_mat_class)
            _mass = _mass_props(geometry_certificate["volume_mm3"], _density)
            geometry_certificate["material"] = _mat_name
            geometry_certificate["mass_g"] = _mass["mass_g"]
            geometry_certificate["mass_kg"] = _mass["mass_kg"]
            # Per-part mass, so the BOM can report a real per-line mass rather
            # than only an assembly total.
            for _pid, _c in (_part_certs or {}).items():
                if _c and _c.get("volume_mm3"):
                    _pm = _mass_props(_c["volume_mm3"], _density)
                    _c["material"] = _mat_name
                    _c["mass_g"] = _pm["mass_g"]

        # Bill of materials — the parts list an engineer expects for an
        # assembly. The quantity data already existed (it is what drives
        # duplicate-build dedup) and was being discarded.
        if not single:
            try:
                from verification.bom import build_bom
                bom_payload = build_bom(manifest, _part_reports, _mat_name)
            except Exception as _bom_exc:
                logger.debug("BOM generation skipped: %s", _bom_exc)

    except Exception as _cert_exc:
        logger.debug("geometry certificate generation failed: %s", _cert_exc)
        geometry_certificate = None

    # Verification summary: per-part + assembly CheckReports. `degraded` is
    # True when anything compiled but failed a verification check — the GLB
    # is still delivered, with the failed checks spelled out.
    _degraded = any(r.degraded for r in _part_reports.values()) or (
        _asm_report is not None and _asm_report.degraded
    )
    verification_payload = {
        "degraded": _degraded,
        "parts": {
            pid: _sanitize_report_payload(r.to_payload())
            for pid, r in _part_reports.items()
        },
        "assembly": _sanitize_report_payload(
            _asm_report.to_payload() if _asm_report is not None else None
        ),
    }

    # What the user can actually download, checked against the filesystem
    # rather than against what the installation could theoretically produce.
    _downloads = artifacts.available(run_id)
    if not _downloads.get("step"):
        logger.warning(
            "Run %s produced no STEP — the user gets mesh formats only", run_id,
        )

    complete_payload = {
        "step": "complete",
        "status": "done",
        "run_id": run_id,
        "downloads": _downloads,
        "degraded": _degraded,          # P1: never a silent success
        "verification": verification_payload,
        "glb": glb_b64,
        "script": final_script,
        "parameters": param_dict_final,  # T1-06
        "export_formats": get_available_formats(),  # T2-09
        "pmi_detected": pmi_detected,   # T3-08: hints frontend to offer PMI annotation
        "aero_report": aero_report,     # Phase 3: PolarReport dict or None
        "struct_report": struct_report, # Phase 4: StructuralReport dict or None
        "geometry_certificate": geometry_certificate,  # G-05: post-compile cert
        "bom": bom_payload,             # parts list + CSV (multi-part only)
    }
    yield f"data: {json.dumps(complete_payload)}\n\n"


@app.post("/clarify")
@limiter.limit("15/minute")
async def clarify_prompt(body: GenerateRequest, request: Request):
    """Run the pre-Planner clarifier and return a list of ambiguities.

    Returns {"ambiguities": []} if the prompt is sufficiently detailed.
    This is a fast call — the frontend should not block generation on it.
    """
    ambiguities = await run_clarifier(body.prompt)
    return {"ambiguities": ambiguities}


@app.post("/requirements")
@limiter.limit("15/minute")
async def extract_requirements(body: GenerateRequest, request: Request):
    """T2-12: Run the Requirements Engineering Agent on a prompt.

    Returns a DesignRequirements JSON without running the Planner.
    Useful for previewing extracted specs before planning.
    """
    reqs = await run_rea(body.prompt)
    if reqs is None:
        return {"status": "error", "detail": "REA failed to extract requirements"}
    return {"status": "ok", "requirements": reqs.model_dump()}


@app.post("/plan")
@limiter.limit("10/minute")
async def plan_assembly(body: GenerateRequest, request: Request):
    """Run the Planner agent (with REA) and return the AssemblyManifest as JSON.
    Does NOT run Machinists. Allows the frontend to show the plan before execution.
    """
    import asyncio as _asyncio
    plan_t0 = time.monotonic()
    logger.info("/plan: received prompt (%d chars)", len(body.prompt))
    try:
        rea_t0 = time.monotonic()
        try:
            requirements = await _asyncio.wait_for(run_rea(body.prompt), timeout=45)
            logger.info(
                "/plan: REA finished in %.1fs (requirements=%s)",
                time.monotonic() - rea_t0,
                "present" if requirements is not None else "none",
            )
        except _asyncio.TimeoutError:
            logger.warning(
                "/plan: REA timed out after %.1fs — continuing without requirements",
                time.monotonic() - rea_t0,
            )
            requirements = None

        planner_t0 = time.monotonic()
        try:
            manifest = await _asyncio.wait_for(
                run_planner(body.prompt, requirements=requirements),
                timeout=150,
            )
        except _asyncio.TimeoutError as e:
            elapsed = time.monotonic() - planner_t0
            logger.error("/plan: Planner timed out after %.1fs", elapsed)
            debug_log_error(
                "planner_preview",
                TimeoutError(f"Planner timed out after {elapsed:.1f}s"),
                {"prompt": body.prompt[:500]},
            )
            return {
                "status": "error",
                "detail": f"Planner timed out after {elapsed:.1f}s (Gemini call did not return)",
            }
        logger.info(
            "/plan: Planner finished in %.1fs (%d parts, %d mates) — total %.1fs",
            time.monotonic() - planner_t0,
            len(manifest.parts),
            len(manifest.mating_rules),
            time.monotonic() - plan_t0,
        )
    except Exception as e:
        logger.exception("/plan: failed after %.1fs", time.monotonic() - plan_t0)
        debug_log_error("planner_preview", e, {"prompt": body.prompt[:500]})
        return {"status": "error", "detail": _safe_error(e)}

    dof_violations = validate_dof_ranges(manifest)
    kinematic_meta = extract_kinematic_metadata(manifest)

    return {
        "status": "ok",
        "assembly_name": manifest.assembly_name,
        "parts": [
            {
                "part_id": p.part_id,
                "description": p.description[:200],
                "anchor_tags": p.anchor_tags,
            }
            for p in manifest.parts
        ],
        "mating_rules": [
            {
                "source_part_id": r.source_part_id,
                "source_anchor": r.source_anchor,
                "target_part_id": r.target_part_id,
                "target_anchor": r.target_anchor,
                "mate_type": r.mate_type.value if r.mate_type else None,
                "translation": r.translation,
                "dof_min": r.dof_min,
                "dof_max": r.dof_max,
                "dof_unit": r.dof_unit,
                "coupling_ratio": r.coupling_ratio,
            }
            for r in manifest.mating_rules
        ],
        "part_count": len(manifest.parts),
        "is_single_part": len(manifest.parts) == 1,
        "dof_violations": dof_violations,   # T2-02
        "kinematic": kinematic_meta,        # T2-03
    }


@app.post("/generate")
@limiter.limit("5/minute")
async def generate(body: GenerateRequest, request: Request):
    return StreamingResponse(
        _pipeline(body.prompt, run_aero=body.run_aero, run_struct=body.run_struct),
        media_type="text/event-stream",
    )


@app.get("/download/{run_id}/{fmt}")
@limiter.limit("60/minute")
async def download_artifact(run_id: str, fmt: str, request: Request):
    """Serve a generated CAD file.

    This is the route that makes Mirum a CAD tool rather than a mesh viewer:
    STEP is the format SolidWorks, NX, Onshape and Fusion import as real
    B-rep geometry. GLB is offered too, but only the browser viewer needs it.

    `run_id` is a server-generated uuid4 hex, validated against a strict
    pattern and re-checked for containment before any filesystem access (see
    artifacts.run_dir). Authentication is enforced by the global X-API-Key
    middleware.
    """
    from fastapi import HTTPException  # noqa: PLC0415
    from fastapi.responses import FileResponse  # noqa: PLC0415

    if fmt not in artifacts.FORMATS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown format {fmt!r}. Available: {', '.join(artifacts.FORMATS)}",
        )

    path = artifacts.artifact_path(run_id, fmt)
    if path is None:
        # Malformed id — do not echo it back, and do not distinguish it from
        # a missing file.
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No {fmt.upper()} available for this run. It may have expired, "
                f"or the export did not produce this format."
            ),
        )

    filename, media_type = artifacts.FORMATS[fmt]
    return FileResponse(
        str(path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health")
async def health():
    """Liveness probe. The only endpoint exempt from API-key auth.

    Deliberately returns no version, model, or configuration detail — it is
    reachable by anyone who can route to the container.
    """
    return {"status": "ok"}


@app.post("/run")
@limiter.limit("10/minute")
async def run_script(body: RunRequest, request: Request):
    """Execute a CadQuery script directly and return the GLB model.

    Disabled unless MIRUM_ENABLE_RUN_ENDPOINT is set: this hands a caller
    direct, arbitrary input to the sandbox, which is a development
    affordance rather than something a hosted deployment should expose.
    """
    if not _ENABLE_RUN_ENDPOINT:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(
            status_code=404,
            detail=(
                "Script execution is disabled on this deployment. Set "
                "MIRUM_API_KEY (which enables it by default) or "
                "MIRUM_ENABLE_RUN_ENDPOINT=1 to allow editing and re-running "
                "generated scripts."
            ),
        )

    output_filename = str(_WORK_DIR / (uuid.uuid4().hex + ".glb"))
    script = body.script.replace("output.glb", output_filename)

    result = await execute_cad_script(script)
    if result["status"] == "error":
        debug_log_event(
            "run_script",
            "subprocess returned error",
            {"raw_traceback": result["traceback"][:4000]},
        )
        return {"status": "error", "detail": _safe_error(result["traceback"])}

    if not os.path.exists(output_filename):
        debug_log_event(
            "run_script",
            "expected .glb not found",
            {"output_filename": output_filename},
        )
        return {"status": "error", "detail": "Script ran but no .glb file was created."}

    with open(output_filename, "rb") as f:
        glb_b64 = base64.b64encode(f.read()).decode("utf-8")
    os.remove(output_filename)

    return {"status": "ok", "glb": glb_b64}


@app.get("/stats")
@limiter.limit("30/minute")
async def get_stats(request: Request):
    """Return failure rate statistics stratified by domain and operation type."""
    try:
        from failure_analysis import generate_failure_report
        return generate_failure_report()
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# T2-08: Multi-turn conversational refinement
# ---------------------------------------------------------------------------

class RefineRequest(BaseModel):
    """Request body for /refine."""
    original_manifest: dict = Field(
        ...,
        description="The AssemblyManifest JSON from the prior /generate or /plan run.",
    )
    original_scripts: dict[str, str] = Field(
        default_factory=dict,
        description="Map of part_id -> CadQuery script from the prior run.",
    )
    refinement_prompt: str = Field(
        ...,
        max_length=2000,
        description="What to change about the existing assembly.",
    )
    session_id: str = Field(
        default="",
        description="Optional session identifier for telemetry grouping.",
    )


@app.post("/export/parasolid")
@limiter.limit("5/minute")
async def export_to_parasolid(body: RunRequest, request: Request):
    """T2-09: Export a CadQuery script's output as Parasolid .x_t.

    Executes the script to produce a STEP file, then converts it to
    Parasolid using CAD Exchanger SDK (commercial license required).
    Falls back to STEP if CAD Exchanger is not available.

    Returns: {"format": "parasolid"|"step", "data_b64": "...", "filename": "..."}
    """
    import tempfile
    from exporters import export_parasolid

    # Run the script to produce output.glb + output.step
    step_path = str(_WORK_DIR / (uuid.uuid4().hex + ".step"))
    # Patch script to also export STEP
    patched = body.script + f"\n\nimport cadquery as cq\ncq.exporters.export(result, r'{step_path}')\n"

    result = await execute_cad_script(patched)
    if result["status"] == "error":
        return {"status": "error", "detail": _safe_error(result["traceback"])}

    if not os.path.exists(step_path):
        return {"status": "error", "detail": "Script ran but no STEP file was produced."}

    xt_path = step_path.replace(".step", ".x_t")
    success, actual_path = export_parasolid(step_path, xt_path, fallback_to_step=True)

    try:
        with open(actual_path, "rb") as f:
            data_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return {"status": "error", "detail": "Export file could not be read."}
    finally:
        for p in [step_path, xt_path, actual_path]:
            try:
                os.remove(p)
            except OSError:
                pass

    fmt = "parasolid" if success else "step"
    ext = ".x_t" if success else ".step"
    return {
        "status": "ok",
        "format": fmt,
        "data_b64": data_b64,
        "filename": f"assembly{ext}",
        "note": "" if success else "CAD Exchanger not available — STEP provided instead.",
    }


@app.post("/refine")
@limiter.limit("10/minute")
async def refine_assembly(body: RefineRequest, request: Request):
    """Multi-turn refinement endpoint.

    Takes an existing AssemblyManifest + CadQuery scripts + a natural-language
    refinement request. Runs the RefinerAgent to compute a ManifestDiff, then:
    1. Applies the diff to produce an updated manifest.
    2. Re-runs the Machinist + Critic Loop ONLY for modified/added parts.
    3. Re-assembles using all scripts (reused + re-generated).
    4. Returns the new .glb, updated script, and updated manifest.

    Refinement telemetry (original_script, refinement_prompt, revised_script)
    is logged for future instruction-following fine-tuning.
    """
    import asyncio

    # Reconstruct manifest from dict
    from schemas import AssemblyManifest, RefinementRequest as RR
    try:
        original_manifest = AssemblyManifest.model_validate(body.original_manifest)
    except Exception as e:
        return {"status": "error", "detail": f"Invalid manifest: {_safe_error(e)}"}

    # Run refiner to get diff
    try:
        diff = await run_refiner(
            original_manifest=original_manifest,
            original_scripts=body.original_scripts,
            refinement_prompt=body.refinement_prompt,
        )
    except Exception as e:
        logger.exception("RefinerAgent failed")
        return {"status": "error", "detail": _safe_error(e)}

    # Apply diff to get updated manifest
    try:
        updated_manifest = apply_manifest_diff(original_manifest, diff)
    except Exception as e:
        logger.exception("ManifestDiff apply failed")
        return {"status": "error", "detail": _safe_error(e)}

    # Identify which parts need re-generation
    parts_to_regenerate = set(diff.modified_parts) | {p.part_id for p in diff.added_parts}
    parts_to_skip = {
        p.part_id for p in updated_manifest.parts
        if p.part_id not in parts_to_regenerate
        and p.part_id in body.original_scripts
    }

    # Re-run Machinists only for changed/new parts
    new_scripts: dict[str, str] = dict(body.original_scripts)
    step_files: dict[str, str] = {}
    part_map = {p.part_id: p for p in updated_manifest.parts}

    async def _rebuild(part_id: str):
        part = part_map[part_id]
        code, step_path, _cert = await run_critic_loop(part)
        return part_id, code, step_path

    tasks = [_rebuild(pid) for pid in parts_to_regenerate if pid in part_map]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                return {"status": "error", "detail": _safe_error(res)}
            part_id, code, step_path = res
            new_scripts[part_id] = code
            step_files[part_id] = step_path

    # For reused parts, we need their step files — re-export from existing scripts
    for pid in parts_to_skip:
        if pid in body.original_scripts:
            # Write a temporary .step for the reused part
            try:
                import tempfile, pathlib
                tmp_step = str(_WORK_DIR / f"{pid}_{uuid.uuid4().hex[:8]}.step")
                reuse_script = body.original_scripts[pid]
                export_script = reuse_script.rstrip() + f"\n\nimport cadquery as cq\ncq.exporters.export(result, r'{tmp_step}')\n"
                result_exec = await execute_cad_script(export_script)
                if result_exec["status"] == "success":
                    step_files[pid] = tmp_step
            except Exception:
                pass  # If re-export fails, assembler will report missing parts

    # Assemble
    output_filename = str(_WORK_DIR / (uuid.uuid4().hex + ".glb"))
    try:
        asm_script, asm_report = await run_assembly_critic_loop(updated_manifest, step_files)
        asm_script = asm_script.replace("output.glb", output_filename)
        exec_result = await execute_cad_script(asm_script)
    except Exception as e:
        return {"status": "error", "detail": _safe_error(e)}

    if exec_result["status"] == "error":
        return {"status": "error", "detail": _safe_error(exec_result["traceback"])}

    if not os.path.exists(output_filename):
        return {"status": "error", "detail": "Assembly succeeded but no .glb was produced."}

    with open(output_filename, "rb") as f:
        glb_b64 = base64.b64encode(f.read()).decode("utf-8")
    os.remove(output_filename)

    # Log refinement triple for future fine-tuning
    try:
        from telemetry import log_attempt
        for pid in parts_to_regenerate:
            if pid in new_scripts and pid in body.original_scripts:
                log_attempt(
                    part_id=f"refinement:{pid}",
                    domain="refine",
                    attempt=0,
                    code=new_scripts[pid],
                    error=None,
                    success=True,
                )
    except Exception:
        pass

    return {
        "status": "ok",
        "degraded": asm_report.degraded,
        "verification": _sanitize_report_payload(asm_report.to_payload()),
        "glb": glb_b64,
        "updated_manifest": updated_manifest.model_dump(),
        "updated_scripts": new_scripts,
        "regenerated_parts": list(parts_to_regenerate),
        "reused_parts": list(parts_to_skip),
        "diff_summary": {
            "modified": diff.modified_parts,
            "added": [p.part_id for p in diff.added_parts],
            "removed": diff.removed_parts,
            "mates_updated": len(diff.modified_mates),
        },
    }


# ---------------------------------------------------------------------------
# T3-08: PMI extraction and STEP annotation endpoint
# ---------------------------------------------------------------------------

class PMIRequest(BaseModel):
    """Request body for /pmi/extract and /pmi/annotate endpoints."""
    prompt: str
    part_id: str
    script: str = ""
    step_path: str = ""        # Server-side path; only valid within /tmp/mirum/work/


@app.post("/pmi/extract")
@limiter.limit("30/minute")
async def extract_pmi(body: PMIRequest, request: Request):
    """T3-08: Extract PMI annotations from a design prompt.

    Uses regex-based extraction (fast) with optional LLM refinement.
    Returns the extracted PMIAnnotation list in JSON format.

    Use before /pmi/annotate to preview what PMI will be attached.
    """
    # Fast regex extraction
    pmi = extract_pmi_from_prompt(body.prompt, body.part_id)
    if pmi is None:
        return {
            "status": "ok",
            "has_pmi": False,
            "message": "No tolerance callouts detected in prompt.",
            "annotations": [],
        }

    return {
        "status": "ok",
        "has_pmi": True,
        "part_id": pmi.part_id,
        "general_tolerance": pmi.general_tolerance,
        "material_spec": pmi.material_spec,
        "annotations": [
            {
                "face_selector": a.face_selector,
                "tolerance_type": a.tolerance_type,
                "value": a.value,
                "reference_dimension": a.reference_dimension,
                "datum_labels": a.datum_labels,
            }
            for a in pmi.annotations
        ],
    }


# ---------------------------------------------------------------------------
# T2-16: Aerodynamic polar plot generation
# ---------------------------------------------------------------------------

class PlotsRequest(BaseModel):
    """Request body for /analysis/plots (T2-16)."""
    naca_code: str = Field(
        ...,
        description="4-digit NACA code, e.g. '0012'.",
        min_length=4, max_length=6,
    )
    polar: dict = Field(
        ...,
        description="PolarReport.model_dump() dict from the /generate complete event.",
    )
    design_alpha: float = Field(
        default=4.0,
        description="Angle of attack (degrees) at which to render the Cp distribution.",
        ge=-20.0, le=30.0,
    )
    reynolds: float = Field(default=1_000_000.0, gt=0)
    mach: float = Field(default=0.0, ge=0.0, le=1.0)
    n_crit: float = Field(default=9.0, ge=0.0, le=20.0)
    session_id: str = Field(
        default="",
        description="Optional session identifier for telemetry grouping.",
        max_length=64,
    )


@app.post("/analysis/plots")
@limiter.limit("10/minute")
async def generate_analysis_plots(body: PlotsRequest, request: Request):
    """T2-16: Generate aerodynamic polar plots as base64 PNGs.

    Generates three server-side matplotlib charts:
      - cl_alpha : Cl vs α with stall_alpha marker and Cl_max annotation
      - cd_alpha : Cd vs α on a log-scale Y-axis
      - cp_dist  : Cp distribution at *design_alpha* (upper/lower surfaces,
                   filled between).  Requires XFOIL; null on failure.

    The Cl and Cd charts are pure Python (fast, no subprocess).
    The Cp chart spawns XFOIL — it returns null rather than an error if
    XFOIL is unavailable, so the endpoint never blocks the frontend.

    Returns
    -------
    JSON with keys: status, cl_alpha, cd_alpha, cp_dist
    cl_alpha and cd_alpha are always populated on success.
    cp_dist may be null if XFOIL is not available.
    """
    import asyncio as _asyncio
    import sys
    from pathlib import Path as _Path

    # Ensure analysis/ is importable (repo root is parent of backend/)
    _repo_root = _Path(__file__).resolve().parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    try:
        from analysis.plots import plot_cl_alpha, plot_cd_alpha, plot_cp_dist  # noqa: PLC0415
    except ImportError as imp_err:
        logger.error("/analysis/plots: import failed: %s", imp_err)
        return {"status": "error", "detail": f"plots module unavailable: {imp_err}"}

    polar = body.polar

    # Cl and Cd charts: pure matplotlib, run in thread pool to stay async-safe
    try:
        cl_b64 = await _asyncio.to_thread(plot_cl_alpha, polar)
        cd_b64 = await _asyncio.to_thread(plot_cd_alpha, polar)
    except Exception as chart_err:
        logger.exception("/analysis/plots: chart generation failed")
        return {"status": "error", "detail": _safe_error(chart_err)}

    # Cp distribution: XFOIL subprocess — best-effort, never blocks the response
    cp_b64: str | None = None
    try:
        cp_b64 = await _asyncio.to_thread(
            plot_cp_dist,
            body.naca_code,
            body.design_alpha,
            body.reynolds,
            body.mach,
            body.n_crit,
        )
    except Exception as cp_err:
        logger.warning("/analysis/plots: Cp plot failed: %s", cp_err)
        # cp_b64 stays None — frontend shows placeholder

    return {
        "status": "ok",
        "cl_alpha": cl_b64,
        "cd_alpha": cd_b64,
        "cp_dist": cp_b64,
    }


@app.post("/analysis/pdf")
@limiter.limit("5/minute")
async def generate_xfoil_pdf_report(body: PlotsRequest, request: Request):
    """PDF-03: Generate and stream an XFOIL aerodynamic report as a PDF.

    Accepts the same request body as /analysis/plots.  Generates all three
    matplotlib charts server-side, then calls analysis/pdf_report.py to
    assemble a multi-page branded PDF and streams the raw bytes back to the
    client with Content-Disposition: attachment.

    Rate-limited to 5/min (heavier than /analysis/plots) because PDF
    generation invokes matplotlib twice plus an optional XFOIL Cp run.

    Returns
    -------
    application/pdf stream with filename mirum_naca{code}_re{re:.0e}.pdf
    """
    import asyncio as _asyncio
    import sys
    from fastapi.responses import Response as _Response
    from pathlib import Path as _Path

    # Ensure analysis/ is importable (repo root is parent of backend/)
    _repo_root = _Path(__file__).resolve().parent.parent
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    try:
        from analysis.plots import plot_cl_alpha, plot_cd_alpha, plot_cp_dist  # noqa: PLC0415
        from analysis.pdf_report import generate_xfoil_pdf  # noqa: PLC0415
    except ImportError as imp_err:
        logger.error("/analysis/pdf: import failed: %s", imp_err)
        return {"status": "error", "detail": f"pdf module unavailable: {imp_err}"}

    polar = body.polar

    # Generate Cl and Cd charts in a thread (blocking matplotlib)
    try:
        cl_b64 = await _asyncio.to_thread(plot_cl_alpha, polar)
        cd_b64 = await _asyncio.to_thread(plot_cd_alpha, polar)
    except Exception as chart_err:
        logger.exception("/analysis/pdf: chart generation failed")
        return {"status": "error", "detail": _safe_error(chart_err)}

    # Cp distribution — best-effort, XFOIL may be absent
    cp_b64: str | None = None
    try:
        cp_b64 = await _asyncio.to_thread(
            plot_cp_dist,
            body.naca_code,
            body.design_alpha,
            body.reynolds,
            body.mach,
            body.n_crit,
        )
    except Exception as cp_err:
        logger.warning("/analysis/pdf: Cp plot failed (skipped): %s", cp_err)

    # Assemble PDF in a thread (fpdf2 is CPU-bound)
    try:
        pdf_bytes = await _asyncio.to_thread(
            generate_xfoil_pdf, polar, cl_b64, cd_b64, cp_b64
        )
    except Exception as pdf_err:
        logger.exception("/analysis/pdf: PDF generation failed")
        return {"status": "error", "detail": _safe_error(pdf_err)}

    naca = body.naca_code
    re = body.reynolds
    filename = f"mirum_naca{naca}_re{re:.0e}.pdf"

    return _Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/pmi/annotate")
@limiter.limit("10/minute")
async def annotate_step_with_pmi(body: PMIRequest, request: Request):
    """T3-08: Attach PMI annotations to an existing STEP file.

    Takes a STEP file path (must be in /tmp/mirum/work/) and a design prompt,
    extracts PMI from the prompt, attaches it to the STEP file using XCAF,
    and returns the annotated STEP file as base64.

    Falls back to writing a .pmi.json sidecar if XCAF is unavailable.
    """
    if not body.step_path:
        return {"status": "error", "detail": "step_path is required"}

    # Security: only allow paths under _WORK_DIR
    step_resolved = pathlib.Path(body.step_path).resolve()
    if not str(step_resolved).startswith(str(_WORK_DIR.resolve())):
        return {"status": "error", "detail": "step_path must be within the work directory"}

    if not step_resolved.exists():
        return {"status": "error", "detail": f"STEP file not found: {body.step_path}"}

    # Extract PMI
    pmi = extract_pmi_from_prompt(body.prompt, body.part_id)
    if pmi is None:
        return {
            "status": "ok",
            "has_pmi": False,
            "message": "No PMI annotations found in prompt — STEP file unchanged.",
        }

    # Attach PMI to STEP
    output_path = str(_WORK_DIR / (uuid.uuid4().hex + "_pmi.step"))
    success, result_path = attach_pmi_to_step(str(step_resolved), [pmi], output_path)

    if not success:
        return {"status": "error", "detail": result_path}

    try:
        with open(result_path, "rb") as f:
            data_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return {"status": "error", "detail": f"Could not read output file: {e}"}
    finally:
        try:
            os.remove(result_path)
        except OSError:
            pass

    return {
        "status": "ok",
        "has_pmi": True,
        "n_annotations": len(pmi.annotations),
        "data_b64": data_b64,
        "filename": f"{body.part_id}_pmi.step",
        "format": "step_ap242",
        "annotations_summary": [
            {"face": a.face_selector, "type": a.tolerance_type, "value": a.value}
            for a in pmi.annotations
        ],
    }


# ---------------------------------------------------------------------------
# T4-10: Admin — model registry hot-swap
# ---------------------------------------------------------------------------

class ModelConfigRequest(BaseModel):
    """Request body for POST /admin/model-config."""
    agent_type: str = Field(..., description="Subagent type key (e.g. 'planner', 'critic')")
    model_id: str | None = Field(None, description="New Gemini model id, or null to leave unchanged")
    temperature: float | None = Field(None, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int | None = Field(None, ge=1, le=65536, description="Max output tokens")
    lite_fallback: str | None = Field(None, description="Lite fallback model id ('' to clear)")


def _require_admin(request: Request) -> None:
    """Validate ADMIN_API_KEY bearer token.  Raises HTTPException on failure."""
    from fastapi import HTTPException  # noqa: PLC0415
    admin_key = os.environ.get("ADMIN_API_KEY", "")
    if not admin_key:
        raise HTTPException(
            status_code=503,
            detail="Admin API is disabled — set ADMIN_API_KEY env var to enable.",
        )
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token.")
    token = auth_header.removeprefix("Bearer ").strip()
    if token != admin_key:
        raise HTTPException(status_code=403, detail="Invalid admin API key.")


@app.post("/admin/model-config")
@limiter.limit("20/minute")
async def admin_model_config(body: ModelConfigRequest, request: Request):
    """Hot-swap the model configuration for a subagent without restarting.

    Requires ``Authorization: Bearer <ADMIN_API_KEY>`` header.

    Returns the updated ModelConfig for the given agent_type, plus a snapshot
    of the full registry so the caller can verify the change.
    """
    _require_admin(request)

    try:
        from subagents.model_registry import ModelRegistry  # noqa: PLC0415
    except ImportError:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(status_code=503, detail="ModelRegistry not available.")

    registry = ModelRegistry.instance()

    try:
        new_cfg = registry.update(
            body.agent_type,
            model_id=body.model_id,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            lite_fallback=body.lite_fallback,
        )
    except Exception as e:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(status_code=400, detail=f"Registry update failed: {e}")

    return {
        "status": "ok",
        "agent_type": body.agent_type,
        "updated": {
            "model_id": new_cfg.model_id,
            "temperature": new_cfg.temperature,
            "max_tokens": new_cfg.max_tokens,
            "lite_fallback": new_cfg.lite_fallback,
        },
        "registry_snapshot": registry.snapshot(),
    }


@app.get("/admin/model-config")
@limiter.limit("30/minute")
async def admin_model_config_get(request: Request):
    """Return a read-only snapshot of the current ModelRegistry.

    Requires ``Authorization: Bearer <ADMIN_API_KEY>`` header.
    """
    _require_admin(request)

    try:
        from subagents.model_registry import ModelRegistry  # noqa: PLC0415
        return {"status": "ok", "registry": ModelRegistry.instance().snapshot()}
    except ImportError:
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(status_code=503, detail="ModelRegistry not available.")
