"""
Per-run artifact store - the CAD files a user actually takes away.

The pipeline captures one accepted native revision (``verification.acceptance``)
and returns its STEP and GLB bytes. Without this store only the GLB (a mesh
that SolidWorks, NX and Onshape cannot import as geometry) reached the user,
and the accepted STEP was discarded. This module keeps the *accepted bytes* -
never a re-execution of the script - under a per-run directory and serves them
through ``GET /download/{run_id}/{fmt}``.

Artifacts live under ``<work_dir>/runs/<run_id>/`` and are short-lived: this is
a download buffer, not a document store. Old runs are swept best-effort
whenever a new run is created.

Security notes
--------------
* ``run_id`` is a uuid4 hex generated server-side and validated with
  ``fullmatch`` before it ever touches the filesystem.
* Every resolved path (directory AND final file) is re-checked for containment
  under the runs root with ``Path.relative_to`` after ``resolve()``.
* Only the literal file names in ``FORMATS`` are ever read or written; no part
  ids or other client-influenced strings become path components.
* Download routes sit behind the global ``MIRUM_ACCESS_TOKEN`` middleware.
"""

from __future__ import annotations

import logging
import pathlib
import re
import shutil
import time
import uuid

import os

try:  # package import (tests, ``backend.main``) vs. flat import (``uvicorn main:app``)
    from compiler import _WORK_DIR
except ImportError:  # pragma: no cover
    from .compiler import _WORK_DIR  # type: ignore[no-redef]

logger = logging.getLogger("mirum.artifacts")

def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


# Separate root from the sandbox cwd (_WORK_DIR). Must stay under /tmp/mirum:
# the container rootfs is read-only.
_ARTIFACT_ROOT = pathlib.Path(os.environ.get("MIRUM_ARTIFACT_DIR", "/tmp/mirum/artifacts"))
_RUNS_DIR = _ARTIFACT_ROOT / "runs"

# How long a completed run's files stay downloadable, and hard caps on disk use
# (oldest runs are evicted first).
_TTL_SECONDS = _env_int("MIRUM_ARTIFACT_TTL_S", 6 * 3600)
_MAX_RUNS = _env_int("MIRUM_ARTIFACT_MAX_RUNS", 50)
_MAX_TOTAL_BYTES = _env_int("MIRUM_ARTIFACT_MAX_TOTAL_BYTES", 512 * 1024 * 1024)
_MAX_ARTIFACT_BYTES = _env_int("MIRUM_ARTIFACT_MAX_BYTES", 64 * 1024 * 1024)

# Formats the store knows how to name and serve.
#  * STL is intentionally absent: producing it would mean re-meshing outside the
#    captured acceptance revision, and the delivered files must be exactly the
#    accepted ones.
#  * DXF is intentionally absent: export_dxf is a single planar section, not a
#    drawing, and offering it as "DXF" would misrepresent what the user gets.
FORMATS: dict[str, tuple[str, str]] = {
    # fmt: (filename, mime type)
    "step": ("model.step", "application/step"),
    "glb":  ("model.glb",  "model/gltf-binary"),
    "spec": ("spec.json", "application/json"),
    "report": ("report.json", "application/json"),
}

_RUN_ID_RE = re.compile(r"[0-9a-f]{32}")


def new_run_id() -> str:
    """Server-generated identifier for one generation's artifacts."""
    return uuid.uuid4().hex


def is_valid_run_id(run_id: str) -> bool:
    # fullmatch, not match: `$` also matches immediately before a trailing
    # newline, so "a"*32 + "\n" would satisfy a `$`-anchored pattern.
    return isinstance(run_id, str) and bool(_RUN_ID_RE.fullmatch(run_id))


def _contained(path: pathlib.Path, root: pathlib.Path) -> bool:
    """True when ``path`` (already resolved) is strictly inside ``root``."""
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return path != root.resolve()


def run_dir(run_id: str, create: bool = False) -> pathlib.Path | None:
    """Resolve a run's directory, or None if the id is malformed/escaping."""
    if not is_valid_run_id(run_id):
        return None
    path = (_RUNS_DIR / run_id).resolve()
    # Defence in depth: the regex already forbids separators and dots, but the
    # containment check is cheap and survives future changes to the pattern.
    if not _contained(path, _RUNS_DIR):
        logger.warning("Rejected run dir outside the runs root")
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
    # Re-check containment on the FINAL path, not just the directory: defence
    # against a symlink planted at the file name.
    path = (base / FORMATS[fmt][0]).resolve()
    if not _contained(path, _RUNS_DIR):
        logger.warning("Rejected artifact path outside the runs root")
        return None
    return path


def available(run_id: str) -> dict[str, bool]:
    """Which formats actually exist on disk for this run.

    Reports what is genuinely downloadable rather than what the installation
    could theoretically produce.
    """
    out: dict[str, bool] = {}
    for fmt in FORMATS:
        path = artifact_path(run_id, fmt)
        if fmt in {"spec", "report"} and not (path and path.is_file()):
            continue
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
            # Only real directories named like a run id; never follow links.
            if (entry.is_dir() and not entry.is_symlink()
                    and is_valid_run_id(entry.name)
                    and entry.stat().st_mtime < cutoff):
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Artifact sweep removed %d expired run(s)", removed)
    return removed


