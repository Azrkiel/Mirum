# Task B implementation status

This is a fixture-based prototype, **not completion or qualification of Task B**.
The requested task is [the Task B brief](cem-task-b.md). At the initial inspection
Task A contained only a brief. Its later `e88c24f` implementation now supplies
shared contracts and fixtures; actual shape-specific native qualification is
still incomplete. No physical construction has
been admitted through requirements, discovery, parent validation or the UI.

## Implemented independently of A

`backend/cem/glider/physical_loads.py` supplies a provisional, strict, versioned
fixture adapter for B. A still owns the first shared-contract merge. Inputs bind
the geometry and operating revisions independently using full SHA256 identities.
The source enum accepts only `analytical_fixture`; changing a label cannot turn
these loads into aerodynamic evidence. Stations are immutable tuples, finite,
ordered, bounded to 129 points and required to start at the root.

The right half-wing uses installed X aft, Y right, Z up axes. Loads are piecewise
linear vertical force per span and spanwise pitching torque per span, in SI.
Loads include any desired load factors already: no elliptical distribution,
implicit factor, automatic mirroring or aircraft-weight substitution is used.
Reactions balance the signed applied force and first moments. Strength and
stiffness bounds integrate absolute loads, split at sign changes, so cancellation
of root force cannot conceal an outboard overload. Four-point Gauss quadrature
splits at every load knot; the supported constant-section integrands are
polynomials, with analytical and subdivision-convergence references in tests.

The supported mechanics are constant-section Euler bending, conservative direct
shear and circular-section torsion screening, tip deflection/twist, and root
bond shear under combined bending, shear and torque. All section properties
come from measured delivered material. Moduli, strengths, safety factors and
stiffness limits are explicit inputs. These models assume perfect adhesion and
a fixed fixture. They do not qualify foam transfer, peel, buckling, fatigue,
polygon torsional warping or a fuselage attachment. Those omissions are included
in every analysis result, even when its supported checks pass.

`backend/cem/glider/physical_fixture.py` defines exactly one prototype:
`bonded_straight_shaped_wing_fixture_v1`. Its envelope is a five-station flat,
unswept, untwisted NACA 0012 wing with polyhedral style at zero dihedral,
0.6–1.4 m span, 0.10–0.16 m root chord, taper 0.7–1.0 and 2–8 mm rod diameter.
The existing bounded chord factors apply. Geometry outside the bore enclosure
or 20 mm foam-stock limit is rejected; those parameter bounds do not guarantee
every combination can be constructed.

The exact inventory is eight solid foam panels, a full-span carbon rod, a
full-span adhesive annulus and a central fixture sleeve. The sleeve replaces
foam in the two centre panels, extends to the first shape station and transfers
fixture reactions through the adhesive. It is not an installed fuselage mount.
Regular 64-gon mating surfaces coincide even when foam-cap topology inserts
extra vertices; the bore vertices lie on polygon edges rather than a different
circle approximation. This prevents small geometric gaps from being called a
bond. Density priors are 35, 1600, 1100 and 1850 kg/m³ for foam, rod, adhesive
and sleeve respectively. Adhesive allowable shear is an assumed 8 MPa before
the support safety factor, not coupon-test evidence.

Exports are binary STL in millimetres; analysis uses metres. Verification
reconstructs the requested revision, requires the exact inventory and material
laws, checks paths and hashes, imports closed connected material, and compares
complete triangles with the canonical surfaces at 0.2 micrometre tolerance.
It independently measures volumes, mass, CG, full inertia tensor, rod/bond/sleeve
sections and actual support overlap. Sum of material volumes must match the
station-loft envelope within 1e-5 relative tolerance. Geometry and measured
mechanics can be exercised together, but fixture closure always returns
`state="incomplete"`, `manufacturing_ready=false`, `engineering_certified=false`.

Run from the repository root with the glider dependencies and pytest installed:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest \
  backend/tests/test_cem_physical.py -q
