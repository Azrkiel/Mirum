"""
analysis/airfoil.py — Airfoil geometry extraction + XFOIL aerodynamic analysis
===============================================================================

Three top-level capabilities, all standalone (no pipeline imports):

  1. STEP → .dat   (P1-02)
     OCC BRepAlgoAPI_Section slices a T2CIII wing STEP file at mid-span,
     extracts the airfoil cross-section, normalises it, and writes an
     XFOIL-compatible Selig .dat coordinate file.

  2. XFOIL runner  (P1-03)
     Drives XFOIL via subprocess pipe with a scripted command sequence,
     captures the polar file, and returns a structured PolarResult TypedDict.

  3. Stall predictor (P1-04)
     Post-processes a PolarResult: dCl/dα sign-change detection to find
     stall_alpha and Cl_max.

Axis convention (matches T2CIII Domain D):
  X = chord  (leading edge at x=0, trailing edge at x=chord)
  Y = span   (root at Y=0, tip at Y=span)
  Z = thickness (suction surface positive)

XFOIL coordinate convention (Selig format):
  Normalised by chord: x ∈ [0,1], z/c
  Order: trailing-edge → upper → leading-edge → lower → trailing-edge
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

logger = logging.getLogger("mirum.analysis.airfoil")


# ---------------------------------------------------------------------------
# TypedDicts  (plan spec)
# ---------------------------------------------------------------------------

class FlightEnvelope(TypedDict, total=False):
    """Analysis conditions for an XFOIL polar run."""
    alpha_start: float   # degrees  (default 0.0)
    alpha_end:   float   # degrees  (default 15.0)
    alpha_step:  float   # degrees  (default 1.0)
    reynolds:    float   # e.g. 1e6
    mach:        float   # e.g. 0.0 for incompressible
    n_crit:      float   # transition criterion; 9 = standard clean tunnel


class PolarResult(TypedDict):
    """Full output from one XFOIL polar sweep."""
    naca_code:       str               # '0012', '2412', etc., or '' if loaded from .dat
    chord_mm:        float             # representative chord used for Re scaling
    reynolds:        float
    mach:            float
    n_crit:          float
    alpha:           list[float]       # degrees — only converged points
    Cl:              list[float]
    Cd:              list[float]
    Cm:              list[float]
    L_D:             list[float]       # Cl/Cd
    converged_count: int               # number of alpha points that converged
    stall_alpha:     float | None      # degrees, or None if not detected
    Cl_max:          float | None      # peak lift coefficient
    dat_path:        str | None        # .dat file used (None if NACA code used directly)
    polar_path:      str | None        # raw XFOIL polar output file path


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_ENV: FlightEnvelope = {
    "alpha_start": 0.0,
    "alpha_end":   15.0,
    "alpha_step":  1.0,
    "reynolds":    1_000_000.0,
    "mach":        0.0,
    "n_crit":      9.0,
}

_XFOIL_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class XfoilNotFoundError(RuntimeError):
    """Raised when the xfoil binary is not on PATH or at the configured path."""


class XfoilRunError(RuntimeError):
    """Raised when XFOIL exits with a failure or produces an empty polar."""


class StepSliceError(RuntimeError):
    """Raised when OCC cannot slice the STEP file."""


# ---------------------------------------------------------------------------
# P1-02  STEP → .dat  (OCC BRepAlgoAPI_Section)
# ---------------------------------------------------------------------------

def slice_step_to_dat(
    step_path: str,
    span_mm: float,
    output_dat_path: str,
    *,
    n_points_per_edge: int = 60,
    label: str = "Extracted airfoil",
) -> str:
    """Slice a T2CIII wing STEP file at mid-span and write a Selig .dat file.

    Uses OCC BRepAlgoAPI_Section to cut the OML solid at Y = span_mm / 2,
    extracts the (X, Z) cross-section, normalises by chord, sorts into
    XFOIL Selig order, and writes the coordinate file.

    Parameters
    ----------
    step_path : str
        Path to the wing STEP file produced by the T2CIII pipeline.
    span_mm : float
        Total wing span in mm (determines the Y-position of the cut).
    output_dat_path : str
        Destination .dat file path.
    n_points_per_edge : int
        Discretisation density per OCC edge (default 60).
    label : str
        First line of the .dat file (airfoil name).

    Returns
    -------
    str
        ``output_dat_path`` on success.

    Raises
    ------
    StepSliceError
        If the STEP cannot be loaded or the section produces no usable edges.
    ImportError
        If pythonOCC is not installed in the current environment.
    """
    try:
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
        from OCC.Core.GCPnts import GCPnts_UniformAbscissa
        from OCC.Core.IFSelect import IFSelect_RetDone
        from OCC.Core.STEPControl import STEPControl_Reader
        from OCC.Core.TopAbs import TopAbs_EDGE
        from OCC.Core.TopExp import TopExp_Explorer
        from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt
    except ImportError as exc:
        raise ImportError(
            "pythonOCC is required for STEP slicing. "
            "Install via: conda install -c conda-forge pythonocc-core"
        ) from exc

    # ── Load STEP ────────────────────────────────────────────────────────────
    reader = STEPControl_Reader()
    status = reader.ReadFile(step_path)
    if status != IFSelect_RetDone:
        raise StepSliceError(f"STEPControl_Reader failed on {step_path!r}")
    nroots = reader.TransferRoots()
    if nroots == 0:
        raise StepSliceError(f"No transferable roots in STEP file {step_path!r}")
    shape = reader.Shape()

    # ── Build cutting plane at mid-span (Y = span/2) ─────────────────────────
    mid_y = span_mm / 2.0
    plane = gp_Pln(gp_Pnt(0.0, mid_y, 0.0), gp_Dir(0.0, 1.0, 0.0))

    section = BRepAlgoAPI_Section(shape, plane, False)
    section.ComputePCurveOn1(True)
    section.Build()
    if not section.IsDone():
        raise StepSliceError(
            f"BRepAlgoAPI_Section failed at Y={mid_y:.1f} mm "
            f"(span={span_mm:.1f} mm)"
        )

    # ── Discretise all edges → (X, Z) points ─────────────────────────────────
    raw_xz: list[tuple[float, float]] = []
    explorer = TopExp_Explorer(section.Shape(), TopAbs_EDGE)
    edge_count = 0
    while explorer.More():
        edge = explorer.Current()
        try:
            adaptor = BRepAdaptor_Curve(edge)
            disc = GCPnts_UniformAbscissa()
            disc.Initialize(adaptor, n_points_per_edge)
            if disc.IsDone():
                for i in range(1, disc.NbPoints() + 1):
                    pt = adaptor.Value(disc.Parameter(i))
                    raw_xz.append((pt.X(), pt.Z()))
                edge_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipping edge: %s", exc)
        explorer.Next()

    if not raw_xz:
        raise StepSliceError(
            f"Section at Y={mid_y:.1f} mm produced no usable points "
            f"({edge_count} edges visited). "
            "Check that span_mm matches the wing geometry."
        )

    logger.debug("Section: %d edges, %d raw points", edge_count, len(raw_xz))

    # ── Sort, normalise, write ─────────────────────────────────────────────
    sorted_pts = _sort_airfoil_points(raw_xz)
    _write_dat(sorted_pts, label, output_dat_path)
    logger.info("Wrote %d coordinate pairs to %r", len(sorted_pts), output_dat_path)
    return output_dat_path


def _sort_airfoil_points(
    raw_xz: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Normalise, deduplicate, and sort raw (X, Z) points into Selig order.

    Returns normalised (x/c, z/c) pairs ordered:
      trailing-edge (1, ~0) → upper surface → leading-edge (0, 0)
      → lower surface → trailing-edge (1, ~0)
    """
    if len(raw_xz) < 10:
        raise StepSliceError(
            f"Too few cross-section points ({len(raw_xz)}); section may be degenerate."
        )

    xs = [p[0] for p in raw_xz]
    zs = [p[1] for p in raw_xz]
    x_min, x_max = min(xs), max(xs)
    chord = x_max - x_min

    if chord < 1e-6:
        raise StepSliceError("Chord length is effectively zero — degenerate section.")

    # Normalise: leading edge → (0, 0)
    norm = [((x - x_min) / chord, z / chord) for x, z in raw_xz]

    # Remove near-duplicates with a spatial grid (1/1000 chord bins)
    grid: dict[tuple[int, int], tuple[float, float]] = {}
    for x, z in norm:
        key = (round(x * 1000), round(z * 1000))
        grid[key] = (x, z)
    deduped = list(grid.values())

    # Split upper / lower on Z sign
    upper = [(x, z) for x, z in deduped if z >= 0.0]
    lower = [(x, z) for x, z in deduped if z < 0.0]

    # Upper: trailing-edge (x→1) to leading-edge (x→0), descending X
    upper.sort(key=lambda p: -p[0])
    # Lower: leading-edge (x→0) to trailing-edge (x→1), ascending X
    lower.sort(key=lambda p: p[0])

    return upper + lower


