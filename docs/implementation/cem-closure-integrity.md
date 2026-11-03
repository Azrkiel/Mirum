# CEM continuation: delivered-part integrity and spar enclosure

This continuation works from `engineering/cem-glider`, base
`4901b6d10fdd0232b546df1a7f3fb2dbfe705b50`. It addresses prerequisites of
G2/G3. It does **not** complete physical construction closure or the G0–G8 roadmap.

## Changed behavior

Closure derives its required part inventory and material laws from the actual
wing/tail configuration and calibrated process inputs, independently of the
supplied bundle manifest. Missing, duplicate, renamed, extra or unlisted STLs,
altered material laws and symlinked part files fail before aerodynamic reanalysis.
Each measured part must be one connected, closed, oriented mesh. An unknown
material law cannot silently use the skin-equivalent branch.

The delivered straight stock spar is checked against the delivered wing meshes.
Every spar triangle is clipped at the centreline. All clipped vertices must lie
inside the outward face half-spaces of the corresponding wing half, within
0.2 micrometres for STL quantization. Because triangles and half-spaces are convex,
this covers complete triangle interiors rather than sparse samples or bounding
boxes. Temporary plane-test matrices and total plane tests are bounded. The
intersection is a conservative sufficient enclosure condition: concave panels
can fail even when an exact nonconvex containment test would pass. Failure means
**enclosure not certified**, rather than a measured physical penetration depth;
`max_outside_m` is the maximum face-plane violation.

A flat-wing control passes; raised/sloping wings around a straight rod and
separated halves with a central gap fail. A pass does not establish spar-channel
clearance, connections, joints or load transfer. `load_path_state` remains
`unverified`, and manufacturing readiness remains false.

Parent validation requires the canonical inventory, connected-part material
measurements and bounded spar-enclosure evidence before accepting `screened`
output. A design can pass aerodynamic discovery yet be rejected at build time.
The existing raised/swept fixture now does exactly this; its deterministic artifacts
and rejected report remain reproducible. Rejected bundles remain unavailable
through the download API.

## Verification

The new tests first reproduced unknown-material and disconnected-part acceptance.
The six inventory mutation cases also failed against the original closure loaded
in an isolated test process, before passing against the new implementation.
Regression command from `/workspace/Mirum`:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
XDG_CACHE_HOME=/tmp/mirum/cache MPLCONFIGDIR=/tmp/mirum/matplotlib \
/workspace/.venv-glider/bin/python -m pytest -q \
  backend/tests/test_cem_geometry_integrity.py \
  backend/tests/test_cem_requirements.py backend/tests/test_cem_api.py \
  backend/tests/test_cem_pipeline.py backend/tests/test_glider_model.py \
  backend/tests/test_glider_search.py backend/tests/test_glider_explorer.py \
  backend/tests/test_glider_flight_conditions.py
```

Result: **136 passed**, one existing Starlette/AnyIO deprecation warning.
The final integrity file has **24 passing cases**, including three subsequently
added open/truncated/symlink mesh cases. Existing frontend glider state tests add
**3 passes**: **142 unique passing checks across these focused runs**. Integrity
cases include real STL measurement and positive/negative enclosure controls;
parent-side evidence tests deliberately use constructed reports.
The existing pinned frontend build and Python dependency consistency check pass.
A real HTTP run after restarting the API on the final source passed health,
metadata and reference-gap feasibility, then compared all five processes and
returned 12 candidates. Building the selected candidate produced a rejected
report with failed spar enclosure, and its download returned HTTP 409. Its
maximum face-plane violation was approximately 0.08025 m (not a penetration-depth
measurement). Load transfer remained explicitly unverified. An earlier run
correctly rejected stale evidence after a source change while the parent API
was running: restart the API after source edits because provenance is cached
per process.

Native CadQuery/OCP also produced a valid 10 × 20 × 3 mm box with volume 600 mm³.
This is current-instance validation, not a clean Docker verification image build,
independent aerodynamic solver qualification, flight evidence or publication.

## Remaining work from the handoff

The straight Y-axis rod builder is retained to expose, rather than conceal, the
installed-path defect. A genuinely physical next slice must construct a compatible
load-bearing path and its joints, iterate local sections against converged loads,
add channels/ribs/skin/payload and ballast cavities, measure fabricated material
volumes, and recover the aerodynamic geometry independently. It must qualify
clearance and load transfer; a conservative enclosure test is insufficient.

Skin-equivalent OML mass is still screening evidence. Full uncertainty re-trim,
actual XFOIL/AVL cross-checks, novel families, PicoGK manufacturing, flight
calibration and advanced environments remain open as described in the original
handoff. This continuation does not modify deploy branches, deploy or publish the
application/environment, or make a fabrication-readiness claim.
