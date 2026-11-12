# Full reviewed implementation plan

This is the detailed planning narrative from the design discussion. [CONTRACT.md](CONTRACT.md), the [task cards](tasks/) and [ACCEPTANCE.md](ACCEPTANCE.md) turn it into executable work. Completion is tracked only in [STATUS.md](STATUS.md). Original checklist markers below are deliberately noninteractive to prevent two competing status trackers.

Packaging clarifications: B05 delivers a service/form-data preparation path, B11 exposes its transport and B12 implements the full UI. B09 defines the durable publisher seam; B10 supplies atomic bundle publication. No production capability is claimed by this plan.

**Mirum build plan — version 1**

Prepared November 12, 2026. Repository baseline: Azrkiel/Mirum, commit d4189b6190c94d3069d9f5c364a7a722178dd775. This plan is based on source inspection. It does not claim that the current application, its dependencies, or its recorded tests have been independently reproduced.

**1. The desired result**

A person describes a supported engineering problem or fills out a form. Mirum turns that input into an explicit design brief. The person reviews the dimensions, load, material properties, assumptions, and allowed design changes. Mirum then builds candidate parts, measures the actual exported geometry, runs the applicable calculations, and delivers either an accepted design package or an explanation of why no candidate was accepted.

First release: one solid rectangular beam fixed at one end, under one static transverse force at the other end. Length and width stay fixed; thickness may be selected from at most four reviewed values. This is the existing cantilever capability. The product statement is “checked against the stated idealized beam model.” Physical manufacturing, assembly performance, and standards compliance remain separate claims.

Two input routes reach the same execution path:
- A structured form that works without an AI provider.
- An AI-assisted brief that extracts requirements and proposes candidate thicknesses, followed by review.

AI suggests inputs. Existing trusted code constructs and checks the part. V1 does not execute AI-written Python in this workflow. General text-to-CAD remains a separate capability.

**2. What the user receives**

An accepted result includes:
- A browser preview of the accepted part, generated from the accepted STEP revision.
- STEP geometry, the generated construction script, and the reviewed brief.
- The final candidate specification and all candidate results, including rejected candidates.
- A plain-language report and machine-readable report: requested dimensions, measured dimensions, predicted bending and stress, limits, assumptions, and checks that were not performed.
- Version and artifact information identifying the exact inputs, builder, verifier, solver, runtime, and files. Digests establish file consistency; they are not physical test evidence or third-party certification.
- One downloadable ZIP package containing the complete record.

A rejected run includes its brief, candidate history, and diagnostic report. It has no accepted CAD download. A timeout, missing solver, or failed file write is reported as unavailable; it is not a claim that the design fails physics. Unsupported requests are identified before generation.

**3. A reference demonstration**

Use an explicit illustrative material record, not an inferred aluminium grade:
- Length 200 mm; width 10 mm.
- Candidate thicknesses 5, 10, and 15 mm.
- End force 50 N; maximum tip displacement 1 mm.
- Elastic modulus 70,000 MPa; allowable bending stress 120 MPa.
- Digital dimensional tolerance 0.01 mm.
- Ideal clamp, homogeneous linear-elastic material, static end load.

These are the inputs already exercised by the repository's engineering service tests. The expected result is rejection at 5 and 10 mm and acceptance at 15 mm, with about 0.67725 mm displacement and 26.66667 MPa root bending stress. Implementation must reproduce these results in the pinned native runtime; the plan is not fresh test evidence.

The UI must identify these material values as illustrative. A real design requires an explicit material/allowable reference. The application must not select a grade, safety factor, load, or support condition on the user's behalf.

**4. Fixed product and architecture decisions**

