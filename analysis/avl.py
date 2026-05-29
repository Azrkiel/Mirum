"""
analysis/avl.py — AVL (Athena Vortex Lattice) wrapper
=====================================================

A subprocess wrapper around Mark Drela & Harold Youngren's AVL, mirroring the
structure of ``analysis/airfoil.py`` (XFOIL wrapper):

  - module-level exceptions ``AvlNotFoundError`` / ``AvlRunError`` (mirror the
    XFOIL ones);
  - ``_resolve_avl_bin(override)`` priority chain (override → MIRUM_AVL_BIN →
    ``bin/avl.exe`` on Windows / ``avl`` on PATH), reusing the ``_abs_if_path``
    idea so a path-like value still execs under ``cwd=<tmpdir>``;
  - ``_build_avl_geometry(...)`` generates a valid ``.avl`` geometry file (a
    SURFACE with N SECTIONs — leading-edge xyz, chord, incidence/twist,
    dihedral via z, optional CONTROL lines) plus a run-case file (alpha,
    density, velocity, reference S/c/b);
  - ``run_avl_analysis(...)`` drives AVL with ``subprocess.run`` (always a
    timeout, never ``shell=True``) and parses the total-forces / stability
    (``.st``) output into an ``AvlResult`` TypedDict;
  - ``analyze_wing_3d(...)`` a thin dispatcher implementing the B4
    division-of-labour contract.

INVISCID-METHOD CAVEAT
----------------------
AVL is an inviscid vortex-lattice solver.  It yields the lift coefficient
``CL_3D``, the induced drag ``CDind`` (== ``CDi`` here), the Trefftz-plane
span/Oswald efficiency ``e``, and the full set of stability & control
derivatives.  It does NOT produce a viscous total drag (``CDvis`` is always
zero unless you supply a viscous polar) and it does NOT predict stall — there
is no boundary layer in the model.  Viscous drag and stall remain XFOIL's job
(``analysis/airfoil.py``).

DIVISION OF LABOUR (B4)
-----------------------
  - ``analysis/lifting_line.py`` = always-available analytic baseline (pure
    Python; needs no binary).
  - ``analysis/avl.py`` = opt-in higher fidelity; when the binary is absent it
    raises ``AvlNotFoundError`` and the caller should fall back to
    ``lifting_line`` — the same graceful-degradation contract XFOIL uses.
  - The two analyses share ``CL_3D`` / ``CDi`` / ``span_efficiency_e``; prefer
    AVL when its binary is present.  ``analyze_wing_3d(..., prefer_avl=True)``
    encodes that preference and performs the fallback automatically.
"""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TypedDict

from analysis.lifting_line import WingPolarPoint, prandtl_lifting_line

logger = logging.getLogger("mirum.analysis.avl")

_AVL_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Exceptions  (mirror XfoilNotFoundError / XfoilRunError)
# ---------------------------------------------------------------------------

class AvlNotFoundError(RuntimeError):
    """Raised when the AVL binary is not on PATH or at the configured path."""


class AvlRunError(RuntimeError):
    """Raised when AVL exits with a failure or produces no parseable forces."""


# ---------------------------------------------------------------------------
# Input TypedDicts
# ---------------------------------------------------------------------------

class WingSection(TypedDict, total=False):
    """One AVL SECTION: leading-edge xyz, chord, local incidence, control.

    Dihedral is expressed implicitly through ``le_z`` rising along the span,
    exactly as AVL expects (it interpolates the surface between consecutive
    SECTION leading-edge points).
    """
    le_x:           float   # leading-edge X [m]
    le_y:           float   # leading-edge Y (spanwise) [m]
    le_z:           float   # leading-edge Z (dihedral via z) [m]
    chord:          float   # local chord [m]
    incidence_deg:  float   # local incidence / twist [deg]
    # Optional control-surface declaration (CONTROL line):
    control_name:   str     # e.g. 'elevator', 'aileron'
    control_gain:   float   # deflection gain (usually 1.0)
    control_xhinge: float   # hinge x/c (0..1), fraction of chord from LE


class WingSpec(TypedDict, total=False):
    """A full wing geometry + reference quantities for one AVL run."""
    name:       str
    sections:   list[WingSection]
    ref_area:   float       # Sref [m²]
    ref_chord:  float       # Cref [m]
    ref_span:   float       # Bref [m]
    mach:       float       # freestream Mach (0 for incompressible)


# ---------------------------------------------------------------------------
# Output TypedDict — overlaps WingPolarPoint on CL_3D / CDi / span_efficiency_e
# ---------------------------------------------------------------------------

