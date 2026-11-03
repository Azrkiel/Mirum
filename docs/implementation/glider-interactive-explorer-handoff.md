# Handoff: interactive passive-glider explorer

## Goal and agreed scope

Add a live, rotatable 3D glider preview that changes as the designer drags single-value sliders. Cover wings, body and tail, let the designer load and edit search candidates, and recalculate performance for the edited design.

The mission remains **small passive fixed-surface gliders optimized for maximum glide distance**. At a fixed launch height in still air, maximize trimmed L/D subject to designer constraints. Display distance as `launch_height_m × trimmed L/D`, with the launch height and assumptions beside the estimate.

This document is a plan. The interactive explorer is not implemented yet. Do not deploy, publish the environment, or reapply previous environment drafts as part of this handoff.

## Current implementation to preserve

The first glider implementation and this handoff are saved together on **`feature/passive-glider-distance-search`**. Start from that branch, not from the earlier main baseline. Inspect `git status` and preserve any subsequent local changes. Use the existing isolated checkout; do not create a worktree unless explicitly requested.

Key files, relative to the repository root:

| File | Existing responsibility |
| --- | --- |
| `frontend/app.py` | Workflow selector and existing Text-to-CAD Babylon.js viewer |
| `frontend/glider_ui.py` | Requirements form, interval sliders, job polling, results and ZIP downloads |
| `backend/gliders/spec.py` | Validated mission, materials, constraints and permitted search intervals |
| `backend/gliders/model/components.py` | Wing, fuselage and tail parameter types and geometry builders |
| `backend/gliders/model/assembly.py` | Shared placement, mass, payload, ballast and wing mounting |
| `backend/gliders/model/analysis.py` | Coupled balance/trim, aerodynamic performance and stability screening |
| `backend/gliders/search.py` | Seeded adaptive search, constraint checks and candidate exports |
| `backend/gliders/api.py`, `worker.py` | Bounded asynchronous jobs, status and downloads |
| `docs/glider-search.md` | Current API, ranges, installation and limitations |
| `requirements-glider.lock` | Additive solver dependencies for the existing Linux locks |

Current API:

- `POST /gliders/search`
- `GET /gliders/search/{job_id}`
- `GET /gliders/search/{job_id}/geometry/{candidate_id}`

Current controls choose **search intervals**, inside a Streamlit form. They do not send updates during dragging, and the ZIP's three-view PNG is static. There is no single-design preview/evaluation API yet.

Supported families are tapered/elliptical/polyhedral wings; pod-boom/torpedo/slab bodies; conventional/T/V tails. Wing dihedral is currently **3–12° upward**. Polyhedral wings have a flat center and raised outer sections. Signed anhedral, 15° dihedral, moving ailerons/flaps and active flight controls are outside this change. Do not silently widen these limits to match the reference picture.

Previous validation, for context rather than a fresh readiness claim: 32 new tests and 66 selected existing regression tests passed. A real HTTP run evaluated 24 designs, found 8 feasible candidates and verified three geometry archives. The leading candidate predicted approximately 119 m from a 10 m launch height. Re-run relevant tests after implementing this plan.

## Intended user experience

1. Open the glider workflow and choose **Explore one design** or **Search designs**. Keep existing range-based search available.
2. Explore one design starts from a reproducible preset or a selected search candidate. Show a large 3D viewport with orbit/zoom, reset camera, and top/front/side views.
3. Drag a slider and watch the geometry change continuously. Wing controls cover span, chord, taper, sweep, upward angle, washout and incidence. Body controls cover style and dimensions. Tail controls expose only dimensions relevant to its selected configuration. Include airfoil choices.
4. Draw a dihedral angle guide in the front view. Distinguish upward angle (dihedral), twist (washout), and incidence through labels and brief help text.
5. Separate the exact value being viewed from its permitted search interval. Editing one design must not silently change the search bounds or launch an optimization run.
6. After edits, show **Performance needs recalculation**. Offer **Recalculate performance**, with optional automatic recalculation after a short pause. Geometry movement must stay responsive while analysis runs.
7. After analysis, show L/D, predicted still-air distance, speed, mass, static margin, stall margin and individual rejection reasons. An attractive but infeasible shape remains visible and clearly marked.
8. Let the designer reset, compare with the original candidate, and download the currently evaluated revision. Search results remain available when returning from exploration.

## Architecture and implementation sequence

### 1. Define one canonical design revision

Add a typed single-design specification, distinct from `GliderRequest`, in a proposed `backend/gliders/design_spec.py`.