def _run_size(entry: pathlib.Path) -> int:
    total = 0
    try:
        for f in entry.iterdir():
            try:
                if f.is_file() and not f.is_symlink():
                    total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def enforce_caps(max_runs: int | None = None, max_bytes: int | None = None) -> int:
    """Evict the oldest runs until run count and total bytes are within caps."""
    max_runs = _MAX_RUNS if max_runs is None else max_runs
    max_bytes = _MAX_TOTAL_BYTES if max_bytes is None else max_bytes
    if not _RUNS_DIR.is_dir():
        return 0
    try:
        runs = []
        for e in _RUNS_DIR.iterdir():
            if e.is_dir() and not e.is_symlink() and is_valid_run_id(e.name):
                runs.append((e.stat().st_mtime, e, _run_size(e)))
    except OSError:
        return 0
    runs.sort(key=lambda r: r[0])  # oldest first
    total = sum(r[2] for r in runs)
    removed = 0
    while runs and (len(runs) > max_runs or total > max_bytes):
        _, entry, size = runs.pop(0)
        shutil.rmtree(entry, ignore_errors=True)
        total -= size
        removed += 1
    if removed:
        logger.info("Artifact cap evicted %d run(s)", removed)
    return removed


def create_run() -> tuple[str, pathlib.Path]:
    """Start a new run: sweep expired ones, then make this run's directory."""
    sweep()
    # Leave room for the run about to be created.
    enforce_caps(max_runs=max(_MAX_RUNS - 1, 0))
    run_id = new_run_id()
    path = run_dir(run_id, create=True)
    if path is None:  # unreachable for a uuid4 hex, but asserts vanish under -O
        raise RuntimeError("Could not create artifact directory")
    return run_id, path


def store(run_id: str, fmt: str, data: bytes, *, _stage: pathlib.Path | None = None) -> bool:
    """Write already-accepted bytes for one format. Returns True on success."""
    if not data:
        return False
    if len(data) > _MAX_ARTIFACT_BYTES:
        logger.warning("Refusing to store %s artifact: %d bytes exceeds cap", fmt, len(data))
        return False
    path = artifact_path(run_id, fmt)
    if path is None:
        return False
    if _stage is not None:
        if not _contained(_stage.resolve(), _RUNS_DIR) or not _stage.name.startswith(".publish-"):
            return False
        path = _stage / FORMATS[fmt][0]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True
    except OSError as exc:
        logger.warning("Could not store %s artifact: %s", fmt, exc)
        return False


def publish(accepted: dict) -> tuple[str, dict[str, bool]]:
    """Persist an acceptance delivery (``{"glb": bytes, "step": bytes, ...}``).

    The bytes are the exact ones the structural acceptance gate validated and
    rendered - the same revision the user sees in the viewer. Returns
    ``(run_id, available_formats)``. A failed run directory is discarded.
    """
    run_id, _ = create_run()
    for fmt in FORMATS:
        data = accepted.get(fmt) if isinstance(accepted, dict) else None
        if isinstance(data, (bytes, bytearray)):
            store(run_id, fmt, bytes(data))
    downloads = available(run_id)
    if not any(downloads.values()):
        discard(run_id)
        return "", downloads
    enforce_caps()
    return run_id, downloads


def discard(run_id: str) -> None:
    """Remove a run's directory."""
    path = run_dir(run_id)
    if path is not None and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def publish_required(run_id: str, data: dict[str, bytes]) -> None:
    """Atomically publish all four deterministic artifacts or discard this attempt.

    Staging uses an unaddressable name. Existing generic optional delivery keeps
    its original behavior; only this dedicated transaction requires all files.
    """
    import tempfile
    if set(data) != {'step', 'glb', 'spec', 'report'} or not is_valid_run_id(run_id):
        raise ValueError('Invalid required artifact publication')
    if sum(len(payload) for payload in data.values()) > _MAX_TOTAL_BYTES:
        raise OSError('Required artifact publication exceeds total storage budget')
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix='.publish-', dir=_RUNS_DIR))
    renamed = False
    try:
        for fmt, payload in data.items():
            if not store(run_id, fmt, payload, _stage=stage):
                raise OSError('Required artifact publication failed: ' + fmt)
        for fmt, payload in data.items():
            if (stage / FORMATS[fmt][0]).read_bytes() != payload:
                raise OSError('Required artifact publication byte mismatch')
        destination = run_dir(run_id)
        if destination is None or destination.exists():
            raise OSError('Required artifact revision is already occupied')
        stage.rename(destination)
        renamed = True
        enforce_caps()
        if not all(available(run_id).values()):
            raise OSError('Required artifact publication evicted before delivery')
    except Exception:
        if renamed:
            discard(run_id)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
