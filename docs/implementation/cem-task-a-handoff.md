# Task A continuation handoff

Starting snapshot: `e88c24f7843883a92663c758dec73e28a59a81e8` on
`cem-task-a-aerodynamic-verification`. A remains open. C can develop builders and
fixture-based admission against `mirum-aero-1`; this is not a completed native
qualification dependency.

## Available to C now

- Shared immutable SI geometry, operating-point, polar, load, body and structural
  contracts in `backend/cem/aero/contracts.py`.
- Explicit geometry/solver axes, CG/moment reference and symmetry conventions.
- Deterministic analytical fixtures in `backend/cem/aero/fixtures.py` and
  `examples/cem/aero-contract-fixture.json`.
- Pinned native build/repair script, bounded adapters, independent coupled
  calculation, raw-artifact replay and paired comparison reporting.
- Detailed implementation and limits in `cem-aerodynamic-verification.md`.

Tailless-swept, canard and tandem are contract identities only. Their native
admission remains disabled. C owns their builders, control/reflex representation,
family trim/stability, interaction and stall-order qualification. A's attached
rigid model cannot establish those by relabelling conventional surfaces.

## Evidence at this starting snapshot

The combined CEM/glider/UI/native suite passed 290 tests. The final targeted suite
passed 31 tests after the additional independent mass-limit check. Solver builds,
`pip check`, source compilation, patch application and diff checks passed.

The exact Debian XFOIL SIGFPE was reproduced and diagnosed in headless graphics
initialization. The repaired source build retains floating-point traps. Native
NACA 0012 panel refinement and finite-wing AVL references pass.

The 16-evaluation HTTP example admits eight shaped elites. Its frozen-hardware
pair has a +0.13605958335011792 m **primary screening** difference. Legacy station
XFOIL checks complete with the repair, but both AVL arms reject spiral stability.
Both independent polar grids have missing rows. Result: `inconclusive`, no
verified gain, comparison bundle HTTP 404.

These are not passing whole-aircraft/family qualification cases. Stable controlled
fixtures and passing individual solver diagnostics do not replace one.

## Next A qualification work, in order

1. Freeze a reproducible conventional-aircraft positive native reference with
   documented geometry, hardware, mass/CG, atmosphere and operating envelope.
   Diagnose numerical solver excursions separately from genuinely unsupported
   station conditions. Do not move hardware inside the equilibrium solve.
2. Record native polar gaps explicitly by foil, Re, angle and transition. Test
   bounded initialization/continuation sequences and qualify only complete
   coverage actually required by the calculation. Do not interpolate through
   missing angles, extrapolate, relax stability or manufacture coefficients.
3. Check force/moment closure, local operating conditions, viscous/induced/body
   drag accounting, coupled equilibrium and numerical refinement against native
   references. Add a related unstable/untrimmable rejection case.
4. Re-run the existing frozen pair. Its spiral rejection remains a rejection if
   the evidence still violates the mission limits. An unsuccessful design is a
   legitimate outcome, not a reason to relax admission.
5. Extend qualification to the existing T- and V-tail envelopes individually,
   then give C explicit qualified scope and remaining body/interference/stall
   limitations. Physical-family admission also needs B's applicable evidence.

## Coordination boundary while C runs

A's continuation owns new qualification modules, native references and the
aerodynamic solver/polar adapters. Preserve the published contract and fixture
interfaces unless an explicitly documented compatible addition is necessary.
Keep new family builders and their admission gates in C's work. Shared-file
integration changes must be reviewed sequentially. Do not silently merge C,
enable its families or modify deployment branches.

## Reproduce in this workspace

Worktree: `/workspace/work/mirum-a`. Python: `/workspace/.venv-glider/bin/python`.
Rebuilt binaries live under `work/native-rebuild/`; setup is reproducible with
`scripts/cem/build_native_aero.py` and its generated build manifest.

```sh
export MIRUM_XFOIL_BIN="$PWD/work/native-rebuild/Xfoil/bin/xfoil"
export MIRUM_AVL_BIN="$PWD/work/native-rebuild/avl-build/src/avl"
export PATH="$(dirname "$MIRUM_XFOIL_BIN"):$(dirname "$MIRUM_AVL_BIN"):$PATH"
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest \
  backend/tests/test_cem* backend/tests/test_glider* frontend/test_cem_ui.py \
  analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Existing raw local evidence is under `work/http/` and `work/native/`; these scratch
paths are not portable proof on another machine. Native reference inputs,
expected tolerances and qualified/rejected outputs must be retained in the next
handoff, with executable/raw-artifact identities. Deployment, environment
publication, fresh-task restoration and empirical flight validation remain open.