```

The real-export fixture in that test demonstrates `construct`, `export_fixture`,
`verify_fixture`, and the measured-input mechanics call. Outputs use pytest's
temporary directories; meshes are never added to the checkout. Export refuses
to overwrite a nonempty directory. Rejection cases include overloads, a short
root bond, missing support, invalid/nonfinite inputs, missing/duplicate parts,
wrong material laws, stale geometry/operating revisions, altered hashes, and a
shifted rod even after its artifact hash is refreshed. Existing requirements
continue rejecting physical multi-station missions, including the fixture name.

## Remaining work required by the brief

1. Integrate A's shared installed-material/support/load-case contract and adapt this
   prototype to it. Preserve numerical axes, identity, operating-condition and
   raw-evidence bindings; fixture loads cannot satisfy aerodynamic qualification.
2. Design an actual fuselage attachment, its material parts and interfaces;
   establish load transfer from aerodynamic wing surfaces through foam, bond,
   spar and attachment. Qualify foam stress, bond peel and mount compliance or
   retain explicit rejection where they control the load path. A fixture sleeve
   is insufficient evidence for those mechanisms.
3. Couple measured structure to sizing and aircraft mass/CG/inertia closure,
   then rerun fixed-revision delivered trim/stability with A's actual loads.
   Changes to geometry require fresh export, measurement and aerodynamic/load
   evidence. Do not reuse the legacy elliptical screen as final qualification.
4. Add sequential integration into requirements, discovery, closure, parent
   reconstruction and UI for this one qualified variant only. Include real
   accepted and rejected end-to-end aircraft cases. The prototype's passing
   tests do not justify enabling any physical construction mode.
5. Perform release deployment, environment publication and fresh-task
   restoration checks separately; none is established by these code tests.

## Continuation: installed collar-to-keel prototype

The follow-on modules `attachment.py`, `foam_transfer.py` and `mounted_wing.py`
extend the fixture into
`bonded_shaped_wing_riveted_keel_prototype_v2`. This remains an isolated research
diagnostic and is not an admitted production construction. Its 20 delivered
parts are eight foam panels, the carbon rod, its adhesive annulus, a collar,
two outer collar-to-foam bonds, two collar-to-plate bond layers, two aluminium
bearing plates, two aluminium through-rivets and a local plywood fuselage keel. Every part has a solid-volume
material law. Plate/rivet density is 2700 kg/m³; the keel prior is 600 kg/m³.
No unmodelled friction or fastener preload supplies capacity.

The V2 collar-to-plate annuli correct a load-path issue in the V1 prototype:
a plain circular bearing cannot carry pitch torque about its own axis without
friction. The new adhesive layers have explicit measured geometry and nominal
normal/shear checks; removing either layer fails inventory. Torque capacity is
never credited to the underlying plain bearing. Local peel still needs its
own qualification.

Root foam is cut back 20 mm on each side to accommodate the plates. The physical
lifting geometry therefore differs from the search wing. The revision changes,
and loads naming the original wing are rejected. This geometry correction must
also reach A's installed-aircraft aerodynamic inputs before final qualification;
the existing uncut-wing trim is never presented as delivered trim.

Trusted stock construction uses a bounded rectangle-with-polygon-bores
extrusion. Delaunay triangulation is accepted only when every prescribed contour
edge exists and the retained section's boundary is exactly the stock and bore
loops. The verifier does not repair material. After STL import it reconstructs
canonical surfaces, hole locations, stock thicknesses, net ligaments, pin
sections and conforming interfaces. A separate station-loft/polygon-stock
volume calculation checks the complete material partition. Missing or moved
plates, rivets, keel or collar bonds fail even with refreshed artifact hashes.

The mount reaction model assumes a rigid collar/plates and equal connector
stiffness. Four measured plate/rivet contacts balance Fz, Mx and My from both
half-wings, including asymmetric loads. The left input uses negative global Mx
for positive lift; both torque inputs use global +Y. Whole-aircraft symmetry is
not assumed. Pin mechanics integrate uniform plate bearing and an affine keel
bearing distribution that balances the roll couple as well as force. Shear
and bending extrema include interior stationary points. Constant keel bearing
would miss asymmetric roll and is not used. Supported nominal checks cover pin
shear/bending, plate and keel bearing/tear-out, and measured plate net sections.
Coupon properties, contact elasticity, local concentrations, preload, clearance,
fatigue, head pull-through and complete fuselage stiffness remain unqualified.

Foam transfer uses measured bore perimeter and area with conforming adhesive
and stock surfaces. Uniform vertical traction and constant polygon-tangent
torque traction reproduce the applied force and torque per unit span. This is
a nominal interface screen; it does not solve chordwise core elasticity or
end-peel concentrations. Foam normal/shear priors are 100/80 kPa, adhesive
normal/shear priors 1/8 MPa, with safety factor 2. These are declared assumptions,
not measured strengths. End peel and bond defects remain explicit blockers.

The mounted verifier reuses measured rod sections for both half-wing beam
checks, using carbon E=100 GPa, G=5 GPa, normal/shear allowables 400/40 MPa and
safety factor 2. Fixed-root Euler deflection is limited to half-span/20 and twist
to five degrees. Mount compliance and shear deformation remain unsupported, so
passing those calculations is insufficient stiffness qualification. Failed
beam, foam-nominal or attachment checks reject a candidate. Passing them yields
`incomplete`, never fabrication acceptance.

### Executable delivered-section sizing fixture

From the checkout root, using the glider dependencies:

```sh
PYTHONPATH=backend OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  MPLCONFIGDIR=/tmp/mirum-mpl XDG_CACHE_HOME=/tmp/mirum-cache \
  python -m cem.glider.mounted_wing --output /tmp/cem-mounted-run