- Reuse CadQuery/OpenCASCADE, the existing beam solver and native verifier, FastAPI, and Streamlit.
- Extend the CEM-style interface with a small adapter and capability registry. Keep each family's specialized specification.
- Use a new versioned EngineeringBrief containing fixed requirements, allowed candidate values, assumptions, material reference, required checks, capability version, and a specification digest.
- Support fixed-design evaluation or bounded thickness selection. Sort and deduplicate reviewed candidates before finalization. Reject invalid values; do not clamp them.
- Choose the smallest reviewed thickness that passes by evaluating in ascending order and stopping at the first accepted candidate. Describe it as the smallest passing option in the reviewed set. Do not claim a global optimum.
- Every candidate must satisfy the existing slender-beam envelope, including length at least ten times width and height. Preserve the verifier's small-displacement, small-strain, topology, numerical-tolerance, equilibrium, and benchmark checks.
- Support finite quantities in mm, N, and MPa internally. Display any supported unit conversion before review. A mass in kg is not automatically a force in N; require an explicit, reviewed conversion or force value.
- Permit one AI preparation call and one schema-repair call per draft revision, each with a 45-second deadline. Bound prompt size to 8,000 characters and structured response size to 16 KiB; record token usage when supplied. No model calls occur during an approved V1 execution. Subsequent clarifications create new draft revisions.
- Limit execution to four unique candidates, one active engineering job, and a documented total deadline. No automatic increase of limits or requirements.
- Keep the existing /engineering/generate API working. Both APIs should reuse the same verified core; the old caller-supplied candidate ordering stays compatible.
- V1 runs in a trusted local or controlled deployment, with one API process/replica. Multi-user SaaS, distributed workers, and arbitrary generated-code hosting require separate work.
- No new geometry kernel, custom programming language, model training, Zoo integration, or broad new physics solver is required for V1.

**5. The shared records**

EngineeringBrief: the immutable, reviewed problem and the allowed choices. A material reference records its source and values; it does not certify the material. The fixed-requirement digest covers normalized fixed inputs and capability/policy versions; the brief digest additionally covers allowed candidate choices. Keep input-mode provenance, record IDs, and timestamps outside these semantic digests so equivalent form and AI inputs can be compared. Acceptance is against this reviewed brief, not an assertion that arbitrary prose was interpreted perfectly.

CapabilityDescriptor: a capability's identifier/version, supported inputs, physical assumptions, required checks, possible exports, runtime requirements, and resource limits.

CandidateSpec: a validated part specification derived from one reviewed brief. Every candidate gets its own digest. The fixed-requirement digest must remain unchanged.

CheckResult: check name, passed/failed/not_evaluated/unavailable state, measured value and unit, comparison limit, method/version, and source artifact digest where applicable.

EngineeringRun: job ID, brief digest, capability and policy versions, attempt order, lifecycle state, outcome, timestamps, failures, and bundle information.

Lifecycle: admitted, running, packaging, then completed, unavailable, or cancelled. Engineering outcome is accepted, rejected, or not_evaluated; unsupported requests are stopped during preparation. Cancellation after a terminal commit returns the existing terminal result. Before that commit, cancellation wins and unpublished artifacts are removed.

Keep lifecycle separate from engineering judgment. A job can finish successfully and conclude “none of the tested candidates passed.” Mandatory unavailable or unevaluated checks prevent acceptance. A geometry-only capability can legitimately leave physics unevaluated, but cannot issue a physics-accepted claim.

**6. Build checklist**

Each item is a separate reviewable implementation unit. Leave the checkbox open until its acceptance conditions have been exercised.

- **B01 — Establish a reproducible baseline.** Depends on: none.

Build: record the selected commit, inspect repository instructions, use an isolated implementation branch, and reproduce the existing cantilever tests and native HTTP example in a pinned Linux/OCP environment. Record current failures separately from new failures. Confirm the available model configuration only when testing the AI path.

Done when: the existing typed example passes; an invalid dimension and a failed physical requirement return no accepted geometry; the exact runtime and commands are recorded. If the existing baseline fails, repair and verify it before relying on it. Do not treat old handoff reports as current proof.

- **B02 — Define V1 scope and capability metadata.** Depends on: B01.