def _write_dat(
    points: list[tuple[float, float]],
    label: str,
    dat_path: str,
) -> None:
    with open(dat_path, "w", encoding="utf-8") as fh:
        fh.write(f"{label}\n")
        for x, z in points:
            fh.write(f"  {x:.6f}  {z:.6f}\n")


# ---------------------------------------------------------------------------
# P1-02 (analytical fallback)  NACA 4-digit → .dat
# ---------------------------------------------------------------------------

def naca4_to_dat(
    naca_code: str,
    dat_path: str,
    *,
    n_points: int = 100,
) -> str:
    """Generate a Selig .dat file analytically from a NACA 4-digit code.

    Uses the exact NACA 4-digit thickness and camber distributions — the same
    formula as ``cad_utils.make_naca_wire()``.  No OCCT required.

    Parameters
    ----------
    naca_code : str
        4-digit NACA code, e.g. '0012', '2412'.
    dat_path : str
        Output .dat file path.
    n_points : int
        Number of points per surface (default 100).  Total file will have
        approximately 2*n_points rows.

    Returns
    -------
    str
        ``dat_path`` on success.
    """
    code = naca_code.strip()
    if len(code) != 4 or not code.isdigit():
        raise ValueError(
            f"Invalid NACA code {code!r}: must be exactly 4 digits (e.g. '0012')."
        )

    m = int(code[0]) / 100.0   # max camber fraction
    p = int(code[1]) / 10.0    # max camber position fraction
    t = int(code[2:4]) / 100.0  # max thickness fraction (half-chord)

    # Cosine spacing — denser at LE and TE
    beta = [math.pi * i / (n_points - 1) for i in range(n_points)]
    xc = [(1.0 - math.cos(b)) / 2.0 for b in beta]

    def thickness(x: float) -> float:
        return (t / 0.2) * (
            0.2969 * math.sqrt(x)
            - 0.1260 * x
            - 0.3516 * x ** 2
            + 0.2843 * x ** 3
            - 0.1015 * x ** 4
        )

    def camber_and_slope(x: float) -> tuple[float, float]:
        if p < 1e-9:  # symmetric
            return 0.0, 0.0
        if x <= p:
            yc = m / p ** 2 * (2 * p * x - x ** 2)
            dyc = 2 * m / p ** 2 * (p - x)
        else:
            yc = m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2)
            dyc = 2 * m / (1 - p) ** 2 * (p - x)
        return yc, dyc

    upper: list[tuple[float, float]] = []
    lower: list[tuple[float, float]] = []
    for x in xc:
        th = thickness(x)
        yc, dyc = camber_and_slope(x)
        theta = math.atan(dyc)
        xu = x - th * math.sin(theta)
        zu = yc + th * math.cos(theta)
        xl = x + th * math.sin(theta)
        zl = yc - th * math.cos(theta)
        upper.append((xu, zu))
        lower.append((xl, zl))

    # Selig order: upper TE→LE, then lower LE→TE
    upper.reverse()   # was LE→TE, now TE→LE
    # lower is already LE→TE from xc ordering

    _write_dat(upper + lower, f"NACA {naca_code}", dat_path)
    logger.info("Wrote NACA %s .dat (%d points) to %r", naca_code, len(upper + lower), dat_path)
    return dat_path


