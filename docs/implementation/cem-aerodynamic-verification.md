# Task A implementation and qualification record

Implementation plan (in dependency order):

1. Version the shared installed-geometry, operating-point, polar, load, body,
   structural-load-case and family-builder contracts, with deterministic fixtures.
2. Reproduce the Debian XFOIL failure, retain raw evidence and diagnose a native
   source build. Pin source, compiler flags, transition and executable identities.
3. Implement bounded polar coverage and an independent nonlinear lifting-line
   calculation. Couple local induced velocity, Reynolds number, lift, profile
   drag and section moments; solve fixed-hardware glide equilibrium.
4. Check native AVL geometry, derivatives, loads, axis conventions, conservation
   and refinement. Keep unstable cases rejected.
5. Integrate independent results and the numerical gain criterion into paired
   comparisons, including parent-side reconstruction and API/UI presentation.
6. Run deterministic/adversarial, native-reference and end-to-end checks. Record
   qualification failures and unsupported envelopes explicitly.

Numerical verification is not empirical flight validation. Existing primary
screening remains available. Native executable success alone cannot qualify a
whole-aircraft gain. Manufacturing acceptance remains separate.

## Implemented interface

The existing `POST /cem/glider/compare-shape` request is unchanged. With
`independent_recheck=true`, a comparison now contains both the legacy executable
diagnostics and `independent_performance`. The latter has its own two-arm
admission, objective difference, error criterion, native-reference results,
context bindings and bounded raw-artifact inventory. Streamlit displays the
independent state and rejected-arm reasons. It does not turn diagnostics into a
gain claim. A comparison still cannot deliver a design bundle.

`backend/cem/aero/` owns the new adapters and calculations. The existing worker
and API parent call them through `glider/comparison.py`; neither needs a new
endpoint or provider credential. Primary discovery, its budget and its objective
remain screening outputs. The independent comparison uses a separate objective
and never substitutes it into the original archive.

## Shared contracts for B and C

`contracts.py` defines `mirum-aero-1`: installed geometry and reference quantities,
station polars and transition controls, operating conditions, lifting loads and
local induced velocities, body increments, structural load cases, installed
material/support inventories and the family-builder protocol.

- Length, area, mass, force and moment use m, m², kg, N and Nm. Inertia is kg m².
- Geometry, dimensional forces and moments use right-handed x aft, y starboard,
  z up. Positive My is nose-up. Moments are about the recorded installed CG.
- Angles use degrees; derivatives use radians. Rate derivatives use `pb/(2V)`,
  `qc/(2V)`, `rb/(2V)`. The current equilibrium supports zero beta/rates only.
- Symmetric surfaces contain the positive-y half and an explicit mirror flag.
  Body stations define piecewise conical circular segments for this adapter.
- Sref, Cref and Bref are explicit. Each strip carries its own chord, area,
  section-plane velocity, Reynolds number, effective angle, force and moment.
  The induced-velocity vector retains all three geometry-axis components.
- Structural load factors multiply the recorded steady loads. These are rigid
  lifting-surface loads, not gust, landing, inertia, joint or torsional closure.
  B must add those mechanisms and prove its installed material/support path.
- Geometry, hardware/placement/rod, full mission, process/material law,
  calibration and implementation each have separate SHA-256 bindings. Missing,
  nonfinite, stale, duplicate or nonconserving data is rejected.
- The contract allows C's planned tailless-swept, canard and tandem identities.
  The numerical adapter still rejects those families. A valid contract does not
  enable geometry, numerical or physical admission for a new family.

`examples/cem/aero-contract-fixture.json` and `fixtures.py` provide a uniform
one-newton wing load, a declared wing/spar attachment and analytic half-wing
reaction/bending references: 0.5 N and 0.125 Nm. Zero artifact and executable
hashes are fixture sentinels. These fixtures are not native evidence, measured
material closure or manufacturing acceptance.

## XFOIL runtime diagnosis and reproducible setup

The exact Debian `6.99.dfsg+1-3+b1` executable has SHA-256
`b83d843c142f19e465f9cf3cd1ecde3581c12592ab0490579236ad409c44dfe0`.
It reproduced SIGFPE both for NACA 2412 at Re 100,000 and for the archived
control's actual first station: NACA 1306, chord 0.13104745969745088 m,
Re 50974.526987082, center alpha 5.18 degrees, with the unchanged legacy
five-angle command stream. The latter belongs to elite `58d44bfcb883d41d`.

