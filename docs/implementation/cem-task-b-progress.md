# Task B implementation status

This is a fixture-based prototype, **not completion or qualification of Task B**.
The requested task is [the Task B brief](cem-task-b.md). The inspected Task A
branch ends in a task brief as well; it supplies neither merged shared contracts
nor validated shape-specific aerodynamic loads. No physical construction has
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

1. Merge A's shared installed-material/support/load-case contract and adapt this
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
