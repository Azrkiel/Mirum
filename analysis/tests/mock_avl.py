#!/usr/bin/env python3
"""
mock_avl.py — AVL stdin/stdout emulator for automated testing.

Mirrors the role of ``mock_xfoil.py`` for Athena Vortex Lattice (AVL).  Reads
AVL's scripted command stream from stdin, and when the command sequence asks
for the total-forces / stability output to be written to a file (the
``ST <file>`` command inside the OPER sub-menu), writes a realistic canned AVL
``.st`` stability block to that path and exits 0.

The geometry / run-case files referenced by ``LOAD`` / ``CASE`` are NOT parsed
in detail — the mock only needs to (a) discover the output filename and (b)
emit a forces block whose numbers parse cleanly into the avl.py TypedDict.

Reference numbers (a representative AR≈8 rectangular wing at α=5°):
  CLtot ≈ 0.45, CDind ≈ 0.0085, e (Oswald) ≈ 0.96, plus the usual
  stability derivatives (CLa, Cma, CLq, ...) and a control derivative when an
  elevator/aileron control was declared.

AVL total-forces (.st) format reference: Drela & Youngren, AVL 3.36 User
Primer (the ``ST`` command output block).
"""
import os
import re
import sys


# A faithful slice of an AVL ``ST`` (stability-axis total forces) report.
# Only the fields avl.py parses need to be exactly formatted; the rest mimics
# the real layout so the parser is exercised against realistic noise.
_ST_BLOCK = """\
 ---------------------------------------------------------------
 Vortex Lattice Output -- Total Forces

 Configuration: Mirum wing
     # Surfaces =   1
     # Strips   =  20
     # Vortices = 200

  Sref =  8.0000       Cref =  1.0000       Bref =  8.0000
  Xref =  0.2500       Yref =  0.0000       Zref =  0.0000

 Standard axis orientation,  X fwd, Z down

 Run case:  mirum-case

  Alpha =   5.00000     pb/2V =  -0.00000     p'b/2V =  -0.00000
  Beta  =   0.00000     qc/2V =   0.00000
  Mach  =     0.000     rb/2V =  -0.00000     r'b/2V =  -0.00000

  CXtot =   0.00123     Cltot =   0.00000     Cl'tot =   0.00000
  CYtot =   0.00000     Cmtot =  -0.08210
  CZtot =  -0.45120     Cntot =   0.00000     Cn'tot =   0.00000

  CLtot =   0.45120
  CDtot =   0.00854
  CDvis =   0.00000     CDind =   0.00854
  CLff  =   0.45050     CDff  =   0.00842    | Trefftz
  CYff  =   0.00000         e  =   0.9600    | Plane

   elevator        =   0.00000

 ---------------------------------------------------------------

 Stability-axis derivatives...

                             alpha                beta
                  ----------------    ----------------
 z' force CL |    CLa =   5.123456    CLb =   0.000000
 y  force CY |    CYa =   0.000000    CYb =  -0.250000
 x' mom.  Cl'|    Cla =   0.000000    Clb =  -0.061000
 y  mom.  Cm |    Cma =  -0.812000    Cmb =   0.000000
 z' mom.  Cn'|    Cna =   0.000000    Cnb =   0.061000

                     roll rate  p'      pitch rate  q'      yaw rate  r'
                  ----------------    ----------------    ----------------
 z' force CL |    CLp =   0.000000    CLq =   7.512000    CLr =   0.000000
 y  force CY |    CYp =   0.121000    CYq =   0.000000    CYr =   0.211000
 x' mom.  Cl'|    Clp =  -0.452000    Clq =   0.000000    Clr =   0.105000
 y  mom.  Cm |    Cmp =   0.000000    Cmq = -11.230000    Cmr =   0.000000
 z' mom.  Cn'|    Cnp =  -0.051000    Cnq =   0.000000    Cnr =  -0.071000

                       elevator     d1
                  ----------------
 z' force CL |   CLd1 =   0.011500
 y  force CY |   CYd1 =   0.000000
 x' mom.  Cl'|   Cld1 =   0.000000
 y  mom.  Cm |   Cmd1 =  -0.024500
 z' mom.  Cn'|   Cnd1 =   0.000000

 Trefftz drag: CDff =   0.00842
 Oswald efficiency:    e  =   0.9600
 ---------------------------------------------------------------
"""


def _find_output_path(lines):
    """Scan the AVL command stream for the ``ST <file>`` total-forces write.

    AVL's OPER sub-menu writes the stability report with the two-token command
    ``ST`` followed (on the same or next non-empty line) by the output filename.
    We also accept ``FT`` (total-forces) as an alias.
    """
    for i, raw in enumerate(lines):
        toks = raw.strip().split()
        if not toks:
            continue
        head = toks[0].upper()
        if head in ("ST", "FT"):
            # Filename may be the second token on this line ...
            if len(toks) >= 2:
                return toks[1]
            # ... or the next non-empty line.
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    return lines[j].strip()
    return None


def main():
    stdin_text = sys.stdin.read()
    lines = stdin_text.splitlines()

    out_path = _find_output_path(lines)

    # Emit AVL-like banner chatter on stdout.
    sys.stdout.write(
        " ===================================================\n"
        "  Athena Vortex Lattice  Program      Version  3.36\n"
        "  Copyright (C) 2002   Mark Drela, Harold Youngren\n"
        " ===================================================\n\n"
    )
    sys.stdout.flush()

    if out_path is None:
        sys.stderr.write("mock_avl: no ST/FT output path found in command stream\n")
        sys.exit(1)

    # AVL appends; mock truncates for determinism.
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(_ST_BLOCK)

    sys.stdout.write(f" Stability-axis derivatives written to {out_path}\n")
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
