# Mirum CEM builder handoff

Date: 2026-11-03. This document describes the implementation left in the workspace and the work needed to complete the supplied roadmap.

## Start here

Latest continuation: [segmented polyhedral wing and joint screening](cem-segmented-wing.md)
records the installed four-segment path, sleeves/bond geometry, positive closure,
and the remaining test qualification, foam transfer and wing-saddle work.

Latest construction continuation: [volume-measured foam wing cores](cem-foam-core.md)
records real solid-material/channel geometry, its verified flat-wing control,
and the installed dihedral-path restriction still preventing a complete glider.

Latest continuation: [converged stock-rod bending](cem-converged-structure.md)
records the actual-mass sizing/section checks and remaining physical closure work.

Continuation: [delivered-part integrity and spar enclosure](cem-closure-integrity.md)
records the subsequent G2/G3 integrity checks, tightened build rejection behavior,
verification and remaining physical-construction work.

The current implementation is a working **engineering screening workflow**, not a completed G0–G8 system or a fabrication-qualified glider generator. Preserve that distinction in code, reports and UI.

Continue from branch `engineering/cem-glider` in the repository below. In the original workspace it lives at `/workspace/Mirum`. This handoff accompanies publication of the implementation on that GitHub branch. The original implementation base was:

```text
2e6a4eebe666955f3493d354e7d93cf0380312db
```

Repository: https://github.com/Azrkiel/Mirum.

Use the branch's current commit from `git log -1` as the published snapshot. The work originally existed as modified tracked files and untracked files; the branch now packages those changes. Inspect any local modifications before resetting or discarding a working tree. A portable patch of the earlier implementation snapshot was saved separately and checked against the base above. Apply it only to a clean checkout, not over an existing branch containing the implementation.

Companion artifacts are committed under `output/cem-glider/`: `mirum-cem.patch`, `implementation-status.md`, `verification.md`, `performance-report.json`, `glider-screening-example.zip`, and `glider-example.png`/`.pdf`. A fresh agent should check out `engineering/cem-glider` directly. The portable patch is an alternative for reconstructing the earlier implementation snapshot from its original base; it is not needed when using this branch. Historical verification notes retain the implementation-time statement that the changes were then uncommitted.

Read, in order:

1. `CLAUDE.md` and applicable repository instructions.
2. `docs/implementation/cem-roadmap.md` — the original user-supplied plan.
3. `docs/implementation/cem-glider.md` — implemented scope and qualification status.
4. This handoff, then the source files for the next slice.

The next recommended slice is **finish physical sizing and closure in G2/G3**, followed by new shape discovery in G4. The user's priority is discovering new shapes, so make the closure work support that goal rather than expanding only the conventional layout indefinitely.

## User decisions to preserve

- The example is **distance in metres**. Maximize distance from a 10 m launch; 1200 m is a reference stretch goal.
- A reference target is advisory and must never stop maximization. `minimum_performance` is a separate optional hard requirement evaluated at P10.
- Still air is the default. Wind and rising-air environments require explicit selection.
- Compare sheet foam, balsa, FDM LW-PLA, FDM PLA and hybrid construction. `auto` attempts all processes; an explicit no-admitted-design result is valid.
- Discover new shapes rather than only tuning one fixed layout.
- For future ambiguous text requests, ask for clarification instead of guessing units, launch method or intent.
- Rocket chamber CEM is later work, R1. Do not change `deploy/*` branches for this task.

## What is implemented

The reachable flow is:

```text
strict typed mission
  -> analytic feasibility / reference gap / quantified levers
  -> process-aware seeded search and MAP-Elites archive
  -> select an admitted elite
  -> build closed outer-mould-line meshes and stock spar geometry
  -> independently import the exact binary STLs
  -> measure mass properties under explicit material laws
  -> re-run trim, stability and performance with measured mass properties
  -> rebuild/re-measure if trim changes, up to three attempts
  -> uncertainty + input/artifact/version-bound screening report and ZIP
```

The physics path does not call an LLM. The optional text translator returns either `needs_clarification` or `review_requirements`; it does not launch a search automatically or supply physics evidence.

Current supported layouts are conventional, T-tail and V-tail. The archive uses the existing seeded random/local sampler and keeps elites in family/aspect-ratio/wing-loading/sweep cells. It does not yet implement novel-family geometry, B-spline/CST controls, gradient refinement or independent solver qualification.