class AvlResult(TypedDict):
    """Forces + derivatives from one AVL run.

    Shares ``CL_3D`` / ``CDi`` / ``span_efficiency_e`` with
    ``lifting_line.WingPolarPoint`` so the two are interchangeable on those
    headline quantities.  AVL-only fields carry the stability and control
    derivatives.
    """
    CL_3D:                 float
    CDi:                   float                 # == CDind (inviscid induced)
    span_efficiency_e:     float                 # Trefftz/Oswald efficiency
    Cm:                    float                 # pitching moment Cmtot
    stability_derivatives: dict[str, float]      # CLa, Cma, CLq, ...
    control_derivatives:   dict[str, float]      # CLd1, Cmd1, ... (if CONTROL)
    passed:                bool
    warnings:              list[str]
    forces_path:           str | None            # raw .st file path (if kept)


# ---------------------------------------------------------------------------
# Binary resolution  (mirror _abs_if_path / _resolve_xfoil_bin)
# ---------------------------------------------------------------------------

def _abs_if_path(value: str) -> str:
    """Resolve path-like values to absolute; leave bare command names alone.

    A value containing a path separator (e.g. ``analysis/tests/mock_avl``) is
    resolved against the cwd so it is still found when AVL runs with
    ``cwd=<tmpdir>``.  A bare command name (e.g. ``avl``) is returned untouched
    so PATH lookup still works.
    """
    if os.sep in value or (os.altsep and os.altsep in value):
        return str(Path(value).resolve())
    return value


def _resolve_avl_bin(override: str | None) -> str:
    """Return the AVL binary path, checking sources in priority order.

    Priority: override arg → ``MIRUM_AVL_BIN`` env var → repo-local
    ``bin/avl.exe`` on Windows → ``avl`` on PATH (Linux/macOS).  Path-like
    values are made absolute (see ``_abs_if_path``); bare command names are
    left untouched for PATH lookup.
    """
    if override:
        return _abs_if_path(override)

    env_val = os.environ.get("MIRUM_AVL_BIN", "")
    if env_val:
        return _abs_if_path(env_val)

    if os.name == "nt":
        # analysis/avl.py → <repo>/bin/avl.exe
        return str(Path(__file__).resolve().parent.parent / "bin" / "avl.exe")

    return "avl"


def _as_argv(bin_path: str) -> list[str]:
    """Turn a resolved binary path into a subprocess argv list.

    A real AVL executable (``avl`` / ``avl.exe``) is run directly.  A Python
    shim ending in ``.py`` (used by the ``mock_avl`` test double on platforms
    such as Windows where a shebang script cannot be exec'd directly) is run
    via the current interpreter so the same code path is exercised end-to-end
    regardless of OS.
    """
    import sys
    if bin_path.lower().endswith(".py"):
        return [sys.executable, bin_path]
    return [bin_path]


# ---------------------------------------------------------------------------
# B3 — .avl geometry + run-case generation
# ---------------------------------------------------------------------------

