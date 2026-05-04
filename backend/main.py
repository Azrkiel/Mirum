"""
FastAPI Orchestration Layer for Text-to-CAD Pipeline.

Exposes the hierarchical subagent pipeline as a streaming SSE endpoint.
Each pipeline step emits a JSON progress event so the frontend can render
a live checklist.
"""

import base64
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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agents import (
    ScriptError,
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
        "GEMINI_API_KEY_HEAVY must be set. The orchestrator drives both "
        "heavy (gemini-3-flash-preview) and lite (gemini-3.1-flash-lite-preview) "
        "models off this single key — quota is metered per (model, key)."
    )
genai.configure(api_key=_GEMINI_KEY_HEAVY)


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

@asynccontextmanager
async def _lifespan(application: FastAPI):
    cleanup_tmp_parts()
    logger.info("Startup: cleared stale tmp_parts")
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


# Server-side request timeout (plan §4 A-10). Cap any handler at 600s so a
# runaway sync pipeline cannot hold a worker forever. SSE / job-event-stream
# routes are exempt: their handlers return a StreamingResponse object
# quickly; the body iterator runs outside the handler-call window and may
# legitimately stream for the full pipeline duration. Hitting this timeout
# is always a bug — log loudly so we notice.
import asyncio as _timeout_asyncio  # noqa: E402 — local alias keeps the
# middleware self-contained without colliding with the ad-hoc `import asyncio
# as _asyncio` pattern used inside _pipeline.
from fastapi.responses import JSONResponse  # noqa: E402

_REQUEST_TIMEOUT_SECONDS = 600.0


def _is_streaming_route(path: str) -> bool:
    """Routes whose handler returns a StreamingResponse and whose body
    iterator may legitimately run for many minutes. Exempt from the
    request-timeout middleware so we don't 504 a healthy SSE stream."""
    return path == "/generate" or (
        path.startswith("/jobs/") and path.endswith("/events")
    )


@app.middleware("http")
async def request_timeout(request: Request, call_next):
    if _is_streaming_route(request.url.path):
        return await call_next(request)
    try:
        return await _timeout_asyncio.wait_for(
            call_next(request), timeout=_REQUEST_TIMEOUT_SECONDS
        )
    except _timeout_asyncio.TimeoutError:
        logger.error(
            "request_timeout: %s %s exceeded %ss",
            request.method, request.url.path, _REQUEST_TIMEOUT_SECONDS,
        )
        return JSONResponse(
            status_code=504,
            content={
                "status": "error",
                "detail": (
                    f"server-side request timeout ({int(_REQUEST_TIMEOUT_SECONDS)}s); "
                    "if you submitted a long generation, use POST /jobs instead "
                    "and poll /jobs/{id}"
                ),
            },
        )


class GenerateRequest(BaseModel):
    prompt: str = Field(..., max_length=5000)


class RunRequest(BaseModel):
    script: str = Field(..., max_length=50000)


def _event(step: str, status: str, detail: str = "") -> str:
    """Format a server-sent event line."""
    payload = {"step": step, "status": status}
    if detail:
        payload["detail"] = detail
    return f"data: {json.dumps(payload)}\n\n"


async def _pipeline(prompt: str) -> AsyncGenerator[str, None]:
    """Run the full pipeline, yielding SSE progress events."""

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
    output_filename = str(_WORK_DIR / (uuid.uuid4().hex + ".glb"))
    param_dict = {}  # T1-06: Initialize for both paths

    if single:
        # Single-part fast path: one machinist call, direct export, no assembler.
        # Single-part never uses the .step file (no deterministic assembler run),
        # so we skip _save_part_step entirely — one fewer subprocess, one fewer
        # AV-scan window.
        part = manifest.parts[0]
        label = f"machinist:{part.part_id}"
        yield _event(label, "running", "Manufacturing single part")
        try:
            code, _ = await run_critic_loop(part, save_step=False)
        except ScriptError as e:
            logger.exception("Machinist failed (single-part)")
            debug_log_error(
                "machinist_single",
                e,
                {"part_id": part.part_id, "script_excerpt": (e.script or "")[:1500]},
            )
            yield _event(label, "error", _safe_error(e))
            error_complete = {
                "step": "complete",
                "status": "error",
                "message": _safe_error(e),
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
        yield _event(label, "done", f"Part '{part.part_id}' validated")

        # Direct export — no assembler needed
        yield _event("export", "running", "Exporting to .glb")
        final_script = await run_single_part_export(code, output_filename)
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

        # Run concurrently with semaphore-based rate limiting
        async def _build_part(part):
            code, step_path = await run_critic_loop(part)
            return part.part_id, code, step_path

        tasks = [_build_part(part) for part in parts]
        part_scripts: dict[str, str] = {}
        step_files: dict[str, str] = {}
        for coro in asyncio.as_completed(tasks):
            try:
                part_id, code, step_path = await coro
                part_scripts[part_id] = code
                step_files[part_id] = step_path
                yield _event(labels[part_id], "done", f"Part '{part_id}' validated")
            except ScriptError as e:
                logger.exception("Machinist failed (multi-part)")
                debug_log_error(
                    "machinist_multi",
                    e,
                    {"script_excerpt": (e.script or "")[:1500]},
                )
                yield _event("machinist", "error", _safe_error(e))
                error_complete = {
                    "step": "complete",
                    "status": "error",
                    "message": _safe_error(e),
                    "script": e.script,
                }
                yield f"data: {json.dumps(error_complete)}\n\n"
                return
            except Exception as e:
                logger.exception("Machinist failed (multi-part)")
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

        # Deterministic assembler (no LLM, no retries)
        yield _event("assembler", "running", "Assembling from pre-compiled .step files")
        try:
            final_script = await run_assembly_critic_loop(manifest, step_files)
            final_script = final_script.replace("output.glb", output_filename)
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
                "message": _safe_error(e),
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
    os.remove(output_filename)

    # Get parameters for single-part flow; multi-part doesn't have them (param_dict is undefined)
    param_dict_final = param_dict if single else {}

    # T3-08: Check if prompt has tolerance callouts (inform frontend to offer /pmi/annotate)
    pmi_detected = has_tolerance_callouts(prompt)

    complete_payload = {
        "step": "complete",
        "status": "done",
        "glb": glb_b64,
        "script": final_script,
        "parameters": param_dict_final,  # T1-06
        "export_formats": get_available_formats(),  # T2-09
        "pmi_detected": pmi_detected,   # T3-08: hints frontend to offer PMI annotation
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
        _pipeline(body.prompt),
        media_type="text/event-stream",
    )


@app.post("/run")
@limiter.limit("10/minute")
async def run_script(body: RunRequest, request: Request):
    """Execute a CadQuery script directly and return the GLB model."""
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
async def get_stats():
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
        code, step_path = await run_critic_loop(part)
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
        asm_script = await run_assembly_critic_loop(updated_manifest, step_files)
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


# ---- Worktree A: non-blocking /jobs route family ----
# Imports go here (rather than at module top) so wiring sees `_pipeline`
# already defined in this module's namespace. The submitter returns
# immediately; a background asyncio task drains the same `_pipeline`
# coroutine the legacy /generate route still uses, so frontend clients
# no longer have to hold a TCP socket open across multi-minute Planner
# / Critic-loop calls. Plan §4 A-02/A-03/A-07.
from jobs import JobRegistry  # noqa: E402
import routes_jobs  # noqa: E402

app.state.job_registry = JobRegistry()
routes_jobs.set_pipeline(_pipeline)
app.include_router(routes_jobs.router)