The legacy glider request now supports distance and duration objectives. The CEM path adds launch energy, atmosphere, calibrated drag factors, wind-aware speed-to-fly and uniform-uplift trajectory semantics. A headwind can remove forward ground progress without removing time aloft; regression tests cover that distinction.

Current design results are `screened` or `rejected`. These fields deliberately remain false:

```json
{
  "accepted_for_manufacture": false,
  "engineering_certified": false,
  "manufacturing_ready": false
}
```

## Source map

| File | Responsibility |
| --- | --- |
| `backend/cem/core.py` | Strict base model, canonical JSON, digests, mission identity and CEM protocol |
| `backend/cem/provenance.py` | Implementation-source and runtime fingerprint; cached once per process |
| `backend/cem/glider/requirements.py` | Typed mission, launch/environment/process/family inputs and compute bounds |
| `backend/cem/glider/physics.py` | Launch energy, atmosphere and ground trajectory calculations |
| `backend/cem/glider/feasibility.py` | Optimistic sampled-model bounds, gap and levers; search always continues |
| `backend/cem/data/airfoil_bounds.json` | Versioned offline NeuralFoil envelope with source metadata |
| `scripts/cem/build_airfoil_bounds.py` | Deterministically rebuilds that envelope offline |
| `backend/cem/glider/processes.py` | Process assumptions, catalogue rod bending sizing and manufacturing screens |
| `backend/cem/glider/model.py` | Existing glider solver adapter, spar mass, station confidence/Re admission |
| `backend/cem/glider/discovery.py` | Process comparisons, archive cells and elite revisions |
| `backend/cem/glider/geometry.py` | Closed OML construction, STL export and bounded independent mesh measurements |
| `backend/cem/glider/closure.py` | Measured-mass re-trim, fixed placement and measured spar-section check |
| `backend/cem/glider/uncertainty.py` | Seeded 200-sample local polar propagation |
| `backend/cem/glider/calibration.py` | Immutable calibration snapshots and identifiable profile-drag update |
| `backend/cem/calibration/glider.json` | Initial engineering priors, explicitly not flight calibrated |
| `backend/cem/glider/service.py` | Discovery/build orchestration, bounded correction, report/ZIP packaging and stage adapter |
| `backend/cem/evidence.py` | Parent-side report, archive, part, revision and ZIP validation |
| `backend/cem/api.py`, `worker.py`, `app.py` | HTTP jobs, capped worker and provider-free host |
| `backend/solver_slot.py` | Shared expensive-job admission with the legacy glider workflow |
| `frontend/cem_ui.py` | Mission form, feasibility, process comparison, archive chart and build/download flow |
| `backend/tests/test_cem_*.py` | CEM requirements, API/evidence, real solver and mesh regression tests |

Existing files changed include `backend/gliders/spec.py`, `search.py`, `api.py`, `model/analysis.py`, `model/assembly.py`, `backend/main.py`, `frontend/app.py`, `requirements-glider.lock`, `Dockerfile.verify` and `README.md`.

## Important limits and open engineering issues

### Physical geometry and structure are incomplete

Wing, tail and pod meshes are outer mould lines. Their mass comes from a **skin-equivalent areal-density law**, not fabricated material volume. The stock rod uses measured solid volume. Payload, ballast and hardware are point masses. This is useful screening evidence but is not full as-built closure.

`SizedGlider.spar()` sizes a uniform catalogue rod against the permitted maximum aircraft mass and launch load factor. It does not yet iterate local spar sections against the converged design's actual load distribution.

`geometry.build_meshes()` emits a **straight Y-axis stock rod**. Do not assume it follows sweep or polyhedral dihedral, fits inside every wing section in 3D, or has a qualified connection to the wing. Current section-thickness screening is not an installed-path or joint/load-transfer check. Add explicit containment, clearance and load-path tests before promoting any physical assembly claim.

The delivered rod's polygon section inertia is recovered from its measured solid inertia for the bending check. That check does not establish torsion, joints, buckling, fatigue, spar deflection or landing/gust qualification.

The closure reanalysis uses measured mass/CG/inertia with the original parametric aerodynamic shape. It does not independently reconstruct aerodynamic sections from delivered geometry. Tail-incidence correction rebuilds and remeasures the artifacts; do not attach new trim results to stale geometry.