def _build_avl_geometry(
    spec: WingSpec,
    alpha_deg: float,
    velocity_ms: float,
    density_kgm3: float,
) -> tuple[str, str]:
    """Build the AVL ``.avl`` geometry text and the ``.run`` run-case text.

    Returns
    -------
    (avl_text, run_text) : tuple[str, str]
        ``avl_text`` is a complete AVL geometry input (header + one SURFACE with
        N SECTIONs); ``run_text`` is a ``.run`` run-case file naming one case at
        the requested alpha with the freestream density/velocity.

    Raises
    ------
    AvlRunError
        If the spec has fewer than two sections (a surface needs ≥2 to define a
        panel) or non-positive reference quantities.
    """
    sections = spec.get("sections", [])
    if len(sections) < 2:
        raise AvlRunError(
            f"AVL surface needs at least 2 sections, got {len(sections)}"
        )

    sref = spec.get("ref_area", 0.0)
    cref = spec.get("ref_chord", 0.0)
    bref = spec.get("ref_span", 0.0)
    if sref <= 0 or cref <= 0 or bref <= 0:
        raise AvlRunError(
            f"reference quantities must be positive (Sref={sref}, "
            f"Cref={cref}, Bref={bref})"
        )

    mach = spec.get("mach", 0.0)
    name = spec.get("name", "wing")

    # ── Header ───────────────────────────────────────────────────────────────
    lines: list[str] = []
    lines.append(name)
    lines.append(f"{mach:.4f}                       | Mach")
    # iYsym iZsym Zsym — symmetric about the XZ plane (mirror in y).
    lines.append("0     0     0.0             | iYsym  iZsym  Zsym")
    lines.append(f"{sref:.4f}  {cref:.4f}  {bref:.4f}   | Sref   Cref   Bref")
    lines.append("0.0   0.0   0.0             | Xref   Yref   Zref")
    lines.append("0.00                        | CDp (profile drag; 0 = inviscid)")
    lines.append("")

    # ── One lifting surface ───────────────────────────────────────────────────
    lines.append("SURFACE")
    lines.append(name)
    # Nchord Cspace  Nspan Sspace — chordwise/spanwise vortex distribution.
    lines.append("10   1.0   20   1.0          | Nchord Cspace Nspan Sspace")
    lines.append("YDUPLICATE")
    lines.append("0.0")
    lines.append("")

    # ── Sections ───────────────────────────────────────────────────────────────
    for sec in sections:
        le_x = sec.get("le_x", 0.0)
        le_y = sec.get("le_y", 0.0)
        le_z = sec.get("le_z", 0.0)
        chord = sec.get("chord", 0.0)
        ainc = sec.get("incidence_deg", 0.0)
        lines.append("SECTION")
        # Xle Yle Zle Chord Ainc Nspan Sspace
        lines.append(
            f"{le_x:.4f}  {le_y:.4f}  {le_z:.4f}  {chord:.4f}  {ainc:.4f}  0  0"
        )
        # Flat-plate sections (NACA 0000 camberline); callers can override the
        # airfoil later.  AVL is happy with a bare camber-less section.
        ctrl_name = sec.get("control_name")
        if ctrl_name:
            gain = sec.get("control_gain", 1.0)
            xhinge = sec.get("control_xhinge", 0.75)
            # CONTROL  name gain Xhinge  XYZhvec  SgnDup
            lines.append(
                f"CONTROL\n{ctrl_name}  {gain:.4f}  {xhinge:.4f}  "
                f"0.0 0.0 0.0   1.0"
            )
        lines.append("")

    avl_text = "\n".join(lines) + "\n"

    # ── Run-case file ──────────────────────────────────────────────────────────
    run_lines: list[str] = []
    run_lines.append("---------------------------------------------")
    run_lines.append("Run case  1:  mirum-case")
    run_lines.append("")
    run_lines.append(f"alpha        ->  alpha       =   {alpha_deg:.4f}")
    run_lines.append("beta         ->  beta        =   0.0")
    run_lines.append("pb/2V        ->  pb/2V       =   0.0")
    run_lines.append("qc/2V        ->  qc/2V       =   0.0")
    run_lines.append("rb/2V        ->  rb/2V       =   0.0")
    run_lines.append("")
    run_lines.append(f"density   =   {density_kgm3:.4f}")
    run_lines.append(f"velocity  =   {velocity_ms:.4f}")
    run_lines.append("grav.acc. =   9.81")
    run_lines.append("")
    run_text = "\n".join(run_lines) + "\n"

    return avl_text, run_text