A debug build retaining Debian's floating-point traps resolves the stack to
`plotlib/plt_base.f:686`, `GETWINSIZE`, called from `PLTINI` during the OPER
sweep. In headless mode `G_SCALE` has not been initialized by a graphics window;
the size query divides by it even though `PLOP / G F` disabled graphics. This is
a graphics/runtime defect, not aerodynamic nonconvergence.

The small patch in `scripts/cem/native/xfoil-headless.patch` returns the page size
when there is no graphics window. It changes no aerodynamic equation and retains
invalid/zero floating-point traps, bounds checks and the iteration limit.
The same NACA 2412 sweep then completed all five points. Genuine missing polar
angles remain nonconvergence; runtime SIGFPE, timeout, missing executable and
missing output have distinct states and retained input/log identities.

On Linux with C/Fortran compilers, X11 development files, curl, git, patch, make
and CMake installed:

```sh
python scripts/cem/build_native_aero.py --directory work/native-aero
export MIRUM_XFOIL_BIN="$PWD/work/native-aero/Xfoil/bin/xfoil"
export MIRUM_AVL_BIN="$PWD/work/native-aero/avl-build/src/avl"
```

The script hash-checks the XFOIL source and Debian patch archives, applies the
headless patch, and builds AVL 3.32 from
`RobotLocomotion/avl@b67837fa737afdf7a56655b044242619246b3ada` in double precision.
It records source, patch, compiler/version, flags and executable hashes in
`build-manifest.json`. It performs no system install. Both upstream solvers are
GPL software; their downloaded source includes their license notices.

The new polar adapter explicitly sets Ncrit 9 and transition limits 1/1, warms
the boundary layer at zero alpha before accumulation, then requests each angle
individually. Every requested row must converge. It supports NACA four-digit
sections, incompressible flow, Re 30,000–1,000,000 and bounded angle/panel counts.
It interpolates only between complete grids at consistent transition/Mach
settings, linearly in alpha and log(Re). No extrapolation or interpolation across
missing rows is admitted. Non-positive local lift slope is outside the attached
envelope. A shared executable hash does not make missing station coverage valid.

Each native invocation has a 20-second limit and a 1 MiB file limit; the paired
worker retains its existing 1,800-second/3 GiB overall cap. Native subprocesses
receive no provider credentials. Inputs, outputs and logs remain hash-bound even
for rejected runs.

## Independent calculation and scope

The new calculation does not call `_aero`, AeroBuildup or NeuralFoil. Nonlinear
horseshoe lifting lines couple every installed surface's circulation to local
induced velocity, section-plane incidence, speed/Re and native XFOIL coefficients.
The station lift residual must be below 1e-6. Profile drag and section pitching
moments come from the polars; induced drag comes from the circulating loads.
Their sum is not added to a pre-existing primary drag estimate.

The body adapter independently calculates skin friction/form-factor and base
drag, cylinder crossflow and the Munk pitching couple. Forces and moments include
the actual CG lever arms. Integration of each piecewise conical segment preserves
force and first moment under subdivision. Applicability is deliberately narrow:
circular sections, fineness 6–30, length Re 100,000–3,000,000 and |alpha| ≤ 8°.
Slabs, cavities, exposed hardware, separated flows, junction/interference flow and
body rate damping are not qualified. Transition ambiguity and a body-drag
allowance are reported; they are not measured flight uncertainty.

Speed, alpha and descent angle solve `L=W cos(gamma)`, `D=W sin(gamma)` and zero
pitching moment with dimensionless residuals below 1e-4. Unresolved side force,
roll/yaw moment, mission mass/dimensions, speed limits, uncovered polar queries
and nonconvergence reject admission. Incidence, placement, ballast, payload and
the installed rod never change during this solve. The reported trajectory uses
the pinned launch, wind and uplift semantics; unbounded sustained flight is not
converted into a finite objective.

AVL independently receives the installed camber, twist, dihedral, symmetry and
CG. Element forces reconstruct dimensional geometry-axis loads; their coefficient
sum must close total force/moment within 0.003, accounting for report rounding.
AVL's `cmLE` changes sign on YDUP surfaces and is converted explicitly. The `ai`
strip column is far-wake downwash, not local effective incidence. The coupled
solver calculates its own local induced velocity instead.

AVL refinement uses 24/8 and 48/12 span/chord resolution. Forces must change by
at most max(0.0005, 2%); derivatives by max(0.002, 5%). Native and independent
lift must agree within max(0.02, 10%), pitch coefficient within 0.02 and induced
drag within max(0.001, 15%). Body pitch/lift and destabilizing yaw increments enter
the static/lateral checks. Original roll, Dutch-roll and spiral limits remain.
Stable fixtures do not authorize accepting an unstable real design.