Build: the cantilever capability card, supported-unit policy, required-check list, export list, selection rule, and all computation/storage limits. Include the family exclusions: holes, fillets, varying sections, extra bodies, other supports, other loads, fatigue, buckling, contact, thermal loads, manufacturing variation, and general 3D stress.

Done when: each exclusion has a clear unsupported response and is represented in the validation fixtures. Product copy describes the actual evidence.

- **B03 — Create the reviewed brief and versioned result schemas.** Depends on: B02.

Build: strict brief, candidate, run, check, and capability records using the current Pydantic conventions. Separate requirements that cannot change from the candidate thickness list. Add canonical serialization, full digests, schema version, field-level origin, and explicit material references.

Done when: missing loads, unknown units, nonfinite numbers, booleans masquerading as numbers, unknown fields, conflicting dimensions, and unsupported assumptions cannot become executable briefs. Equivalent normalized briefs have the same digest. Any meaningful edited requirement invalidates previous review. Execution also checks the finalized capability and policy versions; an unavailable version requires a new reviewed brief, never a silent substitution.

- **B04 — Add the deterministic cantilever adapter.** Depends on: B03.

Build: adapt the existing engineering specification, generate_verified service, beam solver, and verify_step into the common capability interface. Translate candidate thickness into a complete existing CantileverSpec. Preserve all current native measurements and acceptance checks.

Done when: the reference example reaches the same verdict and measurements through both the old route and the adapter. The adapter declares its limited claim. Unsupported geometry cannot inherit the cantilever check label.

- **B05 — Build the form-based brief preparation path.** Depends on: B03, B04.

Build: a form/API path that creates, edits, validates, and finalizes a brief without provider calls. Show fixed dimensions, loading/support assumptions, material source, candidate values, and limits together. Finalization stores the exact reviewed revision.

Done when: the whole typed path works offline; missing information is identified field by field; execution cannot use an unreviewed or superseded brief.

- **B06 — Add AI-assisted brief preparation.** Depends on: B05.

Build: a constrained model response that proposes only supported structured fields, uncertainty/missing-field questions, and up to four candidate thicknesses. Use the existing provider configuration. Preserve the prompt and model provenance in the private run record with bounded logging. Validate every model response with the same schemas.

Done when: malformed responses, extra fields, guessed material properties, unsupported features, contradictions, and out-of-range candidates cannot start execution. A model outage leaves the form path available. The user sees and can correct every inferred field before finalization.

- **B07 — Implement bounded candidate evaluation.** Depends on: B04, B05.

Build: deterministic iteration over the finalized candidate list, with one immutable requirements digest, per-candidate specifications and reports, and early stop after the first passing candidate. Continue after an ordinary physics rejection. Stop as unavailable if required geometry/solver evidence cannot be obtained.

Done when: the reference example selects 15 mm; all-fail returns “no candidate in the reviewed set passed”; single-value mode evaluates exactly one part; attempts cannot change load, material, length, width, limits, or assumptions. No re-asking the AI or unreviewed search expansion occurs during execution.

- **B08 — Centralize the outcome policy.** Depends on: B03, B04, B07.

Build: one policy evaluator that reads capability-required checks and returns accepted, rejected, unavailable, or unsupported with a precise scope. Verify the expected brief, candidate, STEP, and report digests. Never infer physics acceptance from a general geometry certificate.

Done when: missing/failed/unavailable mandatory checks, stale reports, mismatched artifacts, unsupported claim fields, and forged pass flags cannot produce acceptance. AI explanations cannot override machine evidence. Legacy generated-CAD results retain their existing narrower claims.

- **B09 — Add durable execution and cancellation.** Depends on: B07, B08.

Build: a small persistent job record store using SQLite and filesystem artifacts under a dedicated, application-owned data directory, with a mounted writable volume in the release setup. A process restart and a container replacement must have explicitly tested behavior. Keep native scratch separate. Reuse ProcessRunner for owned subprocess cleanup. Permit one active engineering job, reject excess admissions with retry information, and share that admission limit with the legacy engineering route. Persist each candidate outcome before beginning the next one.

