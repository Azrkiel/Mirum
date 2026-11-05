# CO2 dragster generation: proposed project scope

Date: 2026-11-05. Status: draft for user review; implementation has not started.

## Goal

Extend Mirum to generate editable CO2 dragster bodies inspired by the supplied showroom photo, with repeatable dimensions, browser previews, and downloadable STEP geometry.

The user selected **editable 3D dragster bodies and CAD exports** as the first-release priority. This is a scope and design proposal, not a code-complete execution plan. After design review, break the accepted scope into test-first implementation tasks.

## Repository findings and baseline

- GitHub repository: https://github.com/Azrkiel/Mirum. `git ls-remote origin` confirmed remote `main` at `36b996f91b0c17c54710a24545cce6acc131ff1f`; the locally available `origin/main` matches that SHA. GitHub connector calls were unavailable and the web fetch failed; remote state was verified through Git, and files were inspected with `git show origin/main:<path>`.
- `C:/Users/jiang/Mirum` is currently on `integration/text2cad-hardening`, commit `fed8aae9c1142792640019fa12e9ff39cd30bf41`. This checkout and GitHub main have diverged. Do not implement by replacing main with this checkout or broadly merging all local worktrees.
- The local worktree inventory includes glider, propulsion, physics, and validation branches. Their presence does not imply all their features are shipped. This proposal uses the inspected GitHub main as its code baseline.
- Existing support is partial: `backend/classifier.py` lists CO2 dragsters under Domain C; `backend/strategies.py` contains `rule_co2_dragster`; `backend/cad_utils.py:make_co2_void` creates a cylindrical cutting tool. Its defaults, 19.5 mm diameter and 52 mm depth, are existing code values, not a verified cartridge standard or competition requirement.
- The current strategy asks the model to construct a body, position the cartridge cut, and cut axle holes. There is no dedicated dragster specification, deterministic body builder, or dragster-specific acceptance suite in the inspected main tree. The classifier's dragster test stubs the model response; it does not establish CAD generation quality.
- Main already provides `verification/acceptance.py:accept_script`, a STEP-derived GLB preview, and `artifacts.py` serving accepted STEP/GLB bytes through `/download/{run_id}/{fmt}`. Reuse those seams. The default local checkout lacks some of this newer infrastructure.
- The material table has no balsa or basswood entries and falls back to aluminum for unknown materials. A dragster mass estimate must not silently inherit that fallback.

## Approaches considered

| Approach | Benefit | Cost / limitation |
| --- | --- | --- |
| **Parametric builder plus text and form inputs — recommended** | Repeatable geometry; direct dimensional editing; testable cavities and axle layout; no provider call needed for form-based generation | Initial design space is limited to supported styles and parameters |
| Expand the current Domain C prompts | Smallest initial change; wide stylistic freedom | Continues to depend on model-written lofts, feature placement, and boolean reliability |
| Full race-design and optimization system | Could compare designs against a measured racing setup | Needs hardware data, calibrated drag/rolling resistance/thrust models, and a much larger validation effort |

Architecture: convert input into one canonical `DragsterSpec`, then use a deterministic CadQuery builder. Both the dedicated editor and text workflow call the same service. Retain Domain C for compatibility; identify the dragster product intent before generic domain classification so words such as “aerodynamic” or “CO2-powered” do not redirect the body into wing or nozzle generation.

Tech stack: existing Python, Pydantic, CadQuery/OpenCASCADE, FastAPI/SSE, Streamlit, Babylon.js, and Linux/Docker native verification. No new geometry engine or frontend framework.

## First-release experience