def _build_avl_command_script(
    avl_path: str,
    run_path: str,
    st_path: str,
) -> str:
    """Build the AVL stdin command stream.

    LOAD geometry → CASE run-file → OPER → execute (x) → write stability
    forces (ST <file>) → exit.  The ``ST`` line is what the parser keys on.
    """
    lines = [
        f"LOAD {avl_path}",
        f"CASE {run_path}",
        "OPER",
        "X",                 # execute the run case
        f"ST {st_path}",     # write stability-axis total forces + derivatives
        "",                  # exit OPER
        "QUIT",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# B3 — forces / stability parser
# ---------------------------------------------------------------------------

# Match e.g. "CLtot =   0.45120" or "CLa =   5.123456" or "CLd1 =  0.0115".
_KV_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9'_]*)\s*=\s*(-?\d+\.?\d*(?:[eE][-+]?\d+)?)")


def _parse_avl_forces(forces_path: str) -> AvlResult:
    """Parse an AVL ``.st`` total-forces / stability block into an AvlResult.

    Extracts ``CLtot`` → CL_3D, ``CDind`` → CDi, Trefftz ``e`` →
    span_efficiency_e, ``Cmtot`` → Cm, the alpha/beta/rate stability
    derivatives (``CLa``, ``Cma``, ``CLq`` …), and any control derivatives
    (``CLd1``, ``Cmd1`` …) when a CONTROL was declared.

    Tolerant of AVL's spacing; never raises on a missing field (it is simply
    absent / defaulted).  Raises AvlRunError only if no CLtot can be found at
    all (i.e. the file is not a forces block).
    """
    try:
        with open(forces_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except FileNotFoundError as exc:
        raise AvlRunError(f"AVL forces file not found: {forces_path!r}") from exc

    # Collect every key=value pair.
    kv: dict[str, float] = {}
    for m in _KV_RE.finditer(text):
        key, val = m.group(1), m.group(2)
        try:
            kv[key] = float(val)
        except ValueError:
            continue

    if "CLtot" not in kv:
        raise AvlRunError(
            f"no CLtot found in {forces_path!r} — not an AVL forces block"
        )

    cl = kv.get("CLtot", 0.0)
    cdi = kv.get("CDind", kv.get("CDtot", 0.0))
    e = kv.get("e", 0.0)
    cm = kv.get("Cmtot", 0.0)

    # Stability derivatives: <coef><axis> where axis ∈ {a,b,p,q,r}.
    deriv_keys = (
        "CLa", "CLb", "CYa", "CYb", "Cla", "Clb", "Cma", "Cmb", "Cna", "Cnb",
        "CLp", "CLq", "CLr", "CYp", "CYq", "CYr",
        "Clp", "Clq", "Clr", "Cmp", "Cmq", "Cmr", "Cnp", "Cnq", "Cnr",
    )
    stability = {k: kv[k] for k in deriv_keys if k in kv}

    # Control derivatives: <coef>d<digit> (CLd1, Cmd1, ...).
    control = {
        k: v for k, v in kv.items()
        if re.fullmatch(r"C[A-Za-z]{1,2}'?d\d+", k)
    }

    return AvlResult(
        CL_3D=cl,
        CDi=cdi,
        span_efficiency_e=e,
        Cm=cm,
        stability_derivatives=stability,
        control_derivatives=control,
        passed=True,
        warnings=[],
        forces_path=forces_path,
    )


# ---------------------------------------------------------------------------
# B3 — runner
# ---------------------------------------------------------------------------

def run_avl_analysis(
    spec: WingSpec,
    *,
    alpha_deg: float,
    velocity_ms: float,
    density_kgm3: float,
    avl_bin: str | None = None,
    timeout_s: float = _AVL_TIMEOUT_S,
    work_dir: str | None = None,
    keep_forces_file: bool = False,
) -> AvlResult:
    """Generate AVL inputs, run AVL, and parse the total-forces block.

    Parameters
    ----------
    spec : WingSpec
        Wing geometry + reference quantities.
    alpha_deg, velocity_ms, density_kgm3 : float
        Operating point (one alpha).
    avl_bin : str | None
        Path to the AVL executable; falls back through ``_resolve_avl_bin``.
    timeout_s : float
        Kill AVL if it has not exited within this many seconds (default 30).
    work_dir : str | None
        Directory for temp files; a fresh tmpdir is created if None.
    keep_forces_file : bool
        If True and ``work_dir`` is supplied, leave the ``.st`` file in place
        (otherwise it is discarded with the tmpdir).

    Returns
    -------
    AvlResult

    Raises
    ------
    AvlNotFoundError
        If the AVL binary is not found at the resolved path.
    AvlRunError
        If AVL exits non-zero or produces no parseable forces block.
    """
    bin_path = _resolve_avl_bin(avl_bin)

    _cleanup_tmp = work_dir is None
    tmp_obj = tempfile.TemporaryDirectory() if _cleanup_tmp else None
    wdir = tmp_obj.name if tmp_obj else work_dir
    assert wdir is not None

    try:
        avl_path = str(Path(wdir) / "wing.avl")
        run_path = str(Path(wdir) / "wing.run")
        st_path = str(Path(wdir) / "forces.st")

        avl_text, run_text = _build_avl_geometry(
            spec, alpha_deg, velocity_ms, density_kgm3
        )
        Path(avl_path).write_text(avl_text, encoding="utf-8")
        Path(run_path).write_text(run_text, encoding="utf-8")

        script = _build_avl_command_script(avl_path, run_path, st_path)

        logger.debug(
            "Running AVL: bin=%r alpha=%.2f V=%.1f rho=%.3f sections=%d",
            bin_path, alpha_deg, velocity_ms, density_kgm3,
            len(spec.get("sections", [])),
        )

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                _as_argv(bin_path),
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=wdir,
            )
        except FileNotFoundError as exc:
            raise AvlNotFoundError(
                f"AVL binary not found at {bin_path!r}. "
                "Set MIRUM_AVL_BIN env var or install AVL on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AvlRunError(
                f"AVL timed out after {timeout_s}s."
            ) from exc

        elapsed = time.monotonic() - t0
        logger.debug("AVL finished in %.2fs, returncode=%d", elapsed, proc.returncode)

        if proc.returncode != 0:
            stderr_snip = (proc.stderr or "")[:400]
            raise AvlRunError(
                f"AVL exited with code {proc.returncode}. stderr: {stderr_snip!r}"
            )

        if not Path(st_path).exists():
            stdout_snip = (proc.stdout or "")[-600:]
            raise AvlRunError(
                f"AVL produced no forces file at {st_path!r}. "
                f"AVL stdout (tail):\n{stdout_snip}"
            )

        result = _parse_avl_forces(st_path)
        if not keep_forces_file or tmp_obj is not None:
            # The tmpdir (and thus the file) is about to be cleaned up.
            result["forces_path"] = None
        return result

    finally:
        if tmp_obj is not None:
            tmp_obj.cleanup()


# ---------------------------------------------------------------------------
# B4 — dispatcher: prefer AVL, fall back to lifting_line
# ---------------------------------------------------------------------------

def _wingspec_to_lifting_line_inputs(
    spec: WingSpec,
) -> tuple[list[float], list[float], float]:
    """Project a WingSpec onto lifting-line inputs (chord, twist, span).

    Uses each SECTION's chord and incidence directly as the spanwise
    distribution and the spec's ``ref_span`` as the full span.
    """
    sections = spec.get("sections", [])
    chords = [s.get("chord", 0.0) for s in sections]
    twists = [s.get("incidence_deg", 0.0) for s in sections]
    span = spec.get("ref_span", 0.0)
    return chords, twists, span


def analyze_wing_3d(
    spec: WingSpec,
    *,
    alpha_deg: float,
    velocity_ms: float,
    density_kgm3: float,
    sectional_cl_alpha_per_rad: float,
    sectional_cl_alpha0_deg: float,
    prefer_avl: bool = True,
    avl_bin: str | None = None,
    n_fourier_terms: int = 40,
) -> WingPolarPoint:
    """3-D wing analysis with graceful AVL→lifting-line fallback (B4).

    When ``prefer_avl`` is True the AVL binary is tried first; if it is missing
    (``AvlNotFoundError``) or errors (``AvlRunError``) the analytic
    ``prandtl_lifting_line`` baseline is used instead.  The return value is
    always a ``WingPolarPoint`` (AVL results are projected onto its shared
    fields) so callers get a uniform contract on
    ``CL_3D / CDi / span_efficiency_e``.

    The ``warnings`` list always records which engine produced the result.
    """
    chords, twists, span = _wingspec_to_lifting_line_inputs(spec)

    def _lifting_line(extra_warn: str) -> WingPolarPoint:
        res = prandtl_lifting_line(
            chord_distribution_m=chords,
            twist_distribution_deg=twists,
            sectional_cl_alpha_per_rad=sectional_cl_alpha_per_rad,
            sectional_cl_alpha0_deg=sectional_cl_alpha0_deg,
            span_m=span,
            aoa_root_deg=alpha_deg,
            velocity_ms=velocity_ms,
            density_kgm3=density_kgm3,
            n_fourier_terms=n_fourier_terms,
        )
        res["warnings"] = [extra_warn, *res["warnings"]]
        return res

    if not prefer_avl:
        return _lifting_line("engine=lifting_line (AVL not preferred)")

    try:
        avl_res = run_avl_analysis(
            spec,
            alpha_deg=alpha_deg,
            velocity_ms=velocity_ms,
            density_kgm3=density_kgm3,
            avl_bin=avl_bin,
        )
    except AvlNotFoundError:
        return _lifting_line(
            "engine=lifting_line (AVL binary absent — fell back)"
        )
    except AvlRunError as exc:
        return _lifting_line(
            f"engine=lifting_line (AVL run failed: {exc}; fell back)"
        )

    # Project the AVL result onto the shared WingPolarPoint contract.
    return WingPolarPoint(
        CL_3D=avl_res["CL_3D"],
        CDi=avl_res["CDi"],
        span_efficiency_e=avl_res["span_efficiency_e"],
        L_distribution=[],   # AVL strip loads not parsed in v1
        Cl_local=[],
        passed=avl_res["passed"],
        warnings=["engine=avl", *avl_res["warnings"]],
    )