Done when: cancel, worker timeout, process crash, restart, duplicate submission, and simultaneous cancellation/completion have defined outcomes. Restart marks abandoned work unavailable and removes unpublished staging files. Cancellation kills owned descendants and releases capacity. Use one API process/replica until shared coordination is implemented. Fail startup for an unsupported multi-process configuration. Reconcile filesystem and database after crashes: unpublished bundles are removed; completed records missing required files become unavailable. No accepted record is committed before bundle publication.

Initial execution policy: preserve each existing 30-second native-stage timeout and 1.5 GiB subprocess cap, at most four candidates, and a 420-second overall run deadline after admission. Baseline measurements must confirm this budget covers the fixed workload; budget changes require a new policy version, never a hidden retry increase. Include coexistence testing with other enabled workflows.

- **B10 — Build complete, atomic result packages.** Depends on: B08, B09.

Build: capture the exact accepted STEP and its STEP-derived preview; create the brief, selected specification, script, attempt history, reports, artifact manifest, and ZIP without re-running the CAD source. Reuse the existing artifact publication pattern in a dedicated engineering storage namespace, with an explicit per-capability allowlist and atomic required-bundle publication. The legacy sweeper must not evict engineering bundles.

Done when: all required files exist and their digests match before a run is published as accepted. A disk-full or missing-report failure cannot leave a partially accepted result. Rejected/unavailable jobs can export diagnostics without accepted CAD.

The current artifact store supports only STEP, GLB, spec, and report. Package/archive/script/history support must be implemented and tested explicitly; it is not automatically available.

Bundle layout: brief.json, selected_spec.json, model.py, model.step, preview.glb, attempts.json, report.json, report.md, and manifest.json. The manifest hashes all payload files except itself. Store the manifest and ZIP digests in the run record outside the archive, avoiding circular hashes. Rejected packages omit model.step and preview.glb and identify that no design was accepted.

Storage policy: at most 32 MiB per complete run, 512 MiB total, 50 retained runs, and six-hour bundle availability. Reserve capacity at admission; reject new work rather than evict unexpired results. Keep compact job/expiry records for seven days. Expiry respects active downloads, removes payloads, and returns an explicit expired response afterward. UI shows the expiry time and tells users to save their ZIP. Requests and full prompts expire with their bundle; avoid dumping them or credentials into routine logs.

- **B11 — Expose versioned APIs.** Depends on: B05, B06, B09, B10.

Build the following endpoints under /engineering/v1: GET /capabilities; POST /drafts; GET and PATCH /drafts/{id}; POST /briefs to finalize an exact draft revision; GET /briefs/{id}; POST /jobs; GET /jobs/{id}; POST /jobs/{id}/cancel; GET /jobs/{id}/artifacts/{format}. Draft edits and finalization require the expected revision, with conflicts returning 409. Use typed requests for both form and AI flows. Submitting a job references an immutable finalized brief; the job request cannot supply replacement physics values.

Done when: authentication/rate-limit behavior matches the existing deployment policy; idempotency returns the original job for the same request and rejects key reuse with different content; API errors distinguish invalid/unsupported input, overload, expired results, and unavailable execution. Polling does not re-run generation. Existing endpoints remain covered by compatibility tests. Use 422 for invalid/unsupported structured requests, 429 for admission/rate limits, 409 for version/content/state conflicts, 410 for known expired artifacts, and 503 for unavailable runtime/provider services. Nonterminal polling returns job state without blocking; already-started jobs expose execution failures in their terminal records.

- **B12 — Add the complete user workflow.** Depends on: B11.

Build: a Streamlit “Checked beam design” workflow with form or AI entry, missing-information questions, a review screen, start/cancel, progress, preview, comparison of candidate results, and downloads. Display fixed support and force direction visually or in a clearly labelled sketch.

