# Task B qualification handoff

## Assignment and starting state

Complete the remaining qualification work in [Task B's brief](cem-task-b.md).
Qualify one explicitly named shaped-wing construction envelope from installed
loads through delivered material and fixed-revision trim. Preserve rejection
when the evidence is incomplete. Do not broaden admission to other construction
modes or aircraft families from one passing variant.

**Integration can proceed; A and B are not fully qualified.** The B implementation
is a measured, fixture-tested prototype. It has no admitted physical multi-station
mission and establishes neither fabrication acceptance nor engineering
certification. Task C can develop builders against the shared contracts while
these qualification gates remain closed.

Starting branch: `cem-task-b-physical-construction`.
Reviewed implementation baseline:
`fc8acab816e6b8059ec5244440d19d18190d5c85`.
It already includes A's
`e88c24f7843883a92663c758dec73e28a59a81e8` through merge
`e0d9f4afaf9be8813375bb91767497c2ca2a08cb`.
Earlier B implementation commits are `d40cd0b` and `686bf42`.
These B commits were created locally; check that they are available in the next
task's checkout before starting. A commit or merge does not establish remote
publication or qualification.

Use the existing isolated checkout. Read applicable agent instructions and the
cloud runtime skill when using a cloud environment. A separate worktree is not
required. Keep A, B and C module work isolated; merge changes to shared
orchestration files sequentially and rerun checks on the integrated revision.

## What exists

| File | Implemented behavior | Qualification limit |
| --- | --- | --- |
| `backend/cem/glider/physical_loads.py` | Strict analytical distributed-load fixture; measured-section beam, root-bond, deflection and twist checks | Fixed-root Euler model and declared material priors; no full installed-aircraft qualification |
| `backend/cem/glider/physical_fixture.py` | Closed shaped foam/rod/adhesive/collar stock, canonical STL import and measurements | Original isolated fixture; not the mounted construction |
| `backend/cem/glider/attachment.py` | V2 collar-to-plate bonds, plates, rivets and local keel; measured interfaces, reaction equilibrium, pin bearing/bending and ligament checks | Rigid/equal-stiffness and nominal bond/contact assumptions; incomplete fuselage path |
| `backend/cem/glider/foam_transfer.py` | Conforming material interfaces and nominal polygon traction checks | No core elasticity, end-peel, defect or process qualification |
| `backend/cem/glider/mounted_wing.py` | Complete prototype export, mass/CG/full inertia, material partition, fresh-export catalogue sizing and CLI | Uses analytical fixture loads; all passing results remain `incomplete` |
| `backend/cem/glider/structural_adapter.py` | A's point-strip load consumer; shared material/support export and read-only reconstruction | No native qualification or aerodynamic/material correspondence claim; shared strength/sizing coupling is still required |
| `backend/cem/aero/contracts.py` | A-owned `mirum-aero-1` geometry, load, material and support contracts | Contract validity alone does not qualify numerical or physical behavior |

See [B's implementation record](cem-task-b-progress.md) for equations, assumptions
and test history, and [A's qualification record](cem-aerodynamic-verification.md)
for solver setup and remaining aerodynamic failures.

The current construction identity is
`bonded_shaped_wing_riveted_keel_prototype_v2`.
It supports only a flat, unswept, untwisted five-station NACA 0012 wing, zero
dihedral in polyhedral style, span 0.6–1.4 m, root chord 0.10–0.16 m, taper
0.7–1.0 and 2–8 mm rods, subject to stock/web/enclosure rejection. This is a
prototype parameter envelope, not a blanket qualification of those combinations.

There are **20** solid-volume material parts: eight foam panels, rod, inner
adhesive annulus, collar, two outer collar-to-foam bonds, two collar-to-plate
bonds, two bearing plates, two through-rivets and a local fuselage keel. The
shared handoff has **22** declared supports connecting all parts to the keel.
The keel is a local member, not a complete aircraft fuselage.

Two details must survive further changes:

- The plates require a **20 mm root cutback on each half-wing**. This changes
  installed lifting geometry. Uncut search-wing loads and trim cannot qualify it.
- A round collar in a plain bore cannot carry pitch torque through frictionless
  bearing. V2 explicitly models collar-to-plate adhesive annuli and their nominal
  strength. Preserve that path or replace it with separately justified geometry;
  never restore implicit friction capacity. V1 exports are stale for V2.

## Qualification work, in dependency order

### 1. Establish the actual integrated revision and native evidence

Run the existing focused checks before changing behavior. Record the checkout
SHA, implementation fingerprint, dependency versions and exact selected solver
binaries. This instance's earlier whole-suite run skipped eight native checks:
the solver binaries and Fortran/CMake build prerequisites were absent.

Use A's checked-source build procedure below or independently verified binaries.
Retain source/checksum/build manifests, executable identities, inputs and raw
outputs. Do not disable TLS, package/artifact verification, floating-point traps
or assertions to make a native run pass.

A's baseline passed numerical reference cases on its original instance, but
reported missing real-arm polar angles and native spiral-stability rejection.
Those outcomes are unresolved qualification limits, not permission to substitute
analytic polars or relax stability checks. A must provide passing evidence for
the actual geometry/operating envelope being qualified, plus rejected cases.
Passing reference wings alone does not supply that evidence.

### 2. Prove aerodynamic-to-delivered geometry correspondence

Build A's installed aerodynamic inputs from the delivered revision, including
the root cutback, actual wing stations, placements and applicable body/attachment
representation. Establish an explicit, independently checked relationship between
A's geometry payload and B's material geometry. Their hashes describe different
objects and must not simply be copied into one another.

Account for the complete flight assembly's mass, CG and inertia, including the
representation of tail, body, payload, ballast and other hardware. The CLI's
extra 50 g is a scalar load fixture and cannot stand in for those properties.
A's material schema currently carries only inertia diagonals; B retains full
tensors separately. Resolve or explicitly reject unsupported inertia coupling
before using that contract for complete-aircraft dynamics.

**Required evidence:** revision-bound geometry correspondence, exact material
inventory and laws, connected interfaces, enclosure/channel/stock proofs, and
measured mass/CG/full inertia within declared tolerances. A geometry correction
requires new exports, measurements, loads and trim.

### 3. Couple shared shape-specific loads to structural analysis and sizing

`consume_loads` already preserves A's strip point forces and all section-couple
components. It translates CG moments once:

```text
section_couple = moment_CG - (strip_center - CG) × force
moment_at_root = moment_CG + (CG - root) × force
```

Keep SI units, X aft/Y right/Z up axes, global +Y torque, explicit left/right
loads and the load factor applied exactly once. Do not smooth point strips into
an invented load distribution that changes force or first moments. Do not fold
tail forces into an elliptical total-wing lift approximation.

The adapter currently computes exact resultants; it does **not** feed A's point
loads into qualified beam/joint strength and catalogue sizing. Implement that
coupling, retaining force/couple components and load discontinuities. The
prototype beam checks cover a narrower set of components than the shared load
contract. Provide a justified path for drag/lateral and other controlling loads
or keep that operating case rejected.

Add the body, distributed material inertia, payload/rod reactions and launch,
gust or landing cases needed by the declared mission envelope. A's structural
case covers rigid steady lifting surfaces only. Record unassigned loads and
reject incomplete closure; avoid double-counting load factors or weight.

**Required evidence:** exact whole-assembly and local reactions, analytical
beam/joint references, convergence, justified section/material laws, load-case
coverage, and rejected overload/missing-support/disconnected-path cases.

### 4. Qualify controlling physical mechanisms and stiffness

Replace or substantiate the assumptions that can control the admitted envelope:

- Foam/core load spreading, crushing, normal and shear behavior.
- Adhesive end peel, stress concentration, defects, process/material variability
  and the collar-to-plate torque path.
- Collar/plate contact elasticity and attachment compliance; the current pin
  bearing distribution is a declared equilibrium model, not an elasticity solve.
- The keel's transfer into the rest of the aircraft.
- Relevant coupled bending, shear deformation, carbon anisotropy, polygon
  torsional warping, buckling, axial/head pull-through and fatigue mechanisms.

Choose bounded, justified models and reference cases with stated tolerances.
Unsupported mechanisms must stay explicit; a controlling unsupported failure
mechanism blocks admission. Current strength/modulus priors and a connected mesh
are insufficient material/process evidence. Distinguish numerical qualification,
coupon/bench evidence, fabrication acceptance and engineering certification.

If necessary material/process or external evidence is unavailable, identify the
precise blocker and finish independent implementation/reference work. Do not
invent measured strengths or describe software tests as physical tests.

### 5. Close sizing, delivered measurements and fixed-revision trim

For each size or geometry change, export again, re-import and independently
measure the actual stock/material interfaces. Recompute loads with current
measured properties and the validated operating context. Do not reuse a previous
rod's section, mass, load revision or trim result.

Rerun trim and stability for the actual delivered mass/CG/inertia and geometry,
with placement, payload, ballast and incidence fixed. A solver may not rebalance
an unbuilt design and call it delivered closure. Any correction that changes
geometry starts another export/measurement/load/trim cycle.

**Required evidence:** at least one complete positive case and bounded negative
cases for the named envelope. Keep `incomplete`, `failed`, unavailable, skipped
and passed outcomes distinct. The current default 5 mm fixture result is not
that positive qualification case.

### 6. Integrate one qualified mode through production admission

Only after the previous gates pass, make sequential shared-file changes to
requirements, discovery/model, closure/service, parent evidence reconstruction,
worker/API and UI. Production requirements currently reject physical
multi-station construction; preserve that behavior until evidence warrants a
narrowly named mode. Do not enable legacy foam modes or C's other families by
removing that guard wholesale.

The parent must reconstruct current material, load, support, native and trim
evidence. Existing `validate_structure` deliberately returns false for physical
admission even for a valid declared handoff. Changing that return or promoting a
closure label cannot replace the missing checks. Add archive/context binding,
real API/UI admission and rejected-bundle end-to-end cases.

Keep numerical qualification separate from fabrication/certification flags.
Deployment, environment publication and fresh-task restoration are separate
release checks and have not been performed here.

## Commands and expected outcomes

Run from the checkout root with the glider, backend test and frontend dependencies
installed. The current instance has `/workspace/cem-venv`; activate it if present.
Its presence in a future task must be checked. Use repository-pinned dependency
versions and a compatible isolated Python environment if it must be rebuilt.

```sh
source /workspace/cem-venv/bin/activate
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export MPLCONFIGDIR=/tmp/mirum-b-qual-mpl
export XDG_CACHE_HOME=/tmp/mirum-b-qual-cache
```

Focused B implementation and contract checks:

```sh
python -m pytest \
  backend/tests/test_cem_physical.py \
  backend/tests/test_cem_attachment.py \
  backend/tests/test_cem_foam_transfer.py \
  backend/tests/test_cem_mounted_wing.py \
  backend/tests/test_cem_structural_adapter.py -q
```

Real delivered-section sizing diagnostic, using a new empty output directory:

```sh
PYTHONPATH=backend python -m cem.glider.mounted_wing \
  --output /tmp/mirum-b-qualification-sizing
```

Exit **3 means incomplete**, **2 means rejected**. Do not convert either into an
acceptance claim. Inspect `result.json` and each attempted section's
`manifest.json`, `loads.json` and `measurement.json`. The baseline V2 default
rejected 2/3/4 mm rods on beam checks and selected 5 mm for the supported fixture
checks, at about 277.39 g loaded mass including the declared extra 50 g. It
retained `incomplete`. The current run must supply its own evidence.

Native setup, once C/Fortran compilers, X11 development files, curl, git, patch,
make and CMake are available:

```sh
b_qual_native_dir=/tmp/mirum-b-native
python scripts/cem/build_native_aero.py --directory "$b_qual_native_dir"
export MIRUM_XFOIL_BIN="$b_qual_native_dir/Xfoil/bin/xfoil"
export MIRUM_AVL_BIN="$b_qual_native_dir/avl-build/src/avl"
export PATH="$b_qual_native_dir/Xfoil/bin:$b_qual_native_dir/avl-build/src:$PATH"
python -m pytest backend/tests/test_cem_aero.py -q
```

The build directory must be new; existing native sources are preserved by the
builder. Inspect reference results and retained raw evidence. A native skip,
timeout, missing polar angle or stability rejection remains unresolved.

Combined integration regression command:

```sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Historical evidence: the combined run completed **368 passed, 8 native checks
skipped**, with two dependency warnings. After a final reporting correction and
an added pitch-bond overload reference, **49 targeted checks passed**. These are
recorded implementation results, not a promise of the next run's test count or
qualification. `uv pip check` and `git diff --check` also passed. Preserve test
exit status when capturing logs and record actual new counts and skips.

## Evidence package and completion report

Keep generated meshes, native builds and raw run outputs outside the checkout.
Preserve a reviewable evidence package before temporary paths are lost; record
its location and retention. A local `/tmp` path alone is not durable handoff.
Commit concise reports and identities, not bulky STL files or credentials.

For each claimed qualified envelope record:

| Evidence | Minimum contents |
| --- | --- |
| Revision/context | Checkout SHA; implementation, geometry, hardware, mission, process, calibration and operating identities |
| Native/load evidence | Executable/build/raw artifact identities, reference results, actual shape/condition coverage and complete applied load cases |
| Material/support evidence | Canonical manifest and material laws; STL hashes; measured mass/CG/full inertia; interface/channel/enclosure proofs; physical models and material/process sources |
| Mechanics | All controlling mechanisms, reaction equilibrium, analytical/reference comparisons, convergence, strength and stiffness limits |
| Closure | Sizing history, fresh exports and measurements for every revision, fixed-geometry delivered trim/stability and declared tolerances |
| Admission | Parent reconstruction plus positive and adversarial archive/API/UI cases for exactly the named envelope |
| Outstanding work | Failed, skipped and unsupported checks; qualification, fabrication/certification and release status separately |

The final handoff must say which gates actually passed, what remains blocked and
what exact external evidence or implementation is needed next. C's readiness to
start and a green regression suite do not imply A/B physical qualification.