### Uncertainty and calibration are bounded initial implementations

The 200 samples perturb drag, lift limit, efficiency, mass and launch efficiency at fixed trim CL. They are not 200 complete re-trim/stability solves. Failed stall samples count as zero performance. These are engineering priors, not flight-validated confidence intervals.

The flight-test endpoint currently supports still-air drop flights with range and speed-compatible duration, updating only the identifiable profile-drag multiplier. It does not jointly identify launch efficiency, CLmax, Oswald efficiency, mass and drag from one range/time observation. Additional evidence must identify each added coefficient.

To use an updated prior, submit the returned calibration hash on the next mission. There is no silently changed global active calibration and no automatic archive re-score. Calibration storage needs persistence across container replacement; job IDs and in-memory job membership do not survive a server restart.

### Feasibility is a sampled-model screen

The envelope was generated from NeuralFoil, not independently cross-validated against live XFOIL. It uses optimistic AR/e/drag assumptions and is not a universal physical impossibility proof.

If performance or mass calibration changes, the current finite envelope is invalidated: the report becomes `marginal`, `optimistic_bound` becomes null and `bound_state` indicates regeneration is required. Preserve this behavior until a calibrated bound is actually rebuilt.

### Novelty and advanced environments are unavailable

Unsupported families must remain explicit capability gaps. Do not relabel conventional aircraft as tailless/tandem/novel or rank unverified novelty as a win. Thermal and ridge inputs are modelled but generation rejects them; no bounded lift-region model exists yet. Sustained uniform uplift is reported without inventing infinite range or a finite duration bound.

## Recommended next work, with acceptance criteria

### 1. Finish G2/G3 for one physical construction family

Start with a bounded sheet/balsa or hybrid vertical slice; retain the same typed mission and job/evidence interfaces.

- Size local spar sections from converged loads and material properties, with explicit material data, safety factors and joint assumptions.
- Build the installed spar path, slots/channels, ribs/skin, payload bay and ballast pocket. Include clearance and stock/process restrictions inside admission.
- Measure actual fabricated material volume, CG and inertia from closed part geometry. Add hardware/payload/ballast separately with recorded placement.
- Independently recover/check aerodynamic sections and planform from delivered geometry, then re-run trim, stability and performance.
- Preserve the bounded correction loop and final artifact binding.

Acceptance: injected 20% mass error fails; malformed/open or extra/unaccounted parts fail; spar outside the intended wing or missing load-path evidence fails; geometry changes invalidate prior evidence; corrected geometry is remeasured; final mass is within the declared tolerance and measured-section structural checks pass. Do not globally mark all processes fabrication ready when only one family has evidence.

### 2. Add independent solver checks and complete uncertainty

- Rebuild/cross-validate the airfoil envelope with actual XFOIL at relevant station Reynolds numbers.
- Recheck top candidates with actual XFOIL station polars and AVL trim/stability/modes. Test shims and analytical fallbacks must never count as independent solver evidence.
- Set explicit convergence/agreement thresholds and record executable/model versions and input hashes.
- Run seeded full trim/stability solves for uncertainty samples, or retain an explicitly scoped approximation alongside a separately qualified ensemble.

Acceptance: missing solver, nonconvergence, insufficient station evidence or excessive solver disagreement produces failed/unverified evidence. An injected low-Re drag hole is caught before the candidate is ranked as a verified gain. Bands repeat with the same seed and account for rejected samples.

### 3. Expand G4 shape discovery

- Add family builders and family-specific admission, starting with tailless swept, reflex plank and tandem/canard; box/joined wings require a joint load-path model.
- Add shared spanwise B-spline chord/twist/dihedral/sweep controls and constrained CST/Kulfan airfoils.
- Extend the existing archive; add Opti refinement and a discrete/failed-gradient fallback where appropriate.
- Record novelty distance from an actual conventional comparator and gains relative to uncertainty.

Acceptance: deterministic archive for the same input/seed/version; at least three qualified families represented; invalid reflex/washout, stall ordering and untrusted model regions fail admission; independent recheck catches injected exploitation; no unsupported family is silently substituted. Explain if the best design does not beat the comparator.

### 4. Implement G5 manufacturing builders

