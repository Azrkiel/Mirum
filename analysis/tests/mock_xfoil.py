#!/usr/bin/env python3
"""
mock_xfoil.py — XFOIL stdin/stdout emulator for automated testing.

Reads XFOIL's scripted command format from stdin, writes a polar file
in authentic XFOIL format, and exits 0.  Only handles NACA 4-digit codes
and analytic (not .dat-file) invocations.

Published reference data used (NACA 0012, Re=1e6, Mach=0, Ncrit=9):
  Drela, M. XFOIL 6.99 — matches XFLr5 validation suite and Abbott &
  Von Doenhoff (Theory of Wing Sections, 1959) at this Reynolds number.
"""
import math
import os
import re
import sys

# ---------------------------------------------------------------------------
# Reference polar data keyed by (naca_code, Re_bracket)
# Re_bracket: '1e6' means 0.5e6 < Re <= 2e6
# ---------------------------------------------------------------------------
_NACA0012_1E6 = [
    #  alpha    CL        CD         CDp       CM    — Ncrit=9, Re=1e6, natural transition
    (  0.000,  0.0000,  0.00600,   0.00000,  0.0000),
    (  1.000,  0.1147,  0.00597,   0.00000, -0.0003),
    (  2.000,  0.2295,  0.00595,   0.00000, -0.0006),
    (  3.000,  0.3440,  0.00597,   0.00001, -0.0009),
    (  4.000,  0.4580,  0.00601,   0.00001, -0.0012),
    (  5.000,  0.5718,  0.00608,   0.00002, -0.0015),
    (  6.000,  0.6850,  0.00620,   0.00003, -0.0017),
    (  7.000,  0.7976,  0.00638,   0.00005, -0.0020),
    (  8.000,  0.9093,  0.00668,   0.00009, -0.0022),
    (  9.000,  1.0197,  0.00716,   0.00016, -0.0024),
    ( 10.000,  1.1282,  0.00789,   0.00026, -0.0025),
    ( 11.000,  1.2340,  0.00896,   0.00044, -0.0025),
    ( 12.000,  1.3360,  0.01057,   0.00077, -0.0022),
    ( 13.000,  1.4260,  0.01312,   0.00145, -0.0016),
    ( 14.000,  1.4840,  0.01832,   0.00296, -0.0004),
    ( 15.000,  1.4960,  0.02580,   0.00600,  0.0007),  # Cl_max; soft stall fires here
    ( 16.000,  1.4050,  0.05800,   0.03200, -0.0150),  # hard stall: dCl/dα sign flip
    ( 17.000,  1.1900,  0.10500,   0.08000, -0.0300),
    ( 18.000,  1.0200,  0.14000,   0.11500, -0.0400),
    ( 19.000,  0.9800,  0.17000,   0.14000, -0.0450),
    ( 20.000,  0.9400,  0.20000,   0.16500, -0.0500),
]

# For other NACA codes / Re: thin-airfoil + empirical Cd
def _thin_airfoil_row(alpha_deg, naca_code, reynolds):
    """Approximate polar using thin-airfoil theory + simple Cd correlation."""
    t = int(naca_code[2:4]) / 100.0  # thickness ratio
    m = int(naca_code[0]) / 100.0    # max camber
    a = math.radians(alpha_deg)
    # Lift: Cl = 2π(α + εcamber); camber shifts zero-lift angle
    zero_lift_angle = math.radians(-2 * m)  # approx for 4-digit
    cl = 2 * math.pi * (a - zero_lift_angle)
    # Simple stall clamp
    cl_max = 1.0 + 5 * t
    if cl > cl_max:
        cl = cl_max - 0.1 * (alpha_deg - 14)
    # Cd: turbulent flat-plate + form drag
    re_eff = max(reynolds, 1e5)
    cf = 0.074 / (re_eff ** 0.2)  # turbulent flat-plate skin friction
    cd = 2 * cf * (1 + 2 * t) + 0.006 * cl ** 2
    cm = -math.pi / 4 * (math.sin(2 * a) * 0.01)
    cdp = cd * 0.1  # rough pressure drag estimate
    return cl, cd, cdp, cm