1. Choose **CO2 dragster** in Mirum's workflow selector, or describe a CO2 dragster in Text to CAD.
2. Choose one of three supported styles: low wedge, smooth teardrop, or narrow-waist body with a raised rear cartridge housing. The photo supplies visual direction: tapered nose, flowing waist, substantial rear housing, and exposed wheel positions.
3. Edit dimensions, axle positions, cartridge-pocket dimensions, and shape controls. Show units and every assumed/default value. The photo does not establish scale or hidden cavity dimensions.
4. Generate and inspect the body in the existing orbit viewer. Display volume, measured dimensions, check results, and mass only when a suitable density was supplied.
5. Change parameters and regenerate. Export STEP, GLB, the resolved parameter JSON, and a reproducible CadQuery script.

The first release delivers a **single body solid**. Wheel dimensions define the placement/clearance envelopes; separate wheels, axles, spacers, and cartridge solids are a later assembly feature. The body should resemble the photo's body shapes, not reproduce its paint, lettering, or an entire showroom scene.

“Editable” means the user can reopen the saved parameter JSON in Mirum, regenerate the same design, edit the script, or import the STEP solid into another CAD program. STEP does not preserve Mirum's parameters as a native feature-history tree in another CAD program.

## Included scope

### Canonical design specification

- Versioned spec in millimeters; fixed frame: X from nose to rear, Y transverse, Z upward, with ground at Z=0. Place body geometry above the requested ground clearance.
- Style, body length/max width/max height, nose and waist proportions, rear-housing dimensions, and bounded longitudinal section controls. Cross-section topology stays consistent within each style; arbitrary free-form surfaces are outside this release.
- Front/rear axle X positions, shared axle height, bore diameter, wheel diameter/width, and wheel-to-body side clearance. Axle holes run along Y; the cartridge cavity opens at the rear and extends toward the nose along -X.
- Cartridge pocket diameter/depth, fit allowance, vertical center, minimum surrounding body thickness, and minimum separation from axle bores. These describe an external holder cavity, not a pressure vessel or a cartridge redesign.
- Optional stock bounding box and explicit material density. Report whether mass is body-only, and leave it unavailable when density is missing. Do not infer wood strength or a manufacturing approval from geometry.
- Explicit supplied/defaulted value provenance. Reject nonfinite, negative, contradictory, and out-of-bounds inputs; identify missing required dimensions instead of silently inventing manufacturing dimensions.

### Geometry and acceptance

- Build a smooth symmetric body using bounded section profiles and stable loft/boolean operations. Preserve enough rear material to contain the requested pocket; cut both axle bores from the completed body.
- Require one valid connected body solid, positive finite volume, expected overall dimensions, open through-axle bores, and a rear-open cartridge pocket with the requested depth and location.
- Check wheel/ground/body clearance, bore-to-pocket separation, rear-pocket surrounding thickness, and stock containment when stock dimensions are supplied. Define numerical tolerances and the exact supported envelope in the geometry spike. If a thickness check uses bounded sampling, expose that method and its limits; do not describe it as a whole-body manufacturing proof.
- Run checks against the captured STEP revision. Reuse `accept_script` for native geometry acceptance, then complete dragster-specific measurements before publication. Preview, report, and downloads must refer to that same revision and spec digest.
- A failed or unavailable required check blocks new accepted outputs. Keep the last accepted model visible, with its original parameters/report/downloads; show the new attempt's error separately.
- STEP and GLB are required deliverables. Treat failure to store either as a failed new result, rather than inheriting the generic pipeline's optional-download fallback.

### Inputs, refinement, and exports

- Dedicated parameter editor works without an LLM/API key. A text adapter extracts only supported dragster fields, shows the resolved parameters/defaults, and routes into the same builder. Unsupported shapes or ambiguous/conflicting requests return a useful clarification; no silent fallback to generic body code.
- Recognize `CO2`, `CO₂`, and `carbon dioxide` dragster wording. Do not route a pinewood derby car, a full-size race car, or a standalone cartridge/nozzle request into this workflow merely because the existing keyword rule is broad.
- Support typed parameter changes and text refinements that resolve to parameter changes. Edits regenerate and revalidate the body. Arbitrary source edits may use existing manual execution, but must clear dragster-specific evidence unless the edited result is rechecked against a resolved spec.
- Deliver accepted STEP and GLB through the existing artifact store. Let the UI download resolved spec JSON and source as well. Reopen the JSON through the editor with schema/version validation. Source reproduction requires the matching Mirum helper version, recorded with the spec; geometric equivalence, not byte-identical STEP serialization, is the reproducibility criterion.