Done when: users can complete the reference task without API knowledge; stale screen state cannot submit old approvals or show an old preview as a new result; rejected, unsupported, unavailable, cancelled, and expired cases each have actionable messages. The result says which beam-model checks passed and which broader checks were not performed.

- **B13 — Build a regression and failure-case suite.** Depends on: B06 through B12.

Build: fixture-driven extraction tests; schema/policy tests; native geometry and physics tests; real HTTP/API tests; job lifecycle and artifact tests; browser tests. Add deliberately wrong-but-kernel-valid shapes, missing evidence, and stale artifact fixtures. Reuse existing tests instead of duplicating their implementation.

Done when: all mandatory release cases below pass. Offline tests use recorded provider outputs. A separate bounded live-provider evaluation must pass before the AI-assisted feature is labelled available. Native geometry checks must exercise real CadQuery/OCP.

- **B14 — Package and document the first release.** Depends on: B13.

Build: pinned runtime/dependency setup, offline typed smoke example, live-AI configuration instructions, operator troubleshooting, capability documentation, API examples, and a concise release evidence record. State local artifact expiry and how to save a package permanently.

Done when: a fresh environment can run the documented reference example; downloaded geometry/report/preview identify one revision; restart and expiry behavior are verified; the UI's advertised capabilities match the enabled backend. Deployment/publication remains a separate action when requested.

**7. Minimum release checks**

V01: Form and AI-assisted input produce the same fixed-requirement and brief digests and the same candidate selection when their normalized requirements, choices, and versions match. Their provenance records may differ.

V02: The 200 x 10 mm reference example rejects 5 and 10 mm thickness and accepts 15 mm within the existing numerical tolerances.

V03: A too-low allowable or too-tight deflection limit rejects every candidate with measured reasons and no accepted STEP/preview.

V04: Missing load, ambiguous units, kg described as force, contradictory requirements, and unknown material source require clarification.

V05: A request for holes, a second load, a different support, or a non-slender section returns unsupported without silently dropping the feature.

V06: Native checks reject valid-but-wrong CAD: offset/rotated prism, wrong size, missing material, extra solid, and changed root section.

V07: Missing solver, nonfinite solver output, malformed worker report, timeout, or failed required check cannot produce acceptance.

V08: Changing a finalized brief, candidate, report, STEP, or preview breaks evidence association and prevents acceptance or delivery.

V09: Editing input invalidates the old review; concurrent browser tabs and duplicate clicks cannot launch a different unreviewed request.

V10: Cancel, restart, deadline, overload, and duplicate-idempotency cases leave no orphan native processes or partial accepted bundle.

V11: Failed file write, capacity exhaustion, expired files, and archive download errors are visible and cannot return a misleading accepted package. Unexpired accepted bundles survive unrelated legacy sweeps.

V12: A replay uses the saved reviewed brief and pinned implementation without calling the AI. Verdicts and measured properties agree within declared tolerances. Raw STEP/ZIP bytes may contain timestamps or metadata; do not promise identical file hashes across independent regenerations unless explicitly established. Digests always identify the exact delivered revision.

V13: Existing engineering and general CAD routes retain their documented behavior and cannot accidentally acquire broader physics claims.

V14: Browser checks cover the accepted preview, report, all downloads, every failure outcome, and page refresh during a job.

V15: AI evaluation uses a fixed 30-case set: 10 complete supported requests, 10 incomplete/ambiguous requests, and 10 unsupported requests. Require zero silent requirement drops or unreviewed execution, and at least 8 of the 10 complete cases to yield an accurate reviewable draft. Remaining supported failures must be explicit and recoverable through the form. This is a defined benchmark, not proof that arbitrary natural-language requests will always be interpreted correctly.

**8. Growth after V1**

- **E01 — Integrate the existing PicoGK capability.** Depends on: B14.

