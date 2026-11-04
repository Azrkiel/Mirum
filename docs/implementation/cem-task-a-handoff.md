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

## Qualification continuation results

One frozen high-Re conventional whole-aircraft reference now passes native polar
panel refinement, coupled force/moment equilibrium, lifting/AVL mesh refinement,
native force/axis agreement and the unchanged mission stability limits. The
versioned input is `backend/cem/data/aero-conventional-reference-v1.json`:
0.312 kg, CG x = 0.259 m, NACA 0012, Re 230,000–350,000, prescribed rigid geometry
and inertia. Static margin is about 0.10489; predicted distance from 10 m is about
54.95684 m. No mass, incidence or placement is adjusted during the solve.

The separate aft-CG countercase (x = 0.33 m) is rejected for native pitch
instability. Its assessment is at the positive case's operating point; it claims
neither zero-moment trim nor an unstable flight equilibrium. This is a numerical
method reference, not physical material, installed-rod or flight qualification.

Covered-domain Newton backtracking now rejects invalid numerical trials without
inventing coefficients or rejecting a design merely because one trial left its
table. Failure still means no equilibrium established. Native operating-point
and standard-axis force/drag consistency are explicitly checked. The 16/6 and
24/8 AVL meshes pass the same tolerances within the retained 20-second cap; the
larger 48/12 mesh had exceeded that cap on the whole-aircraft case.

The original low-Re obstruction has a reproducible diagnostic. For NACA 0006 at
Re 30,000 and Ncrit 9, ascending/repeated sweeps miss −4°, 4°, 7.5° and 8°;
zero-centered continuation misses −4°, 4° and 7°; per-point initialization misses
7°. The 8° coefficient is sequence-sensitive. These are converged-row gaps,
separate from the repaired SIGFPE. No branch supplies a complete requested grid,
and no fallback or union of partial rows is admitted.

The combined native CEM/glider/UI suite passes **304 tests**. Qualification and
audit reports bind the current source, inputs, executable identities and raw
artifacts, and replay without executing native solvers. The shared contract and
original analytical fixture interfaces are unchanged; C's families stay disabled.

The final real HTTP rerun also passes parent evidence validation. It preserves
eight shaped elites and the +0.13605958335011792 m primary screening difference.
The new whole-aircraft reference passes inside that comparison, while both
actual arms still reject incomplete NACA 0006/Re 30,000 coverage and both legacy
AVL diagnostics retain the spiral-instability rejection. `verified_gain=false`
and comparison bundle HTTP 404 remain correct.

Current evidence identities:

- Source/runtime fingerprint:
  `27ee54426fb0e1785376cc7e2b8d7595a657f235513994b6a56321d00819153f`.
- Frozen reference input:
  `994594ce2c89c1920fb21e38adfb9e224fae2831a59b574fdb9ca7cb8cd0f05c`.
- Whole-aircraft reference evidence:
  `f2571d9d32ec1d100e3f1940ec1378a10e6a165764a955e006e7cfc383e39832`.
- Low-Re section audit evidence:
  `4316bd41a1f4fea7b4976d43f0a1f167911d469ebdaaf869a69b4d05e40273ac`.

The case's 160/240-panel differences are 0.0039 Cl, 0.00002 Cd and 0.0009 Cm.
The 12/24-strip objective difference is about 0.00490 m. The largest normalized
equilibrium residual is below 4.2e-11. The unstable countercase's native Cma is
positive (0.490627). These numbers verify the declared reference, not a broader
family, physical flight result or calibrated error guarantee.

## Remaining A qualification work, in order

1. Establish supported low-Re coverage with a justified numerical/physical method
   and explicit transition/stall envelope. The sequence audit is not a substitute
   for complete polars or a reason to extrapolate or splice inconsistent rows.
2. Add separate positive and rejected references for the existing T-/V-tail
   envelopes. The new conventional reference does not qualify these by analogy.
3. Preserve the real frozen pair's spiral rejection. A genuinely different stable
   design requires its own geometry/hardware revision and fresh evidence; never
   adjust hardware inside the comparison to rescue an arm.
4. Bound body/interference and numerical-model errors beyond the present declared
   engineering allowances before claiming broader applicability. Rigid attached
   flow does not establish stall order, junction flow or flexible construction.
5. Supply C only explicitly qualified scope. Its family-specific reflex/control,
   interference/stall and native end-to-end qualifications remain C's work;
   physical-family admission also needs B's applicable construction evidence.

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

The continuation's local evidence is retained under
`work/qualification/http-final/`, `work/qualification/frozen-reference-final/`
and `work/qualification/section-audit-final/`. The frozen-reference copy replays
successfully after relocation. These local paths are not hosted downloads; C
should reproduce the scripts against its own executable/runtime identities.

Run the added reference and sequence audit with the selected binaries:

```sh
python scripts/cem/qualify_aero.py --directory work/aero-reference
python scripts/cem/qualify_aero.py --directory work/aero-reference --replay
python scripts/cem/audit_aero_polars.py --directory work/low-re-audit
python scripts/cem/audit_aero_polars.py --directory work/low-re-audit --replay
```

The audit always withholds aircraft admission and gain claims; successful command
execution is not complete polar coverage. See the implementation notes for the
reference's precise supported scope and the distinction between audit and
qualification exit status.
