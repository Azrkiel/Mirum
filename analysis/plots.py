"""
analysis/plots.py — Server-side aerodynamic polar plots (T2-16).
================================================================

Three chart functions, all returning base64-encoded PNG strings.
All rendering is server-side (matplotlib Agg backend) — no browser
JS charting library is needed; the frontend embeds the PNGs directly.

  plot_cl_alpha(polar)
      Cl vs α line chart.  Stall α marked with a vertical dashed line
      and annotated with Cl_max.

  plot_cd_alpha(polar)
      Cd vs α on a log-scale Y-axis so low-α laminar drag is visible.

  plot_cp_dist(naca_code, alpha, reynolds, mach, n_crit)
      Cp distribution at one α point.  Upper and lower surface plotted
      separately; the gap between them is filled.  Requires a working
      XFOIL binary (MIRUM_XFOIL_BIN env var or "xfoil" on PATH).
      On XFOIL failure the function returns a placeholder PNG rather
      than raising.

Style
-----
Dark background (#0e1117) matching Streamlit's default dark theme so
the PNGs look native when embedded with st.image().
"""

from __future__ import annotations

import base64
import io
import logging
import os
from typing import Any

logger = logging.getLogger("mirum.analysis.plots")

# ---------------------------------------------------------------------------
# Matplotlib — non-interactive Agg backend (safe for server / threads)
# ---------------------------------------------------------------------------

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import matplotlib.ticker as ticker       # noqa: E402

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_BG      = "#0e1117"    # Streamlit dark background
_AXES_BG = "#16181f"    # slightly lighter axes area
_GRID    = "#2d2d3a"    # faint grid lines
_TEXT    = "#c8ccd6"    # axis labels / tick text
_ACCENT  = "#4f8ef7"    # primary line (upper surface, Cl)
_ORANGE  = "#f7a24f"    # secondary line (Cd, lower surface)
_STALL   = "#ff4b4b"    # stall annotation
_FILL    = "#4f8ef7"    # fill-between alpha=0.10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_fig(figsize: tuple[float, float] = (5.8, 3.4)) -> tuple:
    """Create a dark-themed Figure + Axes pair."""
    fig, ax = plt.subplots(figsize=figsize, facecolor=_BG)
    ax.set_facecolor(_AXES_BG)
    ax.tick_params(colors=_TEXT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID)
    ax.xaxis.label.set_color(_TEXT)
    ax.yaxis.label.set_color(_TEXT)
    ax.title.set_color("#e0e0e0")
    ax.grid(True, color=_GRID, linestyle="--", linewidth=0.5, alpha=0.7)
    return fig, ax


def _fig_to_b64(fig: plt.Figure) -> str:
    """Render a Figure to a base64 PNG and close it."""
    buf = io.BytesIO()
    fig.savefig(
        buf, format="png", dpi=110,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
    )
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("ascii")
    plt.close(fig)
    return b64


def _clean_list(values: list[Any]) -> list[float]:
    """Drop None / NaN from a polar list (XFOIL unconverged points)."""
    import math
    return [v for v in values if v is not None and not math.isnan(v)]


def _zip_clean(
    alpha_raw: list[Any],
    y_raw: list[Any],
) -> tuple[list[float], list[float]]:
    """Return (alpha, y) pairs where both are finite floats."""
    import math
    pairs = [
        (a, y)
        for a, y in zip(alpha_raw, y_raw)
        if a is not None and y is not None
        and not math.isnan(a) and not math.isnan(y)
    ]
    if not pairs:
        return [], []
    return [p[0] for p in pairs], [p[1] for p in pairs]


# ---------------------------------------------------------------------------
# P3-01  plot_cl_alpha
# ---------------------------------------------------------------------------

