# CEM continuation: converged stock-rod bending

This slice continues `c483e17b7a01f593c88ce36c89e5e142d5c2b163` on local branch
`cem-next-phase`. It advances the G2/G3 structural prerequisite. It does not
complete physical construction closure or start qualifying new G4 families.

## Changed behavior

`SizedGlider.spar()` now sizes against the current non-spar aircraft mass,
including payload, hardware and ballast, plus the selected catalogue rod's own
mass. An ascending, bounded catalogue iteration finds the least fixed point.
It runs again after placement/ballast changes; it has no stale size cache. The
mission mass cap remains an admission constraint rather than the assumed load.
Sizing reserves the bending-section loss of the delivered 48-sided polygon.
This can change candidate masses, trim, admission and archive rankings; historical
IDs/performance must not be reused. Source/runtime provenance covers the new code.

`structure.py` integrates the ideal elliptical half-wing load from each station
to the tip. Its root value is `n W b / (3 pi)` and its tip moment is zero. The
closure check uses the **measured total aircraft mass** and the delivered STL,
not the sizing mass or its claimed stress. Seventeen stations document the
monotonically decreasing bending load. A uniform section makes the root the
governing section; this is not a sampled search for arbitrary local defects.

Before deriving a section from global solid inertia, the verifier requires a
closed, connected, convex, centred, full-span Y-axis prism with matching end
rings, axis-aligned cap faces and vertical side faces. It rejects a short,
shifted, tilted, tapered or necked rod. This explicitly bounds the section
identity's applicability. The structural mesh budget is 4,096 facets. The
0.2 micrometre span tolerance is for STL quantization, not construction clearance.

The result records the material assumptions and safety factor, load factor,
measured section inertia, extreme fibre, station moments/stresses and maximum
stress. Parent validation reloads the hash-bound rod and recomputes the complete
structural result before admitting a screened download. Missing, modified,
overstressed or inconsistent structural evidence fails closed. This complements
the existing independent inventory/enclosure and ZIP/revision gates.

## Qualification limits and next work

The span loading is still ideal elliptical **total** lift. It is not recovered
from an independently solved aerodynamic load distribution and does not include
tail loads. Carbon properties remain engineering assumptions, not qualified
supplier/test data. The rod remains uniform and straight. No tapered installed
path, channel, rib, skin, joint, cavity or local connection has been constructed.
`load_path_state` remains `unverified`; manufacturing/certification flags remain
false. OML mass remains skin-equivalent. No deflection, torsion, buckling, gust,
landing or fatigue qualification is added.

Next, choose one bounded physical sheet/balsa/hybrid construction and encode its
installed geometry, stock/clearance and joint assumptions. Construct actual
skin/rib/channel/cavity parts and measure their material volumes, CG and inertia;
recover/check aerodynamic sections from the delivery. Extend the structural
model to the actual load distribution and installed sections without weakening
the existing enclosure or evidence gates. Keep unsupported layouts rejected.
G4 shape discovery should follow that physical closure path, as the plan directs.

## Verification

The new tests first failed because the structural module was absent. They cover
the closed-form load integral, nonphysical inputs, catalogue mass convergence,
actual-load sensitivity, measured polygon section, undersizing, mass injection,
unsupported installed rod geometries and parent-side evidence tampering.

The focused CEM command (from `/workspace/Mirum`, Python 3.10.22, pinned locks):

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
XDG_CACHE_HOME=/tmp/mirum/cache MPLCONFIGDIR=/tmp/mirum/matplotlib \
/workspace/.venv-glider/bin/python -m pytest -q \
  backend/tests/test_cem_structure.py \
  backend/tests/test_cem_geometry_integrity.py \
  backend/tests/test_cem_requirements.py backend/tests/test_cem_api.py \
  backend/tests/test_cem_pipeline.py
```

Run FastAPI TestClient and HTTP checks in a socket-enabled execution context;
this environment's restricted context blocks sockets and can hang TestClient.
No solver or application assertion should be skipped to work around that.

Results: **86 passed** in the focused CEM suite; **76 passed** in the existing
glider model/search/explorer/flight-condition suites. The final structural,
pipeline and frontend-state recheck passed **33 tests**, including the 3 frontend
state cases not in those suites: **165 unique passing cases across the runs**.
The API suites retain one existing Starlette/AnyIO deprecation warning. Restricted
socket runs were stopped and replaced by the completed socket-enabled runs;
their partial output is not additional pass evidence. The full native/Docker
regression gate was not run.

Current-instance HTTP validation used the provider-free `cem.app` host and
`examples/cem/glider-distance.json`. All five processes were searched, returning
10 candidates. The selected build was rejected for enclosure and required trim
revision; its independently measured bending check passed at 17 stations. It
used 0.17432429315953532 kg measured mass and reported about 182.61 MPa root
stress against the 200 MPa assumed allowable after safety factor. Rejected bundle
download returned HTTP 409. These are screening results, not flight evidence.

The pinned explorer production build and Python dependency consistency check
pass. Native CadQuery/OCP produced a 10 x 20 x 3 mm box of volume 600 mm3.
Streamlit startup returned HTTP 200 `ok` at its internal health endpoint. This
does not establish a browser interaction check, clean Docker verification build,
independent XFOIL/AVL agreement, live Gemini access or publication.
