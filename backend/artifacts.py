"""
Per-run artifact store — the CAD files a user actually takes away.

Until this existed the pipeline produced a STEP file for every part, used it
for verification, and then deleted it: the only thing a user could download
was a `.glb`, a tessellated mesh that SolidWorks, NX and Onshape cannot import
as geometry at all. The B-rep solid the whole product is built to produce
never left the server.

Artifacts live under ``<work_dir>/runs/<run_id>/`` and are served by
``GET /download/{run_id}/{fmt}``. They are deliberately short-lived: this is a
download buffer, not a document management system. Old runs are swept on a
best-effort basis whenever a new run is created.

Security notes
--------------
* ``run_id`` is a uuid4 hex generated server-side and validated against a
  strict pattern before it ever touches the filesystem — it is never taken
  from user input verbatim.
* Every resolved path is re-checked with ``startswith`` against the run root,
  the same defence pattern used by ``/pmi/annotate``.
* Download routes sit behind the same ``X-API-Key`` middleware as the rest of
  the API.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import time
import uuid

logger = logging.getLogger("mirum.artifacts")

_WORK_DIR = pathlib.Path(os.environ.get("MIRUM_WORK_DIR", "/tmp/mirum/work"))
_RUNS_DIR = _WORK_DIR / "runs"

# How long a completed run's files stay downloadable.
_TTL_SECONDS = int(os.environ.get("MIRUM_ARTIFACT_TTL_S", str(6 * 3600)))

# Formats the store knows how to name and serve. `dxf` is intentionally absent:
# export_dxf produces a single planar section at Z=0, not a drawing, and
# offering it as "DXF" would misrepresent what the user gets.
FORMATS: dict[str, tuple[str, str]] = {
    # fmt: (filename, mime type)
    "step": ("model.step", "application/step"),
    "stl":  ("model.stl",  "model/stl"),
    "glb":  ("model.glb",  "model/gltf-binary"),
}

_RUN_ID_RE = re.compile(r"[0-9a-f]{32}")


def new_run_id() -> str:
    """Server-generated identifier for one generation's artifacts."""
    return uuid.uuid4().hex


def is_valid_run_id(run_id: str) -> bool:
    # fullmatch, not match: `$` also matches immediately before a trailing
    # newline, so "a"*32 + "\n" would satisfy a `$`-anchored pattern.
    return bool(_RUN_ID_RE.fullmatch(run_id or ""))


def run_dir(run_id: str, create: bool = False) -> pathlib.Path | None:
    """Resolve a run's directory, or None if the id is malformed/escaping."""
    if not is_valid_run_id(run_id):
        return None
    path = (_RUNS_DIR / run_id).resolve()
    # Defence in depth: the regex already forbids separators and dots, but the
    # containment check is cheap and survives future changes to the pattern.
    if not str(path).startswith(str(_RUNS_DIR.resolve()) + os.sep):
        logger.warning("Rejected run dir outside the runs root: %r", run_id)
        return None
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_path(run_id: str, fmt: str) -> pathlib.Path | None:
    """Absolute path for one artifact, or None if run_id/fmt are invalid."""
    if fmt not in FORMATS:
        return None
    base = run_dir(run_id)
    if base is None:
        return None
    # Re-check containment on the FINAL path, not just the directory. fmt is
    # already restricted to three literal filenames, so this is defence in
    # depth against a symlink planted at that path.
    path = (base / FORMATS[fmt][0]).resolve()
    if not str(path).startswith(str(_RUNS_DIR.resolve()) + os.sep):
        logger.warning("Rejected artifact path outside the runs root: %r", run_id)
        return None
    return path


def available(run_id: str) -> dict[str, bool]:
    """Which formats actually exist on disk for this run.

    The frontend renders a download button per True entry, so this reports
    what is genuinely downloadable rather than what the installation could
    theoretically produce.
    """
    out: dict[str, bool] = {}
    for fmt in FORMATS:
        path = artifact_path(run_id, fmt)
        out[fmt] = bool(path and path.is_file() and path.stat().st_size > 0)
    return out


def sweep(ttl_seconds: int | None = None) -> int:
    """Delete run directories older than the TTL. Returns how many were removed.

    Best-effort: a failure to remove one run never propagates, because this
    runs on the request path and must not turn a successful generation into an
    error.
    """
    ttl = _TTL_SECONDS if ttl_seconds is None else ttl_seconds
    if not _RUNS_DIR.is_dir():
        return 0
    cutoff = time.time() - ttl
    removed = 0
    try:
        entries = list(_RUNS_DIR.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Artifact sweep removed %d expired run(s)", removed)
    return removed


def create_run() -> tuple[str, pathlib.Path]:
    """Start a new run: sweep expired ones, then make this run's directory."""
    sweep()
    run_id = new_run_id()
    path = run_dir(run_id, create=True)
    if path is None:  # unreachable for a uuid4 hex, but asserts vanish under -O
        raise RuntimeError(f"Could not create artifact directory for {run_id!r}")
    return run_id, path


def discard(run_id: str) -> None:
    """Remove a run's directory (used when a generation fails outright)."""
    path = run_dir(run_id)
    if path is not None and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