# ---------------------------------------------------------------------------
# P1-03  XFOIL runner
# ---------------------------------------------------------------------------

def _merge_env(user: FlightEnvelope) -> FlightEnvelope:
    merged: FlightEnvelope = dict(_DEFAULT_ENV)  # type: ignore[assignment]
    merged.update(user)
    return merged


def _build_xfoil_script(
    airfoil_spec: str,
    env: FlightEnvelope,
    polar_path: str,
) -> str:
    """Build the XFOIL stdin command sequence.

    Parameters
    ----------
    airfoil_spec : str
        Either ``'NACA xxxx'`` (use built-in NACA definition) or a full
        filesystem path to a .dat file.
    env : FlightEnvelope
        Analysis conditions (already merged with defaults).
    polar_path : str
        Full path where XFOIL will write the polar accumulation file.
    """
    lines: list[str] = []

    # ── Disable graphics (PLOP G F) ──────────────────────────────────────────
    # Without this, ASEQ in OPER tries to open an X11 display for XFOIL's
    # built-in plotter and aborts with "Cannot open display" in headless
    # environments (Docker, CI). Has no effect on systems with a display —
    # the polar still accumulates to the PACC file regardless.
    lines.append("PLOP")
    lines.append("G F")
    lines.append("")  # blank line exits the PLOP submenu

    # ── Load airfoil ─────────────────────────────────────────────────────────
    if airfoil_spec.upper().startswith("NACA"):
        lines.append(airfoil_spec.upper())  # e.g. "NACA 0012"
    else:
        # Load from coordinate file then re-panel for smooth geometry
        lines.append(f"LOAD {airfoil_spec}")
        lines.append("PANE")

    # ── OPER sub-menu ────────────────────────────────────────────────────────
    lines.append("OPER")

    # Enable viscous analysis and set Reynolds number.
    # XFOIL behaviour: when viscous is OFF, typing "VISC" prompts for Re.
    # When viscous is ON, typing "VISC" toggles it off.  So we always type
    # VISC then the Re value on the following line.
    lines.append("VISC")
    lines.append(f"{env['reynolds']:.6e}")

    # Mach number
    lines.append(f"MACH {env['mach']:.4f}")

    # n_crit — only enter VPAR submenu when non-default (avoids extra prompts)
    n_crit = env.get("n_crit", 9.0)
    if abs(n_crit - 9.0) > 0.05:
        lines.append("VPAR")
        lines.append(f"N {n_crit:.2f}")
        lines.append("")   # blank = exit VPAR submenu

    # Newton iteration limit
    lines.append("ITER 100")

    # ── Polar accumulation ───────────────────────────────────────────────────
    lines.append("PACC")
    lines.append(str(polar_path))   # output file
    lines.append("")                # no drag-dump file (blank)

    # ── Alpha sweep ──────────────────────────────────────────────────────────
    a_start = env.get("alpha_start", _DEFAULT_ENV["alpha_start"])  # type: ignore[literal-required]
    a_end   = env.get("alpha_end",   _DEFAULT_ENV["alpha_end"])    # type: ignore[literal-required]
    a_step  = env.get("alpha_step",  _DEFAULT_ENV["alpha_step"])   # type: ignore[literal-required]
    lines.append(f"ASEQ {a_start:.2f} {a_end:.2f} {a_step:.2f}")

    # Stop accumulation then exit
    lines.append("PACC")
    lines.append("")   # exit OPER submenu
    lines.append("QUIT")

    return "\n".join(lines) + "\n"