- Add a strict, bounded `GliderPartsGeometrySpec` and trusted PicoGK builder for printed shells, graded infill/ribs, spar channels, cavities and printer-bed segmentation.
- Extend sheet/balsa templates to match the physical construction and new families.
- Keep Python acceptance authoritative. Validate bounded mesh budgets, manifoldness, components/part accounting, minimum walls and process limits; bind manufacturing output to measured performance.

Acceptance: deterministic artifacts within one pinned build image; one qualified physical part/component accounting model; measured mass within the declared 5% initial tolerance; 3 GiB worker cap; rejected or incomplete builds cannot produce accepted downloads. PicoGK output is STL/3MF, not a fabricated STEP/B-rep claim.

### 5. Complete G6/G7/G8 and integration

- Connect archive-cell selection to the explorer while preserving exact evaluated revision and evidence scope.
- Validate live Gemini translation with the configured provider. Keep clarification and explicit requirement review before physics jobs.
- Collect real flight data, add identifiable coefficient fits and immutable observation provenance, then implement archive re-scoring with old-version reproducibility.
- Add thermal-column and ridge-region models, including reachability, lift-region extent, time horizon and range assumptions. Report bounded ranges when evidence supports them.
- Build the clean verification image, run the complete offline gate and follow the repository's commit/PR/review workflow.

## Reproduce the current workflow

The workspace environment used `/workspace/.venv-glider/bin/python`. From the repository root:

```sh
PYTHONPATH=backend OPENBLAS_NUM_THREADS=1 \
  /workspace/.venv-glider/bin/python -m uvicorn cem.app:app \
  --host 127.0.0.1 --port 8013

BACKEND_URL=http://127.0.0.1:8013 PYTHONPATH=backend \
  /workspace/.venv-glider/bin/python -m streamlit run frontend/app.py \
  --server.address 127.0.0.1 --server.port 8513
```

These ports were still listening when this handoff was checked; inspect them before starting another instance. Separate older local Mirum services use ports 8000/8501. Process state is ephemeral and does not prove persistence or production deployment.

Select **Physics-based glider CEM**. The provider-free host works for typed missions; live optional text extraction was not validated. For another machine, use the normal pinned project environment plus `requirements-glider.lock`; the workspace-specific venv path is not portable.

```sh
curl -s http://127.0.0.1:8013/cem/glider/feasibility \
  -H 'Content-Type: application/json' \
  --data-binary @examples/cem/glider-distance.json

curl -s http://127.0.0.1:8013/cem/glider/discover \
  -H 'Content-Type: application/json' \
  --data-binary @examples/cem/glider-distance.json
```

Poll `GET /cem/glider/jobs/{job_id}` every 2–3 seconds. Submit `POST /cem/glider/design` with the discovery job ID and an elite ID from that archive. Poll the new design job and download `GET /cem/glider/jobs/{design_job_id}/bundle` only after screening succeeds.

Other routes: `GET /cem/glider/metadata`, `POST /cem/glider/requirements`, `POST /cem/glider/flight-test`.

## Verification evidence already retained

| Check | Actual result |
| --- | --- |
| Full offline source-overlay native run | 979 passed, 27 failed because Node was absent, 1 live test deselected |
| Native recheck with Node, CEM and explorer tests | 70 passed, including two added headwind regressions |
| Unique selected checks across the two runs | All 1,008 have a passing result; this was not one clean 1,008-pass image-build run |
| Existing workspace glider suite | 76 passed |
| Real HTTP flow | Feasibility → all-five-process discovery → build/correction → evidence validation → ZIP download passed |
| Synthetic calibration HTTP flow | New version and old-version identity passed; synthetic calibration removed from the API store afterward |
| Headless Chromium/Streamlit feasibility flow | Passed, no browser errors |
| Security review | No unresolved critical/high findings in the reviewed change; automated Bandit/pip-audit/Ruff scans were unavailable |

The verification used the existing `mirum-backend:local` image, ID:

```text
sha256:223706364f7ebaaf10d9e0157436b9f06150437a97221cf890bb0e6fbc79299f
```

It mounted source read-only, disabled network, used unprivileged UID 1000, a 4 GiB container limit and two CPUs. The recheck additionally mounted the existing Node binary. `Dockerfile.verify` now copies Node from the Node 22 Bookworm image because the browser/canonical geometry tests require it. A clean build of that modified Dockerfile has not been established.