## Proposed flow and failures

```text
[Text prompt / follow-up] -> [Intent + field extraction] --provider failure--+
                                       |                                  |
[Dragster editor / saved JSON] ---------+                                  v
                                       v                        [Explain; form still usable]
                        [Resolve and show DragsterSpec]
                                       |
                        [Validate dimensions and relationships]
                           | invalid / unsupported -> [Field error / clarification]
                           v
                        [Bounded CAD worker: deterministic body]
                           | timeout / CAD failure -> [Attempt failed]
                           v
                        [Capture STEP; native + dragster checks]
                           | missing / failed check -> [Attempt failed]
                           v
                        [STEP-derived GLB + report + revision binding]
                                       |
                        [Publish required STEP and GLB]
                           | storage failure -> [Attempt failed]
                           v
                        [Replace accepted view; enable downloads]

Any failed attempt -> retain prior accepted view, evidence, and downloads.
Parameter edit -> mark displayed result as prior revision until regeneration succeeds.
Expired artifact -> explain expiry; regenerate from saved parameters.
```

## File responsibilities

Paths below are proposed additions or edits relative to GitHub main, not existing implemented APIs.

| Path | Responsibility |
| --- | --- |
| `backend/dragsters/spec.py` (new) | Canonical spec, units, parameter bounds, cross-field validation, version/digest, resolved-value provenance |
| `backend/cad_dragster.py` (new) | Deterministic style/body/cavity/bore geometry; single geometry implementation shared by generated source and service |
| `backend/dragsters/validation.py`, `validation_worker.py` (new) | Measure captured STEP against spec in a bounded subprocess; dimension, cavity, bore, and clearance report |
| `backend/dragsters/service.py`, `api.py` (new) | Source generation, acceptance/check orchestration, required artifact publication; `POST /dragsters/generate` streaming result |
| `backend/dragsters/requirements.py` (new) | Product-intent detection and supported text-to-spec/parameter-patch extraction |
| `backend/main.py`, `schemas.py`, `agents.py`, `classifier.py`, `strategies.py` | Attach dedicated route; adapt existing text flow/requirements/refinement; replace brittle dragster instruction; retain unrelated domain behavior |
| `backend/script_policy.py`, `compiler.py` | Narrowly permit the geometry helper through existing AST/audit/subprocess controls; check both policy layers |
| `frontend/dragster_ui.py` (new), `frontend/app.py` | Workflow entry, three presets, parameter editor/import/export, viewer, revision-aware state and checks |
| `backend/artifacts.py`, `verification/acceptance.py` | Reuse accepted STEP/GLB storage and capture; only small changes if needed for report/revision plumbing; no generic store rewrite |
| `backend/tests/test_dragster_spec.py`, `test_dragster_geometry.py`, `test_dragster_validation.py`, `test_dragster_api.py`, `test_dragster_routing.py` (new) | Contract, native CAD, negative geometry, API lifecycle, intent/extraction coverage |
| `frontend/test_dragster_state.py` (new) | Failed generation, edited parameters, saved-spec reload, and stale-result/download behavior |
| `backend/eval_harness/prompts.json`, `README.md`, `architecture.md` | Representative dragster prompts, workflow documentation, current capabilities/limits |

Keep density reporting dragster-specific initially; do not change the global unknown-material fallback as an unrelated migration. Do not add a new domain letter just for this product family.

## Delivery phases and rough effort

Estimates are engineering effort for one developer, not measured generation performance or a delivery-date promise. Include focused tests and normal fixes in each phase.