def _parse_polar_file(
    polar_path: str,
) -> dict[str, list[float]]:
    """Parse an XFOIL polar accumulation file.

    XFOIL polar format (after the header block):
        alpha      CL       CD       CDp      CM     Top_Xtr  Bot_Xtr
       ------    ------   ------   ------   ------   ------   ------
         0.000   0.0000   0.00606  ...

    Only lines with 7 numeric tokens are treated as data rows.

    Returns
    -------
    dict with keys 'alpha', 'Cl', 'Cd', 'Cm' — all list[float], same length.
    Unconverged points are never written to the polar file by XFOIL, so every
    row returned here is a converged point.
    """
    alpha_l: list[float] = []
    cl_l:    list[float] = []
    cd_l:    list[float] = []
    cm_l:    list[float] = []

    try:
        with open(polar_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return {"alpha": alpha_l, "Cl": cl_l, "Cd": cd_l, "Cm": cm_l}

    # Find the separator line (row of dashes immediately after column headers)
    data_start = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^-{4,}", stripped):
            data_start = i + 1
            break

    if data_start < 0:
        logger.warning("No separator line found in polar file %r", polar_path)
        return {"alpha": alpha_l, "Cl": cl_l, "Cd": cd_l, "Cm": cm_l}

    for line in lines[data_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 5:
            continue
        try:
            a   = float(parts[0])
            cl  = float(parts[1])
            cd  = float(parts[2])
            # parts[3] = CDp (pressure drag) — not exposed in PolarResult
            cm  = float(parts[4])
        except ValueError:
            continue
        alpha_l.append(a)
        cl_l.append(cl)
        cd_l.append(cd)
        cm_l.append(cm)

    return {"alpha": alpha_l, "Cl": cl_l, "Cd": cd_l, "Cm": cm_l}


def run_xfoil_polar(
    airfoil_spec: str,
    flight_env: FlightEnvelope,
    *,
    xfoil_bin: str | None = None,
    timeout_s: float = _XFOIL_TIMEOUT_S,
    work_dir: str | None = None,
    naca_code: str = "",
    chord_mm: float = 0.0,
) -> PolarResult:
    """Run an XFOIL polar sweep and return a structured PolarResult.

    Parameters
    ----------
    airfoil_spec : str
        ``'NACA 0012'`` to use XFOIL's built-in definition, or a filesystem
        path to a Selig .dat file (absolute or relative to ``work_dir``).
    flight_env : FlightEnvelope
        Analysis conditions.  Missing keys are filled from defaults.
    xfoil_bin : str | None
        Path to the xfoil executable.  Falls back to the ``MIRUM_XFOIL_BIN``
        env var, then ``backend/config.py`` ``XFOIL_BIN``, then ``'xfoil'``
        on PATH.
    timeout_s : float
        Kill XFOIL if it has not exited within this many seconds (default 30).
    work_dir : str | None
        Directory for temporary files.  A fresh tmpdir is created if None.
    naca_code : str
        Stored in the returned PolarResult for reference.
    chord_mm : float
        Representative chord length (mm) stored in PolarResult.

    Returns
    -------
    PolarResult

    Raises
    ------
    XfoilNotFoundError
        If the xfoil binary is not found at the resolved path.
    XfoilRunError
        If XFOIL exits with a non-zero code or produces fewer than 2 converged
        points.
    """
    env = _merge_env(flight_env)
    bin_path = _resolve_xfoil_bin(xfoil_bin)

    _cleanup_tmp = work_dir is None
    tmp_obj = tempfile.TemporaryDirectory() if _cleanup_tmp else None
    wdir = tmp_obj.name if tmp_obj else work_dir
    assert wdir is not None  # for type checker

    try:
        polar_path = str(Path(wdir) / "polar.txt")
        script = _build_xfoil_script(airfoil_spec, env, polar_path)

        logger.debug(
            "Running XFOIL: bin=%r airfoil=%r Re=%.0f Mach=%.3f α=[%.1f,%.1f,%.1f]",
            bin_path, airfoil_spec, env["reynolds"], env["mach"],
            env.get("alpha_start", 0), env.get("alpha_end", 15), env.get("alpha_step", 1),
        )
        logger.debug("XFOIL script:\n%s", script)

        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [bin_path],
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=wdir,
            )
        except FileNotFoundError as exc:
            raise XfoilNotFoundError(
                f"XFOIL binary not found at {bin_path!r}. "
                "Set MIRUM_XFOIL_BIN env var or run bin/install_xfoil_windows.ps1."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise XfoilRunError(
                f"XFOIL timed out after {timeout_s}s. "
                "Reduce the alpha range or increase timeout_s."
            ) from exc

        elapsed = time.monotonic() - t0
        logger.debug("XFOIL finished in %.1fs, returncode=%d", elapsed, proc.returncode)

        if proc.returncode not in (0, 1):
            # XFOIL returns 1 on some convergence failures but still writes the polar
            stderr_snip = (proc.stderr or "")[:400]
            raise XfoilRunError(
                f"XFOIL exited with code {proc.returncode}. stderr: {stderr_snip!r}"
            )

        # Parse the polar file
        parsed = _parse_polar_file(polar_path)
        alpha   = parsed["alpha"]
        cl_vals = parsed["Cl"]
        cd_vals = parsed["Cd"]
        cm_vals = parsed["Cm"]

        if len(alpha) < 2:
            stdout_snip = (proc.stdout or "")[-600:]
            raise XfoilRunError(
                f"XFOIL produced fewer than 2 converged points. "
                f"XFOIL stdout (tail):\n{stdout_snip}"
            )

        # Compute L/D, guard against zero Cd
        ld = [c / d if abs(d) > 1e-9 else 0.0 for c, d in zip(cl_vals, cd_vals)]

        # Stall detection
        stall_alpha, cl_max = predict_stall_from_lists(alpha, cl_vals)

        result: PolarResult = {
            "naca_code":       naca_code,
            "chord_mm":        chord_mm,
            "reynolds":        env["reynolds"],
            "mach":            env["mach"],
            "n_crit":          env.get("n_crit", 9.0),
            "alpha":           alpha,
            "Cl":              cl_vals,
            "Cd":              cd_vals,
            "Cm":              cm_vals,
            "L_D":             ld,
            "converged_count": len(alpha),
            "stall_alpha":     stall_alpha,
            "Cl_max":          cl_max,
            "dat_path":        airfoil_spec if not airfoil_spec.upper().startswith("NACA") else None,
            "polar_path":      polar_path if Path(polar_path).exists() else None,
        }
        return result

    finally:
        if tmp_obj is not None:
            tmp_obj.cleanup()


def _resolve_xfoil_bin(override: str | None) -> str:
    """Return the xfoil binary path, checking several sources in priority order.

    Priority: override arg → MIRUM_XFOIL_BIN env var → repo-local
    bin/xfoil.exe on Windows → 'xfoil' on PATH (Linux/macOS).
    """
    if override:
        return override

    env_val = os.environ.get("MIRUM_XFOIL_BIN", "")
    if env_val:
        return env_val

    if os.name == "nt":
        # analysis/airfoil.py → <repo>/bin/xfoil.exe (matches install_xfoil_windows.ps1)
        return str(Path(__file__).resolve().parent.parent / "bin" / "xfoil.exe")

    return "xfoil"


# ---------------------------------------------------------------------------
# P1-04  Stall predictor
# ---------------------------------------------------------------------------

def predict_stall_from_lists(
    alpha: list[float],
    cl: list[float],
) -> tuple[float | None, float | None]:
    """Detect stall from raw alpha and Cl lists.

    Strategy:
    1. Find the global Cl_max.
    2. Walk forward from Cl_max; flag the first point where Cl drops by more
       than 5% relative to Cl_max (soft stall criterion).
    3. Additionally scan for the first sign change in dCl/dα (hard stall).
    4. Return whichever trigger fires first.

    Returns
    -------
    (stall_alpha, Cl_max)  — both None if fewer than 3 data points.
    """
    if len(cl) < 3:
        return None, None

    cl_max = max(cl)
    cl_max_idx = cl.index(cl_max)

    stall_alpha: float | None = None

    # Hard criterion: first negative dCl/dα after positive region
    for i in range(1, len(cl) - 1):
        dcl_fwd  = cl[i + 1] - cl[i]
        dcl_back = cl[i]     - cl[i - 1]
        if dcl_back > 0 and dcl_fwd < 0:
            stall_alpha = alpha[i]
            break

    # Soft criterion: Cl drops ≥5% from peak
    soft_stall: float | None = None
    for i in range(cl_max_idx + 1, len(cl)):
        if cl[i] < cl_max * 0.95:
            soft_stall = alpha[i]
            break

    # Use the earlier of the two triggers
    if stall_alpha is None:
        stall_alpha = soft_stall
    elif soft_stall is not None:
        stall_alpha = min(stall_alpha, soft_stall, key=lambda a: a)

    return stall_alpha, cl_max


def predict_stall(polar: PolarResult) -> tuple[float | None, float | None]:
    """Convenience wrapper: extract stall info from a PolarResult.

    Returns
    -------
    (stall_alpha, Cl_max) — both None if polar contains fewer than 3 points.
    """
    return predict_stall_from_lists(polar["alpha"], polar["Cl"])


# ---------------------------------------------------------------------------
# High-level convenience functions
# ---------------------------------------------------------------------------

def analyze_naca(
    naca_code: str,
    chord_mm: float,
    flight_env: FlightEnvelope,
    *,
    xfoil_bin: str | None = None,
    timeout_s: float = _XFOIL_TIMEOUT_S,
    work_dir: str | None = None,
) -> PolarResult:
    """Run a full XFOIL polar for a NACA 4-digit airfoil by code.

    XFOIL uses its own built-in analytic definition — no .dat file is written.

    Parameters
    ----------
    naca_code : str
        4-digit NACA code, e.g. ``'0012'`` or ``'2412'``.
    chord_mm : float
        Representative chord length in mm (stored in result, not used by XFOIL
        directly — XFOIL works on normalised coordinates).
    flight_env : FlightEnvelope
        Analysis conditions.
    """
    code = naca_code.strip().lstrip("NACA").strip()
    if len(code) != 4 or not code.isdigit():
        raise ValueError(f"Expected a 4-digit NACA code, got {naca_code!r}.")

    airfoil_spec = f"NACA {code}"
    return run_xfoil_polar(
        airfoil_spec,
        flight_env,
        xfoil_bin=xfoil_bin,
        timeout_s=timeout_s,
        work_dir=work_dir,
        naca_code=code,
        chord_mm=chord_mm,
    )


def analyze_wing_step(
    step_path: str,
    naca_code: str,
    span_mm: float,
    chord_mm: float,
    flight_env: FlightEnvelope,
    *,
    xfoil_bin: str | None = None,
    timeout_s: float = _XFOIL_TIMEOUT_S,
) -> PolarResult:
    """Slice a T2CIII wing STEP at mid-span, then run the XFOIL polar.

    This is the full pipeline path: geometry → coordinates → aerodynamics.

    When ``naca_code`` is non-empty and the STEP slice fails (e.g. OCC not
    installed), the function falls back to the analytical NACA definition so
    callers always get a polar.

    Parameters
    ----------
    step_path : str
        Path to the ``output.step`` produced by the T2CIII pipeline.
    naca_code : str
        NACA 4-digit code (used for the .dat label and fallback).
    span_mm : float
        Wing span in mm (sets the mid-span cut position).
    chord_mm : float
        Representative chord in mm (stored in PolarResult).
    flight_env : FlightEnvelope
        Analysis conditions.
    """
    with tempfile.TemporaryDirectory() as wdir:
        dat_path = str(Path(wdir) / "section.dat")
        label = f"NACA {naca_code}" if naca_code else Path(step_path).stem

        # Try OCC slice first; fall back to analytical NACA if unavailable
        try:
            slice_step_to_dat(step_path, span_mm, dat_path, label=label)
            airfoil_spec = dat_path
            logger.info("Using OCC mid-span section from %r", step_path)
        except (ImportError, StepSliceError) as exc:
            if naca_code:
                # Defense-in-depth: validate naca_code before it reaches the
                # XFOIL stdin script. Any non-conforming code (whitespace,
                # newlines, non-digit characters) is rejected here even though
                # upstream Pydantic max_length=4 limits the worst case.
                code = naca_code.strip().lstrip("NACA").strip()
                if len(code) != 4 or not code.isdigit():
                    raise StepSliceError(
                        f"OCC slice failed and naca_code {naca_code!r} is not a "
                        f"valid 4-digit fallback (stripped to {code!r})."
                    ) from exc
                logger.warning(
                    "STEP slice failed (%s); falling back to NACA %s analytical definition.",
                    exc, code,
                )
                airfoil_spec = f"NACA {code}"
                dat_path = None  # type: ignore[assignment]
            else:
                raise

        return run_xfoil_polar(
            airfoil_spec,
            flight_env,
            xfoil_bin=xfoil_bin,
            timeout_s=timeout_s,
            work_dir=wdir,
            naca_code=naca_code,
            chord_mm=chord_mm,
        )