```

Use a new, empty output directory. `--wing-json` accepts a `ShapedWing` parameter
object within the named flat-wing envelope. The default extra non-wing mass is
50 g, the analytical load factor is 2, and peak torque is 0.01 N·m per metre.
These can be set explicitly with the corresponding CLI options. The added mass
is only a declared load fixture; no unexported aircraft's CG/inertia is certified.

The load is zero through the root cutback and rises linearly toward the tip.
Its integral on both half-wings equals the load factor times measured assembly
plus declared extra mass times gravity. This distribution is deliberately
analytical, not a reconstruction of aerodynamic lift. Every stock size receives
fresh STL exports, a manifest, measured mass/inertia/interfaces, loads bound to
that geometry revision and a new mechanics report. The loop tries 2, 3, 4, 5,
6 and 8 mm rods and stops at the first passing supported fixture screen.

The CLI returns **3 for incomplete**, **2 for rejected**. A completed diagnostic
is not an accepted design. `result.json` records the attempted sections and
remaining blockers; each attempted section has `manifest.json`, `loads.json`
and `measurement.json`. Outputs stay outside the checkout.

On this instance the default fixture rejected 2, 3 and 4 mm rods on beam checks
and selected 5 mm for its passing supported checks, retaining `incomplete`.
The V2 run's loaded mass was about 277.39 g, including the declared extra 50 g.
This is evidence of the executable fixture-sizing/measurement path only, not a
minimum aircraft spar or a qualified performance result.

The tests include a real CLI sizing run, fresh exports and revision-bound loads
for every attempted section, full reaction equilibrium, analytical pin and net
section references, polygon traction conservation across multiple resolutions,
and rejected missing/disconnected/rehashed members. Requirements/discovery/UI
admission and A's final aerodynamic/delivered-trim qualification remain pending.

Before the shared-contract merge, the combined focused run passed **152 tests**
(one existing pytest configuration warning), including existing structure,
segmented-wing, shape and requirements regressions. The first fixture groundwork
is commit `d40cd0b`, and the initial mounted/sizing continuation is `686bf42`.
Task A's shared-contract implementation at `e88c24f` has now been merged into
this branch. It still reports incomplete real whole-aircraft polar coverage and
native spiral rejection. Its numerical references do not qualify this mounted
physical revision.

## Shared contract handoff and read-only reconstruction

`structural_adapter.py` now consumes A's actual `InstalledGeometry`,
`StructuralLoadCase`, `InstalledMaterial`, `Support` and `InstalledStructure`
types. The provisional distributed-load fixture remains useful for beam
references; it does not replace A's point-strip load contract.

`consume_loads` requires exact geometry, hardware, mission, process, calibration
and implementation binding equality. It retains every strip force and section
couple in global X-aft/Y-right/Z-up axes. It recovers the section couple by
subtracting `(strip_center - CG) × force` from the recorded CG moment, then
translates moments to the structural spar once. The load factor scales both
forces and couples exactly once. Point loads stay point loads; no interpolation
changes their total force or first moments. Centreline ambiguity, an absent
half-wing, unknown surfaces, out-of-span strips and stale contexts fail.

The consumer exposes all six resultant components and exact outboard cuts.
Tail/other surface loads remain named and quantified as unassigned, rather
than being folded into wing lift. Drag and lateral force are retained, even
though this prototype's beam/joint strength checks do not qualify their paths.
Moment translation is invariant under a CG change when source moments are
re-expressed consistently. Shared schema validity is not proof of native
qualification: the consumer always reports `shared_load_contract_only` and
`incomplete`. Body, inertia/payload/rod, gust and landing loads still need an
explicit complete-aircraft load case.

`export_structure(wing, diameter, manifest, directory, binding=...)` reconstructs
the delivered parts and contact geometry before producing
`installed-structure.json` and `installed-support-evidence.json`. Twenty material
records reference actual STL hashes, measured mass, CG, density and inertia;
22 supports form a connected graph from keel to every delivered wing member.
The support evidence artifact binds both participating material hashes,
locations, declared mechanisms, measured contact sections and all six context
identities. Mechanical constraints and `closure_state` stay `declared`.

The A geometry binding and B's delivered-construction hash are distinct
identities, not aliases. Aerodynamic-to-material geometry correspondence remains
`unverified`; attaching the shared structure to a supplied aerodynamic context
does not prove that correspondence. A's material schema carries only inertia
diagonals, so the full measured tensors are retained separately. The parent-style
`validate_structure` reimports the material and recreates the expected handoff
read-only. It rejects edited mass/density, missing supports, changed context,
promoted closure, modified inertia or raw support artifacts. Even a valid
declared handoff returns false for physical admission. Export preserves an
existing, different evidence file and requires a new output revision.

Tests use A's genuine shared fixtures for the 0.5 N / 0.125 N·m half-wing reaction
reference, complete vector/couple retention, CG-origin invariance, all six stale
binding cases, no implicit mirroring, and material/support replay. This is
fixture-based integration. Native tools are not present on this instance, and
Fortran/CMake build prerequisites are absent; no fresh native aerodynamic
qualification or actual mounted-aircraft trim is claimed.

The post-merge CEM, legacy-glider, API/UI and aerodynamic adapter run completed
**368 passed, 8 skipped** in 224.83 seconds. The skips are native solver checks;
the two warnings are dependency deprecations. The command was:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  MPLCONFIGDIR=/tmp/mirum-mpl XDG_CACHE_HOME=/tmp/mirum-cache \
  python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Dependency consistency (`uv pip check`) passed. No source, test, manifest or
lockfile changes were needed to install the frontend/API test dependencies;
the repository's pinned frontend, SlowAPI and pytest-asyncio versions were used.
The final reporting correction preserves the complete pin-bearing evidence
dictionary alongside each plate-bearing stress; it does not alter the numerical
strength checks. A separate overload reference rejects pitch torque exceeding
the explicit collar-to-plate bonds' nominal shear capacity.
After that correction, all **49** targeted attachment, real mounted-CLI and
shared-contract reconstruction checks passed in 85.65 seconds.