| Phase | Work and exit condition | Effort |
| --- | --- | --- |
| 0. Baseline and geometry spike | Start an isolated branch from verified current main; record source state; prove a native STEP/GLB export for each style and establish supported bounds/tolerances | 0.5–1 day |
| 1. Spec and presets | Validate units, frame, dimensions, hardware envelopes, material handling, parameter import/export, and explicit defaults | 1 day |
| 2. Deterministic body builder | Three style families, cartridge cut, axle bores; nominal and boundary cases generate one valid body solid | 2–3 days |
| 3. Measurement and delivery | Verify captured geometry, bind evidence to spec/revision, block failures, deliver actual accepted STEP/GLB bytes | 1.5–2 days |
| 4. Editor and preview | Working parameter editor, preset selection, JSON reload, viewer/downloads, and previous-result preservation | 1–1.5 days |
| 5. Text generation and refinement | Supported dragster prompts and parameter edits route to the same builder; negative intent cases preserve existing workflows | 1–1.5 days |
| 6. Regression and visual review | Offline native gate, browser checks of all styles, live extraction sample if available, documentation and example exports | 1 day |

**Working range: 8–11 engineering days**, plus up to 2 days contingency for loft robustness or integration issues discovered in phase 0. Reassess scope after the spike if the style families cannot fit the existing native worker budgets.

Build a vertical slice first: one wedge body, form input, axle/pocket checks, and accepted STEP download. Then add the other two styles and text routing. This tests the full workflow before expanding shape variety.

## Completion criteria and verification

- All three style presets generate valid single-solid bodies; a native matrix covers each style at nominal and supported size/shape boundaries, using hardware dimensions that remain feasible for each case.
- Negative cases explicitly reject a pocket breaking through the exterior, axle/pocket intersection, axle outside the body, negative ground clearance, excessive wheel interference, disconnected body, impossible stock envelope, NaN/Infinity, and inconsistent units.
- STEP reimport meets declared dimension tolerances. Cavity/bore tests deliberately use misplaced or missing cuts so tests cannot pass solely because the spec looked valid.
- Native checking and geometry generation run in owned, bounded processes. Timeout, disconnect, concurrent requests, and storage failure do not publish a successful new result or mix artifacts across requests.
- A user can change parameters, save/reload JSON, regenerate equivalent geometry, and download STEP/GLB matching the current accepted revision. Prior downloads cannot appear to represent edited parameters.
- Form-based generation makes zero provider calls. Text-routing tests cover supported extraction, unsupported changes, provider errors, CO2/CO₂ aliases, and non-dragster intent. Live provider success is measured separately from mocked/offline tests.
- Review nose, waist, rear housing, cavity opening, and axle locations in side/top/isometric views for each style against the reference's broad shape cues.
- Run targeted dragster suites during implementation, then the existing `Dockerfile.verify` offline native gate. Expected outcome: exit 0 with required native tests executed; record actual counts/skips when run. Planning has not run these tests or demonstrated a generated dragster.

## Deferred scope

- Separate wheels/axles/spacers and a fully constrained assembly; hardware BOM and component downloads.
- STL and DXF production exports. Main's accepted artifact store currently supports only STEP/GLB; adding mesh or drawing exports needs its own same-revision acceptance design. STEP is sufficient for the initial CAD-editing goal.
- Competition legality, rule profiles, minimum racing mass, and official dimensions. Add only after the user selects an organization/event/year and supplies or identifies its current rules.
- Race-time estimates, thrust modeling, rolling resistance, CFD, shape optimization, and claims of fastest design. Existing wing/XFOIL analysis is not a dragster performance model.
- CNC toolpaths, fabrication drawings, 3D-print strength qualification, and physical cartridge-holder testing.
- Automatic reconstruction of arbitrary photos; paint/decal generation; arbitrary free-form topology; native SolidWorks/Fusion feature-history export.

No further product choice is required to finish this scope proposal. Design review should confirm the three initial styles and the body-only release. Implementation then needs numeric preset bounds and tolerances established by the spike; competition rules and race-performance data are not blockers for this first release.