def plot_cl_alpha(polar: dict) -> str:
    """Cl vs α chart with stall_alpha marker and Cl_max annotation.

    Parameters
    ----------
    polar : dict
        PolarReport.model_dump() from the /generate complete event.

    Returns
    -------
    str
        Base64-encoded PNG.
    """
    alpha, Cl = _zip_clean(polar.get("alpha", []), polar.get("Cl", []))
    stall_alpha: float | None = polar.get("stall_alpha")
    Cl_max: float | None = polar.get("Cl_max")
    naca = polar.get("naca_code", "?")

    fig, ax = _make_fig()

    if alpha:
        ax.plot(alpha, Cl, color=_ACCENT, linewidth=1.8,
                marker="o", markersize=3, zorder=3, label="Cl")

    if stall_alpha is not None and alpha:
        ax.axvline(stall_alpha, color=_STALL, linestyle="--",
                   linewidth=1.1, zorder=2,
                   label=f"Stall α = {stall_alpha:.1f}°")
        # Place annotation in the upper-middle of the y-range
        y0, y1 = ax.get_ylim()
        y_mid = y0 + (y1 - y0) * 0.55
        ax.text(stall_alpha + 0.3, y_mid,
                f"Stall\n{stall_alpha:.1f}°",
                color=_STALL, fontsize=7.5, va="center")

        if Cl_max is not None and alpha:
            ax.annotate(
                f"Cl_max = {Cl_max:.3f}",
                xy=(stall_alpha, Cl_max),
                xytext=(max(alpha) * 0.45, Cl_max * 0.88),
                color=_STALL, fontsize=7.5,
                arrowprops=dict(arrowstyle="->", color=_STALL, lw=0.8),
            )
    elif not alpha:
        ax.text(0.5, 0.5, "No converged data",
                ha="center", va="center", color="#888888",
                fontsize=9, transform=ax.transAxes)

    ax.set_xlabel("α  (deg)")
    ax.set_ylabel("Cl")
    ax.set_title(f"Cl vs α  —  NACA {naca}", fontsize=9, pad=6)
    if alpha:
        ax.legend(fontsize=7.5, facecolor=_BG, edgecolor=_GRID,
                  labelcolor=_TEXT, framealpha=0.85)

    fig.tight_layout(pad=1.2)
    return _fig_to_b64(fig)


# ---------------------------------------------------------------------------
# P3-02  plot_cd_alpha
# ---------------------------------------------------------------------------

def plot_cd_alpha(polar: dict) -> str:
    """Cd vs α on a log-scale Y-axis.

    Log scale makes laminar-bucket fine structure visible even when
    stall drag is an order of magnitude larger than cruise drag.

    Parameters
    ----------
    polar : dict
        PolarReport.model_dump().

    Returns
    -------
    str
        Base64-encoded PNG.
    """
    naca = polar.get("naca_code", "?")
    alpha_raw = polar.get("alpha", [])
    Cd_raw = polar.get("Cd", [])

    # Filter: log scale requires positive values; drop None / NaN / ≤0
    import math
    pairs = [
        (a, c)
        for a, c in zip(alpha_raw, Cd_raw)
        if a is not None and c is not None
        and not math.isnan(a) and not math.isnan(c)
        and c > 0
    ]
    alpha = [p[0] for p in pairs]
    Cd    = [p[1] for p in pairs]

    fig, ax = _make_fig()

    if alpha:
        ax.semilogy(alpha, Cd, color=_ORANGE, linewidth=1.8,
                    marker="o", markersize=3, label="Cd")
        ax.yaxis.set_major_formatter(ticker.LogFormatterSciNotation())
    else:
        ax.text(0.5, 0.5, "No converged data",
                ha="center", va="center", color="#888888",
                fontsize=9, transform=ax.transAxes)

    ax.set_xlabel("α  (deg)")
    ax.set_ylabel("Cd  (log scale)")
    ax.set_title(f"Cd vs α  —  NACA {naca}", fontsize=9, pad=6)
    if alpha:
        ax.legend(fontsize=7.5, facecolor=_BG, edgecolor=_GRID,
                  labelcolor=_TEXT, framealpha=0.85)

    fig.tight_layout(pad=1.2)
    return _fig_to_b64(fig)


# ---------------------------------------------------------------------------
# P3-03  XFOIL Cp runner
# ---------------------------------------------------------------------------