- Reuse or adapt the existing wing/body/tail parameter fields; do not accept executable source or arbitrary object attributes.
- Carry mission, materials and constraints from the existing request. Validate actual parameter values against both model limits and the designer's allowed ranges/configurations. Ignore irrelevant tail controls in the UI and reject contradictory inputs on the server.
- Explicitly represent both nominal inputs and evaluated placement: wing position, payload position, ballast and tail incidence. Loading a candidate must use its `parameters`, `request` and evaluated placement fields; reconstructing from nominal parts alone loses its trim/balance.
- Specify balancing semantics: an edit creates a nominal revision; recalculation may rebalance and retrim within allowed bounds. Return the resulting effective geometry to the viewer and show any adjustments. Never leave a nominal preview beside metrics for a different balanced geometry.
- Compute a canonical digest over physical inputs, mission/materials/constraints and balancing policy. Return a separate geometry digest for the effective evaluated geometry, plus the implementation/solver version in analysis evidence.
- Build and evaluate fresh assembly instances. The current evaluator mutates its `Glider`; do not let preview, evaluation, export or cached revisions share a mutable instance.
- Move supported controls/ranges into one schema/metadata source so frontend constants and server validation cannot drift.

### 2. Add a fast geometry path and a continuous browser viewer

Proposed routes: `POST /gliders/preview` and a metadata endpoint for supported controls. Preview returns typed revision identifiers, units, coordinate convention, named part meshes and relevant geometry warnings. It must not invoke the aerodynamic evaluator, a model provider, an optimizer or Matplotlib exports.

Use the existing geometry builders as server authority. Generate coarse meshes directly from `Glider.airplane()`; avoid waiting for ZIP/STL files for every slider movement. The existing axes are x aft, y right and z up, in metres. The viewer must handle this explicitly; STL downloads use millimetres.

Recommended frontend route: a small **custom Streamlit component** under `frontend/components/glider_explorer/`, with a browser-side mesh viewer and slider interaction. Reuse the existing Babylon.js rendering approach where useful, but keep the glider viewer independent of the Text-to-CAD result state.

- The existing Python widgets inside `st.form` cannot provide continuous drag updates. A Streamlit rerun after mouse release is an intermediate milestone, not completion of the requested interaction.
- For continuous dragging, render local lightweight parametric geometry on animation frames. Port only the needed geometry formulas, with golden fixtures comparing browser meshes to canonical Python meshes for every family. Label unsettled/local geometry as a preview.
- Debounce canonical server preview requests by roughly 150–250 ms; request the exact settled revision on release. Replace the local preview with the matching server mesh. Keep the camera and axes stable.
- Coalesce edits, abort obsolete requests where possible, and ignore responses for old revision IDs. Do not rebuild the whole viewer or reset its camera for every change.
- Render bounded triangle buffers with part colors, wireframe optional, and visible center/axes. Dispose of replaced geometry/materials and handle failed mesh responses without replacing the last valid model with an unrelated one.
- Target immediate visual feedback while dragging and canonical preview latency below roughly 500 ms for representative small-glider cases on the test host. Measure actual latency and record the conditions; do not claim a universal guarantee.
- Bundle browser dependencies and produce a reproducible build with a lockfile. Add build steps to both frontend container paths as needed (`frontend/Dockerfile` and the Fly frontend image). Avoid relying on a new CDN script being reachable at runtime.

Ship the server preview and its tests first, then the continuous component. Keep the explorer explicitly labelled incomplete until continuous interaction works in a real browser.

### 3. Add single-design aerodynamic recalculation

Proposed routes:

- `POST /gliders/evaluations` → HTTP 202, evaluation ID and input revision.
- `GET /gliders/evaluations/{evaluation_id}` → progress, failure, or revision-bound evidence and effective geometry.
- An export endpoint for a completed, identified evaluation revision.

Refactor the existing bounded worker infrastructure to support a single-design task without running a candidate sweep. Reuse `evaluate` and independent constraint enforcement; do not implement a second scoring formula in the UI. Preserve time/memory limits, child-process credential exclusion, access-token middleware, cleanup and explicit failure handling.

Important scheduling decision: the current search registry allows one search per API process and uses in-memory metadata. Add a small bounded scheduler for search and single-design evaluations rather than silently creating unbounded workers or letting every drag enqueue a job. Keep single-worker deployment assumptions explicit until a shared job store exists. A busy response should leave the edited geometry usable.

Prefer an explicit recalculation button for v1. If adding automatic evaluation, debounce it separately from geometry preview, allow at most the latest pending revision per explorer, and prevent old evaluations from accumulating. Cancellation and server restart must produce recoverable UI states.

On each edit:

