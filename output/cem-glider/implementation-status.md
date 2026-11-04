# Glider CEM implementation and qualification status

This change introduces a working, provider-free physics workflow on top of the
existing glider solver. It implements a **bounded first slice of the CEM
roadmap**, not all G0–G8 acceptance criteria. Its public design status is
`screened` or `rejected`; `accepted_for_manufacture` and
`engineering_certified` remain false.

## Working path

Typed mission → analytic feasibility and reference-gap report → process-aware
seeded MAP-Elites search → selected outer mould line and spar meshes →
independent bounded STL measurements → measured-mass trim/stability reanalysis
→ up to three geometry/trim correction passes → uncertainty and revision-bound
screening bundle.

The reference goal never stops maximization. An optional
`minimum_performance` is a separate hard acceptance threshold at P10.

## Scope against the supplied roadmap

| Phase | Implemented | Remaining qualification/work |
| --- | --- | --- |
| G0 | CEM protocol, strict mission models, concrete stage adapter, canonical JSON, content identities, source/runtime provenance | More domains |
| G1 | Launch energy, atmosphere, versioned offline Re-indexed NeuralFoil envelope, gap/levers, sub-second analytic screen | XFOIL cross-validation; universal infeasibility is not established by sampled polars |
| G2 | Distance and duration objectives, process mass assumptions, catalogue carbon-rod launch bending, process admission, auto comparison, wind speed-to-fly and uniform uplift semantics | Qualified material data; local spar taper, joints/torsion, detailed FDM wall/bed/overhang checks |
| G3 | Closed OML meshes, independent volume/area/CG/inertia measurements, measured-mass re-trim, 5% mass gate, measured spar bending, bounded rebuild loop, seeded 200-sample bands | Physical shell/rib/cavity geometry; independent reconstruction of aerodynamic shape from meshes; full Monte Carlo re-trim ensemble |
| G4 | MAP-Elites over conventional, T-tail and V-tail layouts; existing random/local sampler; station Re/confidence guards | Tailless/canard/tandem/box/blended families; B-splines/CST; Opti/CMA-ES; independent XFOIL+AVL recheck; validated novelty claims |
| G5 | Explicit unavailable capability; no print-ready claim | PicoGK glider builder, shells/infill/channels/cavities, bed splitting and physical-part closure |
| G6 | Typed API, bounded jobs, optional Gemini translation with ambiguity/review gates, Streamlit form, process table, archive chart, elite selection and bundle downloads | Live-provider extraction validation; explorer cell-click integration; novel-family UI |
| G7 | Immutable calibration versions, identifiable profile-drag log-normal update, synthetic recovery test, old identity preservation | Jointly identifiable flight experiments, more coefficient updates, automatic archive re-scoring |
| G8 | Inputs modelled and API rejects unqualified environments | Thermal-column and ridge-region models |

Rocket R1 remains a later domain as specified in the roadmap.

`families="all"` means evaluate every currently qualified family and explicitly
report the unavailable ones. It does not relabel a conventional aircraft as a
tailless or novel design. `process="auto"` attempts every process and gives
each a best admitted design **or an explicit no-design result**.

## Run

Install the existing pinned native/runtime environment plus
`requirements-glider.lock`. The new trimesh/networkx pins match the existing
verification lock.

The normal backend attaches the new routes. A provider-free host is also
available, without loading the text-to-CAD orchestrator:

```sh
PYTHONPATH=backend python -m uvicorn cem.app:app --host 127.0.0.1 --port 8000
BACKEND_URL=http://127.0.0.1:8000 streamlit run frontend/app.py
```

Select **Physics-based glider CEM** in the workflow selector. Numeric typed
missions and physics never call a model provider. The optional words-to-mission
route uses the existing Gemini configuration; ambiguity returns
`needs_clarification`, and a successful translation returns
`review_requirements` before any search starts. Provider responses are not
accepted as physics evidence.

Example:

```sh
curl -s http://127.0.0.1:8000/cem/glider/feasibility \
  -H 'Content-Type: application/json' --data-binary @examples/cem/glider-distance.json
curl -s http://127.0.0.1:8000/cem/glider/discover \
  -H 'Content-Type: application/json' --data-binary @examples/cem/glider-distance.json
```