Logs/scripts, if still available, are in ignored `work/`: `native-verification.log`, `native-recheck.log`, `verification.log`, `http-final-smoke.log`, `ui-final-smoke.log`, `calibration-http-final.log`, `http_smoke.py`, `ui_smoke.py` and `calibration_http_smoke.py`. These are supporting scratch evidence, not durable release assets or production services.

Focused command:

```sh
OPENBLAS_NUM_THREADS=1 /workspace/.venv-glider/bin/python -m pytest \
  backend/tests/test_cem_requirements.py backend/tests/test_cem_api.py \
  backend/tests/test_cem_pipeline.py backend/tests/test_glider_model.py \
  backend/tests/test_glider_search.py backend/tests/test_glider_explorer.py \
  backend/tests/test_glider_flight_conditions.py -q
```

For the clean offline image, use the repository's `Dockerfile.verify` instructions and the managed environment's proxy/CA guidance. Keep TLS verification enabled. Docker's filesystem copies temporarily exhausted this small workspace disk during verification; wait for/remove only your own test containers rather than pruning unrelated images or stopping the existing local Mirum services.

No live-provider generation quality, independent XFOIL/AVL agreement, real flight validation, fabricated part qualification, clean dependency-image build or production deployment is claimed.

## Reference example

The retained eight-evaluations-per-process, seed-42, 10 m drop / 60 g payload run found archive cells in foam, balsa, LW-PLA and hybrid; ordinary PLA had no admitted design. These are a small-budget search result, not a global optimum.

The selected foam example:

| Field | Value |
| --- | --- |
| Mission output ID | `62aa4aaf9641` |
| CEM version | `mirum-cem-glider-0.1.0+2a60707013b2` |
| Calibration | `b1fa2cf84e57e195ef6ec77a66612aefb6d2a60dc7e1f2dc1de6b73576fa87b7` |
| Final design revision | `502be5fc987b85c3d6ee88e36e1bedbe038ef1acfce80e955e1d09ce10691631` |
| Nominal distance | About 124.92 m |
| Distance P10 / P50 / P90 | 107.30 / 124.01 / 144.26 m |
| Equivalent measured mass | 0.18572 kg |
| Mass difference from sizing | About 0.70% |
| Correction attempts | Two; tail incidence rebuilt from about −3.165° to −2.118° |
| Reference gap to 1200 m | About 1075.08 m at nominal prediction |

Preserve these as historical source/runtime-bound results. A changed implementation or runtime changes the fingerprint and may change the output ID; do not hard-code this ID into future tests.

## Invariants and practical traps

- Never let an LLM calculate acceptance physics or produce executable geometry-builder code for this path.
- Missing/nonfinite/stale/incomplete evidence must fail closed. Keep parent validation as well as worker-side checks.
- Preserve strict schemas, compute caps, rate limits, credential-free worker environments, shared solver admission and cancellation cleanup.
- Keep exact archive/elite membership, parameter revisions, per-part measurements, ZIP report equality and download hash validation. A plausible identity/status alone is not evidence.
- Do not lower Re/confidence/stall/structural gates to make search return something. The current station floor is Re 30,000 and confidence 0.8.
- The original legacy glider export had open/inconsistently oriented meshes unsuitable for volume measurement. Reuse the closed CEM construction/measurement path or independently prove a replacement; do not quietly repair missing geometry inside the verifier.
- STL units are millimetres; aerodynamic calculations and measured properties use SI. Preserve the explicit 0.001 import scale.
- Use fresh job/output directories. Reusing a completed directory can contaminate packaging with stale files.
- `provenance()` is cached per process. Restart API and worker hosts after source changes so cached parent identity and worker fingerprints agree.
- Mission IDs identify a mission/version; elite IDs and final design revisions distinguish designs within it. Flight evidence must keep the final design binding.
- Persist calibration snapshots when required. Restarting the server loses in-memory job membership even if artifact files remain.
- Expensive mesh/solver steps must remain bounded. No uncapped large mesh verification or full archive runs.

## First task for the next builder

Create one reviewable G2/G3 slice that installs a physically coherent spar/skin/rib construction for a supported family and measures its actual material mass, CG, inertia and installed section/load-path evidence. Add adversarial geometry/mass tests and keep `accepted_for_manufacture=false` until that particular construction passes all declared physical/process gates. Then use this closure path to support genuinely new G4 shapes.
