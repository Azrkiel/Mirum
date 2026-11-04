# CEM continuation: volume-measured foam wing cores

This follows the converged stock-rod work on local branch `cem-next-phase`.
It implements a bounded **wing subassembly** slice of G2/G3, not complete
physical aircraft closure or manufacturing qualification.

## Implemented

An explicit typed mission option, `construction="foam_core_wing_v1"`, requires
`process="sheet_foam"`. The default remains `screening_oml`; other processes
and `auto` cannot silently substitute this construction. The option participates
in mission identity. The old areal-mass feasibility envelope is invalidated:
`optimistic_bound=null`, `bound_state="rebuild_required_for_construction"`,
and maximization continues without an unsupported infeasibility claim.

The trusted builder preserves the wing's outer aerodynamic triangles and creates
a straight, open-ended polygonal spar channel. It explicitly constructs annular
end faces and closed material meshes, without boolean engines or new packages.
Wing mass is now solid material volume times a 35 kg/m3 foam density prior,
scaled by the pinned process mass calibration. Tail and pod mass remain
skin-equivalent. Payload, ballast and hardware retain their existing placements.
Volume mass, centroid and intrinsic wing inertia feed sizing and the lateral
screen; rod selection includes the removed channel and selected rod mass.

The channel's minimum radial clearance is 0.25 mm and the surrounding foam web
is at least 1 mm under a conservative face-halfspace test. The channel polygon
is circumscribed sufficiently to retain that clearance around the stock rod.
The complete material envelope, rotated into the root chord frame, must fit
20 mm stock. These are engineering construction assumptions, not qualified
material/adhesive or cutting-process data.

Independent import requires each delivered core to be closed, consistently
oriented and connected before any analysis-envelope recovery. The verifier
checks the cylindrical through-channel, annular end faces, clearance, web,
actual rod axis/diameter and stock thickness. It matches delivered exterior
triangles to the parametric aerodynamic OML within the existing 0.2 micrometre
STL tolerance. A separately derived analysis envelope then feeds the existing
spar enclosure check. This envelope is not a repaired material artifact.

Parent validation reimports both cores, remeasures material mass/CG/inertia,
checks file revisions, verifies the installed rod and recomputes the complete
channel evidence. A refreshed hash cannot legitimize altered material geometry,
mass, clearance or load-path claims. Each core is capped at 12,000 facets;
caches are bounded and jobs retain the existing 3 GiB worker cap.

## What remains unqualified

The positive control is a flat, mildly swept/tapered wing subassembly. It does
not establish a flight-admissible aircraft. The original aerodynamic search
range has at least three degrees of dihedral; those bounds were not widened.
The current default raised-wing candidates cannot accommodate this straight
full-span stock path and are rejected explicitly. There is no fallback to
areal OML mass or an invented admitted design.

The reference HTTP run of the new mode completed all eight evaluations with
**zero admitted designs**, each rejected for insufficient channel/web enclosure.
The request with the wrong construction process returned HTTP 422, and its
feasibility check correctly withheld the stale numerical bound. This is negative
admission evidence, not a successful complete physical glider build.

Root/panel joints, bond lines and load transfer are unverified. A clearance hole
does not establish a joint. Skin/rib alternatives, payload/ballast cavities,
physical tails/pod, torsion, deflection and independent aerodynamic solvers
remain open. `load_path_state="unverified"`, `manufacturing_ready=false` and
`accepted_for_manufacture=false` remain mandatory.

## Next slice

Implement a segmented installed spar path for the admitted dihedral layouts,
with explicit root/panel joints and their material/bond/load assumptions. Size
and verify each delivered segment in its own section frame from the converged
load distribution; do not apply the uniform Y-axis identity to a bent assembly.
Preserve per-part accounting and verify both channel clearance and the joint's
load-transfer evidence. This is the remaining prerequisite for a positive full
aircraft construction result. Continue physical tails/pod/cavities and material
qualification before claiming full G3 closure or proceeding to qualified G4 gains.

## Reproduction and evidence

Use the existing checkout and pinned `/workspace/.venv-glider` environment.
The API request example is `examples/cem/glider-foam-core.json`; submit it to
the ordinary feasibility/discovery endpoints on the provider-free `cem.app` host.
Restart the host after source changes because provenance is cached. No Gemini
key is needed. Current search rejection is an expected, explicit result.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
XDG_CACHE_HOME=/tmp/mirum/cache MPLCONFIGDIR=/tmp/mirum/matplotlib \
/workspace/.venv-glider/bin/python -m pytest -q \
  backend/tests/test_cem_foam_core.py \
  backend/tests/test_cem_structure.py backend/tests/test_cem_geometry_integrity.py \
  backend/tests/test_cem_requirements.py backend/tests/test_cem_api.py \
  backend/tests/test_cem_pipeline.py frontend/test_glider_ui.py \
  backend/tests/test_glider_model.py backend/tests/test_glider_search.py \
  backend/tests/test_glider_explorer.py backend/tests/test_glider_flight_conditions.py
```

Use a socket-enabled execution context for TestClient/API tests. The physical
tests include independent analytic material-volume bounds, positive real-STL
core recovery, density-derived mass/inertia, determinism, wrong process/stock,
20% mass injection, and plugged/undersized/shifted/altered/open deliveries.
Parent tests tamper with mass, clearance, shape qualification and load path,
including rehashing a misplaced actual rod. No fabrication, flight, paid-provider
or independent XFOIL/AVL validation is claimed.

The final combined command above completed **185 passed**, with one existing
Starlette/AnyIO deprecation warning. `git diff --check` passed. The HTTP checks
were repeated after restarting the provider-free host on the final source;
the new mode returned the explicit eight-evaluation/no-admitted-design result.
No clean Docker image build or publication was performed in this slice.