Poll `/cem/glider/jobs/{job_id}` every 2–3 seconds. Select an elite and POST
`{"discovery_job_id":"…","elite_id":"…"}` to `/cem/glider/design`.
Poll its returned job ID, then download
`/cem/glider/jobs/{design_job_id}/bundle` after successful screening. Rejected
builds have evidence but no downloadable bundle.

## Identity, evidence and resources

The mission output ID is the first 12 hex characters of SHA-256 over a canonical
envelope of requirements, CEM version and calibration content hash. The seed is
part of requirements. The CEM version incorporates implementation-source and
runtime fingerprints. Each archive elite also has its own parameter/placement
revision, and the final corrected design has a full SHA-256 revision.

Artifacts include the mission ID. `design.json` and STL bytes are deterministic
in one pinned runtime; ZIP entry timestamps are fixed. Reports bind exact STL
hashes, source/runtime versions and the original calibration snapshot. The
parent validates archive membership, final revision, measured-part accounting,
physics/uncertainty evidence and ZIP contents/hash. Download rechecks the ZIP.

One solver slot is shared with the existing glider workflow. A CEM worker has
a 3 GiB address-space cap, 30-minute deadline, 64 KiB stdout/stderr budget and
an environment allowlist without provider/deployment credentials. The parser
caps JSON reports, and binary STL verification bounds bytes/facets. Failed,
cancelled, malformed, stale or incomplete evidence cannot produce a screened
download. Up to eight jobs are retained; jobs expire after an hour or restart.

Use persistent storage for `MIRUM_WORK_DIR/cem/calibration` if flight-test
versions must survive container replacement. Old bundles embed their complete
calibration snapshot. New flights do not overwrite the initial coefficients.

## What the masses and bands mean

Wing/tail/pod meshes are closed **outer mould lines**. They are not fabricated
hollow shells. Their mass law is explicitly a skin-equivalent areal density;
triangle area moments provide their mass, CG and full inertia tensor. The spar
is a solid rod with volume-derived mass. Hardware, payload and ballast are
point masses. The verifier reimports the exact binary STLs and re-runs the
existing aero/stability model with measured mass properties, fixed placement,
payload and ballast. A trim change triggers a geometry rebuild and remeasurement.

The band is local uncertainty propagation over drag, lift limit, efficiency,
build mass and launch efficiency, with fixed trim CL. It is **not 200 full
stability/trim solves**. Failed stall samples count as zero performance.
Sustained uplift has no finite range/duration without a bounded lift region;
such candidates are not ranked using infinity.

The finite feasibility envelope is only valid for the initial performance/mass
calibration. A changed calibration returns `marginal` with no numerical upper
bound until the envelope is regenerated. This prevents a stale pessimistic
bound from becoming a false infeasible verdict.

## Flight-test update

`POST /cem/glider/flight-test` takes a completed design job ID, its output ID and
`measured={launch_height_m, launch_method, duration_s, distance_m}`. This version
supports **still-air drop flights with measured range and speed-compatible
duration**, holding mass, CL, Oswald efficiency and trim fixed. It updates only
the identifiable profile-drag multiplier. Non-drop or missing-range records
are rejected instead of jointly guessing launch energy and drag.

The response returns a new immutable calibration hash. Pass it as
`calibration_version` on the next mission to re-score/re-discover and assimilate
subsequent flight evidence against that prior. No active calibration is silently
changed. The test suite recovers a known synthetic drag factor within 5%; no
physical flight calibration is claimed.

## Verification

```sh
OPENBLAS_NUM_THREADS=1 python -m pytest \
  backend/tests/test_cem_requirements.py backend/tests/test_cem_api.py \
  backend/tests/test_cem_pipeline.py backend/tests/test_glider_model.py \
  backend/tests/test_glider_search.py backend/tests/test_glider_explorer.py \
  backend/tests/test_glider_flight_conditions.py -q
```

The tests exercise real AeroSandbox/NeuralFoil calculations and real mesh export
and import. Native CAD and the full offline API regression gate should also run
in the existing verification environment. Provider authentication/generation
quality, XFOIL/AVL agreement, physical flight performance and fabricated parts
require their own evidence.