Wrap the current typed specification, builder, verifier, and reports in the same records and job API. Register its own voxel tolerance, resource limits, runtime/library versions, and STL export capability. Keep its geometric checks and label intact. Use an explicit minimal worker environment. Require fresh native builder/verifier runs and known-defect fixtures; stored historical reports alone do not qualify the adapter. Do not advertise editable STEP or thermal/flow/structural acceptance for this capability.

- **E02 — Integrate the existing glider CEM.** Depends on: B14.

Map its mission requirements, discovery, geometry closure, measured-mass evidence, calibration version, and uncertainty into the common records. Preserve screened/rejected outcomes, manufacturing-acceptance restrictions, and unsupported-family rejection. Test that the common policy never upgrades screening evidence to a manufacturing or physical-validation claim.

- **E03 — Add a second structural family.** Depends on: B14.

Choose and document one bounded family with a domain engineer. Define the requirements, geometry predicates, material and load model, fixtures, independent benchmarks, manufacturing needs, and desired output before coding. A plate/bracket requiring 3D stresses needs a suitable mesh and solver, convergence studies, load/support mapping to actual geometry, and treatment of stress singularities. It cannot inherit beam validation. Release only when it passes the same input-to-evidence loop and its own qualification gates.

- **E04 — Establish physical and manufacturing evidence.** Depends on: B14 for the beam; each later family requires its own evidence.

For the beam, build measured specimens at several reviewed thicknesses, document stock/material and fixture conditions, measure deflection under controlled loads within the model's range, and compare with predictions. Define measurement uncertainty and acceptance limits before testing. Record discrepancies, including clamp compliance and manufacturing variation. Bind results to the tested drawing/CAD revision. Repeat validation after model changes. For manufacturing readiness, add the process-specific tolerances, inspection instructions, material records, and responsible engineering release appropriate to the application.

- **E05 — Broaden AI design freedom and optional backends.** Depends on: qualified capability contracts from E01–E04 as relevant.

Allow AI to choose among registered, supported families and propose richer parameter sets. Execute only within each capability's admissible design space. Consider Zoo as an optional backend when a specific needed capability justifies it; verify API access, licensing, costs, and export fidelity first. Preserve backend-native representations and explicit conversion error. A new custom language becomes a separate decision after several real capabilities reveal common concepts.

**9. Implementation map**

Existing code to reuse: backend/engineering/spec.py, service.py, beam_fea.py, verify.py and api.py; backend/verification/acceptance.py; backend/process_runner.py; backend/artifacts.py; and the conventions in backend/cem/core.py. Keep the existing glider CEM contract compatible.

Suggested additions, with exact names adjustable during B01:
- backend/engineering/contracts.py: brief, result and check schemas (B03).
- backend/engineering/capabilities.py and cantilever.py: registry and adapter (B02, B04).
- backend/engineering/briefs.py and proposer.py: preparation and AI extraction (B05, B06).
- backend/engineering/search.py and policy.py: bounded search and acceptance (B07, B08).
- backend/engineering/jobs.py and store.py: durable lifecycle and storage (B09).
- backend/engineering/bundles.py: reports, manifests and ZIPs (B10).
- backend/engineering/api_v1.py plus route registration: versioned API (B11).
- frontend/engineering_ui.py plus workflow registration: interface (B12).
- backend/tests/test_engineering_* and frontend tests: behavioral regressions (B13).

These are suggested new modules, not claims that they currently exist. Keep the beam equations and geometry verifier in their existing modules to avoid creating competing implementations.

**10. Completion and sequencing**

V1 is complete only when B01–B14 and V01–V15 are complete, with any intentional AI unavailability clearly disabling that entry path. The typed release may be usable before live-provider qualification; it must not be represented as the complete AI-assisted release.

Follow the dependency order. A practical build sequence is B01–B05, B07–B10, B06 and B11–B12, then B13–B14. This establishes the fully typed execution path before adding language uncertainty.