def _run_xfoil_cp(
    naca_code: str,
    alpha: float,
    reynolds: float,
    mach: float = 0.0,
    n_crit: float = 9.0,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Run XFOIL at one alpha and return (upper_surface, lower_surface).

    Each surface is a list of (x/c, Cp) tuples sorted by x/c ascending.

    Raises
    ------
    RuntimeError
        If XFOIL binary is not found or produces no Cp output.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    xfoil_bin = os.environ.get("MIRUM_XFOIL_BIN", "xfoil")

    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = Path(tmpdir) / "cp.dat"

        # XFOIL scripted command sequence:
        #   NACA + OPER + Visc + Mach + VPAR N + Alfa + CPWR + quit
        commands = "\n".join([
            f"NACA {naca_code}",
            "PANE",
            "OPER",
            f"Visc {reynolds:.0f}",
            f"Mach {mach:.4f}",
            "VPAR",
            f"N {n_crit:.1f}",
            "",               # exit VPAR
            f"Alfa {alpha:.2f}",
            f"CPWR {cp_path}",
            "",               # exit OPER
            "QUIT",
            "",
        ])

        try:
            proc = subprocess.run(
                [xfoil_bin],
                input=commands,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"XFOIL binary not found: {xfoil_bin!r}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("XFOIL timed out during Cp run") from exc

        if not cp_path.exists():
            raise RuntimeError(
                f"XFOIL did not produce {cp_path} at alpha={alpha:.1f}. "
                f"stdout tail: {proc.stdout[-400:]!r}"
            )

        # Parse XFOIL CPWR output.
        # Format: two-column whitespace-separated "x/c   Cp"
        # Upper surface: x decreasing (TE → LE)
        # Lower surface: x increasing (LE → TE)
        # We detect the turnaround at the leading-edge minimum.
        raw: list[tuple[float, float]] = []
        with open(cp_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(("#", "x", "X")):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                try:
                    raw.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue

        if len(raw) < 6:
            raise RuntimeError(
                f"Cp file too short ({len(raw)} points) — XFOIL may not have converged"
            )

        # Split at the x-minimum (leading edge reversal)
        xs = [p[0] for p in raw]
        le_idx = xs.index(min(xs))

        upper = sorted(raw[:le_idx + 1], key=lambda p: p[0])
        lower = sorted(raw[le_idx:],      key=lambda p: p[0])

        return upper, lower


# ---------------------------------------------------------------------------
# P3-03  plot_cp_dist
# ---------------------------------------------------------------------------

def plot_cp_dist(
    naca_code: str,
    alpha: float,
    reynolds: float,
    mach: float = 0.0,
    n_crit: float = 9.0,
) -> str:
    """Cp distribution at one angle of attack.

    Upper and lower surface Cp curves are plotted separately with the
    region between them filled (shading highlights the lift-producing
    pressure difference).  Y-axis is inverted so suction (negative Cp)
    points upward — the aerodynamic convention.

    On XFOIL failure (binary missing, convergence failure, etc.) the
    function returns a placeholder PNG rather than raising so it never
    blocks the PerformanceCard render.

    Parameters
    ----------
    naca_code : str
        4-digit NACA code, e.g. '0012'.
    alpha : float
        Angle of attack in degrees.
    reynolds : float
        Reynolds number.
    mach : float
        Mach number (default 0.0).
    n_crit : float
        Transition criterion (default 9.0).

    Returns
    -------
    str
        Base64-encoded PNG.
    """
    fig, ax = _make_fig(figsize=(5.8, 3.4))
    title = (
        f"Cp distribution  —  NACA {naca_code}  "
        f"α={alpha:.1f}°  Re={reynolds/1e6:.2f}M"
    )

    try:
        upper, lower = _run_xfoil_cp(naca_code, alpha, reynolds, mach, n_crit)

        x_u = [p[0] for p in upper]
        cp_u = [p[1] for p in upper]
        x_l = [p[0] for p in lower]
        cp_l = [p[1] for p in lower]

        ax.plot(x_u, cp_u, color=_ACCENT,  linewidth=1.6, label="Upper surface")
        ax.plot(x_l, cp_l, color=_ORANGE, linewidth=1.6, label="Lower surface")

        # Fill between surfaces on a common x-grid (best-effort)
        try:
            import numpy as np
            all_x = sorted(set(x_u + x_l))
            cp_u_interp = np.interp(all_x, x_u, cp_u)
            cp_l_interp = np.interp(all_x, x_l, cp_l)
            ax.fill_between(all_x, cp_u_interp, cp_l_interp,
                            alpha=0.10, color=_FILL, label="ΔCp")
        except Exception:
            pass  # numpy interpolation best-effort

        ax.invert_yaxis()   # suction (−Cp) points upward
        ax.legend(fontsize=7.5, facecolor=_BG, edgecolor=_GRID,
                  labelcolor=_TEXT, framealpha=0.85)

    except Exception as exc:
        logger.warning("Cp plot failed for NACA %s @ α=%.1f: %s",
                       naca_code, alpha, exc)
        ax.text(
            0.5, 0.5,
            f"Cp data unavailable\n({type(exc).__name__})",
            ha="center", va="center", color="#888888",
            fontsize=9, transform=ax.transAxes,
        )

    ax.set_xlabel("x/c")
    ax.set_ylabel("−Cp  (suction up)")
    ax.set_title(title, fontsize=8.5, pad=6)

    fig.tight_layout(pad=1.2)
    return _fig_to_b64(fig)