The lifting calculation independently refines from 12 to 24 strips per half
surface. Objective change must be ≤2%. For each admitted arm, the declared
objective error criterion adds twice that mesh difference, a 10% engineering
model allowance and the body-drag relative bound. It assumes no cancellation:
the shaped improvement must strictly exceed the **sum** of both arms' bounds.
These are bounds for the declared model comparison, not calibrated physical
confidence intervals or a universal error guarantee.

Parent admission checks current implementation/context/binary identities and the
complete raw inventory. It regenerates expected commands/geometry, reparses
successful reference and station files, reruns the coupled calculation and AVL
assessment from those raw files, and recomputes the gain. Missing evidence or a
successful diagnostic label cannot enable `verified_gain`.

## Verification and remaining qualification

Native references include NACA 0012 at Re 100,000, 160/240-panel XFOIL convergence,
and an AR-5 rectangular wing at alpha 4° with 16/8 and 32/12 AVL meshes. XFOIL's
maximum differences were 0.0016 in Cl, 0.00001 in Cd and 0.0003 in Cm. AVL's lift
coefficients were 0.27546 and 0.27547; analytic finite-wing lift, moment, symmetry
and strip closure checks passed.

Deterministic tests cover analytic finite-wing lift and mesh convergence,
inclined-glide equilibrium, exact load sums, body subdivision, structural
reactions, no primary-aero reuse, strict coverage and stale bindings, native
failure/timeout classification, provider-environment exclusion, stable/unstable
admission, raw-command tampering and missing/forged gain evidence. Fixtures and
subprocess doubles are explicitly separate from native qualification.

The real 16-evaluation, seed-42 HTTP search again admitted eight shaped elites.
Its best frozen-hardware comparison had primary screening delta
**+0.13605958335011792 m**. Both primary arms passed. With the repaired executable,
the legacy station diagnostics completed XFOIL, while both native AVL arms still
rejected **spiral divergence outside the mission envelope**. The new numerical
reference cases passed, but both independent arm polar grids had missing angles.
The result was `inconclusive`, `verified_gain=false`, and the comparison bundle
endpoint returned HTTP 404. A valid rejection was preserved.

This is a bounded independent model and fail-closed comparison implementation;
it does **not** establish a passing native whole-aircraft case for every existing
family, a new physical flight-performance gain, calibrated interference bounds,
fabrication acceptance or engineering certification. B can consume the shared
fixtures immediately; final shape-specific structural qualification still needs
an arm that passes the independent aerodynamic gates. Broader family/body/stall
envelopes require their own positive and rejected reference cases. Deployment,
environment publication and fresh-task restoration have not been performed.

Publication branch: `cem-task-a-aerodynamic-verification`, based on task commit
`cb521103a748ac8a59c4bc4bb57c0796d5b3a13a`. No deployment branch is modified by
this implementation.

The combined CEM, legacy glider, UI, AVL and airfoil regression run passed
**290 tests**, including native solver tests, with two dependency deprecation
warnings. The final aerodynamic/comparison/UI run, including the additional
independent mass-constraint regression, passed **31 tests** with one dependency
deprecation warning. `pip check`, Python compilation and `git diff --check` passed.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest \
  backend/tests/test_cem* backend/tests/test_glider* frontend/test_cem_ui.py \
  analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Put the built solver directories on PATH as well as setting the two explicit
binary variables to exercise the legacy native tests. Without native binaries,
the explicitly native tests skip; offline mathematical/contract tests still run.

## Part C readiness

C can start builder, contract and fixture-based qualification work using this
branch. Integrate A's shared contracts before changing shared orchestration
files, preserving C's own task brief. The written SI/axis conventions, geometry
and load contracts, deterministic fixtures, native build script and rejection
tests are available now. No claim that C's native qualification dependency is
complete follows from that handoff.

Start with separate geometry builders and positive/negative fixtures for each
family, exact reconstruction and revision tests, and family-specific admission
tests. Preserve explicit unsupported states during development. `lifting.solve`
currently rejects tailless-swept, canard and tandem identities; admitting a new
family requires qualification of the method, not removal of that guard alone.
The current four-digit NACA section contract also needs an explicit extension
before representing unsupported reflex sections or control deflections.

Final native qualification remains blocked on positive and unstable/untrimmable
reference cases for each family's supported envelope. In particular, C must
establish tailless reflex/control trim and stability, canard interference and
stall order, and tandem lift sharing/interference/stall behavior. The present
rigid attached-flow calculation and single-surface native references do not
establish those behaviors. Missing polar coverage and the existing designs'
native spiral rejection remain open A qualification limits. Physical family
admission additionally depends on B's applicable construction/load-path evidence.