The first implementation ticket is B01. The first visible milestone is the form-based reference example through the common adapter. The second is the same task through AI-assisted brief preparation. The expansion stages are separate releases; they do not delay an honestly scoped beam release.

Do not promise delivery dates until B01 establishes the runtime and baseline. Estimate tickets after that checkpoint, including native tests and UI verification. The plan identifies what to build and what proves it works; the work has not been performed yet.

**11. Second-pass review — completed**

The draft was reviewed against the repository interfaces and the full input-to-download lifecycle. The following omissions or ambiguities were resolved in this revision:

| Review finding | Resolution | Build / verification |
|---|---|---|
| Acceptance meant different things across workflows | Separate lifecycle, engineering outcome, and capability-specific check scope | B03, B08 / V03, V07, V13 |
| Editing a brief or changing a capability version could reuse stale review | Immutable finalized revisions, conflict checks, version pinning | B03, B05, B11 / V08, V09 |
| “Pick the best” could imply unrestricted optimization | At most four reviewed thicknesses; ascending first-pass rule | B02, B07 / V02, V03 |
| AI outages or invented inputs could block or weaken the typed workflow | Strict schemas, explicit missing-input handling, offline form path | B05, B06 / V04, V05, V15 |
| Existing storage could not deliver the promised full package | Explicit archive formats, isolated namespace, atomic publication | B10 / V08, V11 |
| Manifest and archive hashes could refer circularly to themselves | Payload manifest inside; manifest/ZIP digests outside | B10 / V08 |
| “Persistent” records could disappear with a container | Mounted writable data volume and restart reconciliation | B09, B14 / V10 |
| Cancellation could race accepted publication | One terminal commit with defined cancellation precedence | B09 / V10 |
| Capacity eviction could break the promised download window | Admission reservation; no early eviction; active-download handling | B10 / V11 |
| “Repeatable” could incorrectly promise identical STEP bytes | Separate numerical reproducibility from exact artifact identity | B14 / V12 |
| New sorting behavior could break the old engineering API | New adapter policy preserves legacy caller-order behavior | B04, B07 / V13 |
| AI/form provenance could prevent comparison of equivalent briefs | Hash normalized meaning separately from input-mode provenance and timestamps | B03 / V01 |
| Mocked AI tests could be mistaken for live-provider evidence | Separate native/offline and live-provider release gates | B13 / V15 |

Remaining prerequisites are explicit: B01 must establish a working native runtime; AI availability requires configured provider access and the live benchmark; actual design material/load data must come from the user or an identified source. E03 must select its next family before implementation, and E04 needs a physical test setup. These do not silently broaden V1's claims.

This review closes the planning gaps identified here. Implementation may reveal additional issues; every such issue must either be resolved before its affected acceptance gate or recorded as a specific release blocker. No claim of universal engineering correctness or a guaranteed hole-free implementation is made.

Source anchors at the reviewed commit:
- [Existing typed specification](https://github.com/Azrkiel/Mirum/blob/d4189b6190c94d3069d9f5c364a7a722178dd775/backend/engineering/spec.py)
- [Deterministic service and bounded feedback](https://github.com/Azrkiel/Mirum/blob/d4189b6190c94d3069d9f5c364a7a722178dd775/backend/engineering/service.py)
- [Native geometry and beam verification](https://github.com/Azrkiel/Mirum/blob/d4189b6190c94d3069d9f5c364a7a722178dd775/backend/engineering/verify.py)
- [Artifact store](https://github.com/Azrkiel/Mirum/blob/d4189b6190c94d3069d9f5c364a7a722178dd775/backend/artifacts.py)
- [CEM interface](https://github.com/Azrkiel/Mirum/blob/d4189b6190c94d3069d9f5c364a7a722178dd775/backend/cem/core.py)
- [Existing cantilever acceptance tests](https://github.com/Azrkiel/Mirum/blob/d4189b6190c94d3069d9f5c364a7a722178dd775/backend/tests/test_engineering_service.py)