- Invalidate current performance and evaluated-download eligibility immediately.
- Retain older results only under an explicit previous-design label.
- Bind every response to its input revision and effective geometry digest.
- Discard late replies for superseded revisions; never mark the current design feasible from an older result.
- Show unknown/failed/unevaluated checks distinctly from passed checks. Keep an invalid design inspectable, with rejection reasons.

Cache by canonical inputs plus implementation/solver version, with bounded retention. Changes to payload, mass model, constraints or flight assumptions invalidate evidence even if the external shape looks unchanged. Height-only changes may rescale an existing distance estimate only when the complete analysis context is otherwise identical and the new evidence clearly records the new height.

### 4. Integrate search, comparisons, graphs and export

- Add **Explore this design** to every retained search candidate. Load the exact evaluated assembly and preserve the search run independently.
- Compare the original and edited versions at the same scale, with a metric delta table and separate feasibility states. Use the same mission/assumptions for meaningful comparisons or flag differences.
- Provide a lightweight candidate scatter plot (for example mass versus predicted distance); selecting a point loads that candidate and its matching evidence.
- A continuous performance curve versus dihedral/span is a later enhancement: each point requires a real evaluated design. Bound the sweep and show failed/unevaluated points. Do not present interpolation as solver evidence or make geometry dragging wait for the curve.
- Downloads include geometry, full inputs, effective balance/trim, digests, solver version and assumptions for the selected revision. An explicit unvalidated preview download, if offered, must be labelled accordingly and contain no feasibility claim.
- Preserve existing search exports and the Text-to-CAD workflow.

## Tests and acceptance criteria

Automate the following meaningful cases:

1. Invalid/nonfinite/out-of-range single-design inputs are rejected. Locked parameters remain locked during balancing and trimming where applicable.
2. Geometry-only preview does not call `evaluate`, an optimizer or any provider. Preview units, symmetry, bounds and named parts match the canonical model for all supported families.
3. Browser/Python geometry parity fixtures cover dihedral, polyhedral tips, sweep, twist, all body styles and conventional/T/V tails. Front-view tip height agrees with the canonical builder.
4. A search candidate opened without edits reproduces its evaluated wing/payload placement, ballast and tail incidence.
5. Editing invalidates evidence and downloads. Simulate out-of-order preview/evaluation responses and prove stale results cannot replace current geometry or metrics.
6. Two sessions, rapid edits, cancellation, busy workers, restart/expired jobs and timeouts remain bounded and recoverable. Existing access controls cover every new route.
7. Single-design performance matches the existing evaluator for the same effective inputs. Rejected designs receive reasons rather than success metrics. Repeated pinned-runtime evaluations agree within numerical tolerances.
8. Exported geometry and evidence share the selected evaluated revision. No reconstruction from default balance/trim occurs at download time.
9. Use Streamlit AppTest for state/form behavior and real browser tests for slider dragging, camera preservation, component communication and response ordering. AppTest alone does not validate WebGL interaction.
10. In a real browser, drag wing dihedral from 3° to 12° and observe both tips rise continuously. Change body dimensions and tail type and observe immediate corresponding changes. Recalculate, inspect feasibility, then download the same evaluated revision.

Run relevant existing tests from the repository root after installing the current three Linux lockfiles:

```bash
PYTHONPATH=.:backend:frontend GEMINI_API_KEY_HEAVY=offline-fixture-never-sent \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MPLCONFIGDIR=/tmp/mirum/matplotlib \
.venv/bin/python -m pytest -q \
  backend/tests/test_glider_model.py backend/tests/test_glider_search.py \
  backend/tests/test_access_token_gate.py frontend
```

Add focused tests for the new API, revision state and browser component. Recheck existing native/engineering API tests if shared lifecycle or worker code changes. Preserve real CAD and aero tests; do not replace them with mocked-only success checks.

## Recommended delivery slices

1. **Canonical single-design spec + server geometry preview**, with geometry parity tests.
2. **Continuous slider/3D component**, with camera controls and real browser validation.
3. **Bounded single-design evaluation + stale-result protection**, with effective geometry reconciliation.
4. **Candidate editing, comparison plot and revision-bound downloads**, followed by documentation and relevant regressions.

First action for the next task: inspect the feature-branch implementation and any subsequent local changes, then define the canonical single-design and effective-geometry contracts before adding endpoints or moving the current form. Finish each slice with runnable evidence. No model training is needed.

## Limits to keep visible

The optimizer finds promising candidates within a finite search, not a guaranteed global optimum. Aerodynamic results remain screening estimates at standard sea-level conditions; strength, flutter, launch/gust loads, wing flexibility and physical flight validation remain outside the model. A responsive visualization does not expand that evidence. Extending to anhedral, active control surfaces, RC aircraft or larger sailplanes requires a separate model/scope review.