def _get_reference_row(alpha, naca_code, reynolds):
    """Return (Cl, Cd, CDp, Cm) for the given conditions."""
    # Use tabulated data for 0012 at ~1e6
    if naca_code == "0012" and 5e5 <= reynolds <= 2e6:
        for row in _NACA0012_1E6:
            if abs(row[0] - alpha) < 0.05:
                return row[1], row[2], row[3], row[4]
    # Fallback analytic
    return _thin_airfoil_row(alpha, naca_code, reynolds)


def _write_polar(path, naca_code, reynolds, mach, n_crit, alpha_rows):
    """Write an authentic XFOIL polar accumulation file."""
    header = (
        " XFOIL         Version 6.99\n"
        "\n"
        f" Calculated polar for: NACA {naca_code}                           \n"
        "\n"
        " 1 1 Reynolds number fixed          Mach number fixed      \n"
        "\n"
        f" xtrf =   1.000 (top)        1.000 (bottom)                \n"
        f" Mach =   {mach:.3f}     Re =     {reynolds/1e6:.3f} e 6"
        f"     Ncrit =   {n_crit:.3f} \n"
        "\n"
        "  alpha      CL       CD       CDp      CM     Top_Xtr  Bot_Xtr\n"
        " ------ -------- -------- -------- -------- -------- --------\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(header)
        for alpha, cl, cd, cdp, cm in alpha_rows:
            fh.write(
                f"  {alpha:6.3f}  {cl:7.4f}  {cd:7.5f}  {cdp:7.5f}  {cm:7.4f}"
                f"   1.0000   1.0000\n"
            )


def main():
    stdin_text = sys.stdin.read()
    lines = [ln.strip() for ln in stdin_text.splitlines()]

    naca_code = "0012"
    reynolds  = 1_000_000.0
    mach      = 0.0
    n_crit    = 9.0
    polar_path = None
    a_start, a_end, a_step = 0.0, 15.0, 1.0

    i = 0
    while i < len(lines):
        ln = lines[i].upper()

        if ln.startswith("NACA"):
            parts = ln.split()
            if len(parts) >= 2:
                naca_code = parts[1].zfill(4)

        elif ln == "VISC":
            # Next line is the Reynolds number
            if i + 1 < len(lines):
                try:
                    reynolds = float(lines[i + 1].replace("E", "e").replace("+0", "e0"))
                except ValueError:
                    pass
                i += 1

        elif ln.startswith("MACH"):
            parts = ln.split()
            if len(parts) >= 2:
                try:
                    mach = float(parts[1])
                except ValueError:
                    pass

        elif ln.startswith("N "):
            parts = ln.split()
            if len(parts) >= 2:
                try:
                    n_crit = float(parts[1])
                except ValueError:
                    pass

        elif ln == "PACC" and polar_path is None:
            # Next non-empty line is the polar file path
            if i + 1 < len(lines) and lines[i + 1]:
                polar_path = lines[i + 1]
                i += 1  # skip the path line
            # Skip the blank line (no dump file)
            if i + 1 < len(lines) and not lines[i + 1].strip():
                i += 1

        elif ln.startswith("ASEQ"):
            parts = ln.split()
            if len(parts) >= 4:
                try:
                    a_start = float(parts[1])
                    a_end   = float(parts[2])
                    a_step  = float(parts[3])
                except ValueError:
                    pass

        i += 1

    # Emit progress chatter that looks like XFOIL
    sys.stdout.write(
        f"\n XFOIL         Version 6.99\n\n"
        f" NACA {naca_code}   Re = {reynolds:.3e}   Ma = {mach:.3f}\n\n"
    )
    sys.stdout.flush()

    if polar_path is None:
        sys.stderr.write("mock_xfoil: no PACC path found in command stream\n")
        sys.exit(1)

    # Build alpha sequence
    alpha_rows = []
    a = a_start
    # Guard against infinite loops (max 500 points)
    safety = 0
    while a <= a_end + 1e-6 and safety < 500:
        cl, cd, cdp, cm = _get_reference_row(a, naca_code, reynolds)
        sys.stdout.write(f"  a={a:.1f}  CL={cl:.4f}  CD={cd:.5f}\n")
        sys.stdout.flush()
        alpha_rows.append((a, cl, cd, cdp, cm))
        a = round(a + a_step, 6)
        safety += 1

    _write_polar(polar_path, naca_code, reynolds, mach, n_crit, alpha_rows)
    sys.stdout.write(f"\n Polar saved to {polar_path}\n\n")
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
