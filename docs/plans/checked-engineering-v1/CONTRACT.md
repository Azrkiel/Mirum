# V1 capability and delivery contract

## Product result and scope

Support one homogeneous solid rectangular cantilever, positive XYZ datum, ideal clamp at X=0, static transverse +Z end force at X=L. Length and width are fixed; thickness is fixed or selected from at most four reviewed values. Preserve the current CantileverSpec and verifier envelope, including length at least ten times both section dimensions, the existing digital dimensional-tolerance checks, small-displacement/small-strain policy, equilibrium, independent geometry measurement and beam benchmark checks.

Holes, fillets, additional bodies, varying cross-sections, other loads/supports, shear deformation, fatigue, buckling, contact, thermal analysis, general 3D stresses, physical material qualification, manufacturing variation and standards certification are not supported claims.

Two entry routes are required: provider-free typed preparation and AI-assisted preparation. Both use the same reviewed brief, trusted fixed builder and acceptance checks. Existing general text-to-CAD remains a separate capability.

## Reference acceptance example

| Input | Value |
|---|---|
| Length / width | 200 / 10 mm |
| Reviewed thicknesses | 5, 10, 15 mm |
| Tip force / maximum displacement | 50 N / 1 mm |
| Elastic modulus / allowable stress | 70,000 / 120 MPa |
| Digital dimensional tolerance | 0.01 mm |
| Material source | Explicitly labelled illustrative values, not certified stock properties |
| Assumptions | Ideal clamp, homogeneous linear elasticity, static end force |

Expected model results: 5 mm → displacement about 18.285714 mm, stress 240 MPa, rejection; 10 mm → 2.285714 mm, 60 MPa, rejection; 15 mm → 0.677249 mm, 26.666667 MPa, acceptance. A model-envelope rejection is also a valid failure reason for a too-flexible candidate. These are model predictions, not measurements of a manufactured beam.

## Brief and identity boundary

A finalized EngineeringBrief contains:
- Schema, capability and policy versions.
- Fixed geometry requirements and digital tolerances.
- Load/support assumptions, physical properties, allowable and their material reference.
- Fixed-design or bounded-sizing mode and normalized reviewed thickness choices.
- Required check identifiers and explicit acknowledgement of all proposed values/assumptions.

Normalize quantities into mm, N and MPa. Only documented conversions may be offered, and their result must be reviewed. Kg is not automatically N; require an explicit force or acknowledged mass-to-force conversion. Do not infer a safety factor or silently certify user-provided allowables.

AI suggestions are proposals until reviewed. Finalization records the exact expected draft revision. Edits create a new revision. Conflicts or unsupported capability/policy versions require resolution before running.

Use full SHA-256 identities:
- fixed_requirements_sha256 covers normalized fixed requirements and capability/policy versions;
- brief_sha256 additionally covers mode and normalized candidate choices;
- candidate_sha256 identifies the complete selected CantileverSpec;
- artifact digests identify actual delivered bytes.

Store provenance, input mode, record IDs and timestamps outside the semantic hashes. This allows equivalent AI/form inputs to have the same meaning while retaining different histories. Existing legacy spec digests remain compatible. Digests establish consistency, not external authenticity or physical proof.

## Candidate policy

Validate all choices before native execution. New V1 preparation sorts/deduplicates proposed choices and displays the resulting set before finalization; invalid values are rejected rather than clipped. Execution accepts only the finalized list, evaluates ascending thickness and stops at the first passing candidate. The label is “smallest passing thickness in the reviewed set.”

No hidden objective, global optimality claim, provider call, candidate expansion or physical requirement change is allowed during execution. A required-stage failure stops as unavailable; an ordinary candidate rejection may advance to the next reviewed choice. The old /engineering/generate endpoint retains its caller-order behavior.

## Outcome policy

Lifecycle: admitted → running → packaging → completed. Unavailable and cancelled are alternative terminal states. Unsupported requests stop during preparation.

Engineering outcome: accepted, rejected or not_evaluated. A completed job may have a rejected outcome. Missing/unavailable mandatory evidence cannot produce accepted. A cancellation request before terminal commit wins; after commit, return the existing terminal state without rewriting it.

Every check records identifier, state, method/version, measurements with units, comparison limits and relevant input/artifact identities. V1 beam acceptance requires all checks declared by its versioned capability. Geometry-only capabilities may leave physics unevaluated but must preserve their narrower claim.

## Bundle and publication

Accepted ZIP payload:
- brief.json
- selected_spec.json
- model.py
- model.step
- preview.glb
- attempts.json
- report.json
- report.md
- manifest.json

The preview is generated from the accepted STEP. Do not rerun the source to recreate files after verification. Capture and publish the accepted bytes.

The manifest lists payload paths, types, sizes and hashes excluding itself. Store manifest/ZIP hashes outside the archive in the run record. Reports inside the ZIP must not contain circular archive/manifest hashes. The external run record is the delivery envelope.

Rejected/unavailable results can publish diagnostics with no accepted STEP/GLB. Partial geometry is not offered through accepted-download URLs. Atomic required-file publication precedes the accepted database commit. Recovery may delete orphan unpublished bundles but cannot invent missing evidence.

Use a dedicated engineering namespace and format allowlist. Existing legacy artifact eviction must not sweep it. Validate all filenames/formats; never accept client filesystem paths.

## Execution, storage and deployment budgets

Initial limits, versioned and confirmed against B01:
- At most four candidate attempts; one active engineering job and no hidden queue.
- Share relevant native admission with the legacy engineering route.
- Preserve existing 30-second native-stage deadlines and 1.5 GiB subprocess limits.
- Overall run deadline: 420 seconds after admission, including bounded cleanup/packaging.
- AI preparation: one call plus at most one repair, each 45 seconds; 8,000-character prompt and 16 KiB structured response bounds.
- Complete stored run including archive: at most 32 MiB.
- At most 50 retained artifact-bearing runs and 512 MiB engineering storage.
- Bundles available for six hours; compact state/expiry records retained for seven days.

Reserve capacity at admission, clean expired data first, and reject excess admissions rather than evict unexpired results. Downloads acquire a lease so expiry cannot invalidate an active stream. After expiry return explicit expired status. Full prompts/private input payloads expire with their bundle; routine logs must not contain keys or unrestricted prompts.

SQLite and artifacts live under an application-owned mounted volume within the permitted runtime data tree; native scratch remains disposable. Record behavior for process restart and container replacement. Single API process/replica only until distributed ownership exists. Test coexistence with enabled legacy workflows and reject unsupported launch configuration.

Budgets do not permit relaxing physical requirements or numerical tolerances. If B01 shows a budget insufficient, document evidence and version the operational policy before dependent implementation.

## Versioned API

Prefix: /engineering/v1.

| Method / path | Behavior |
|---|---|
| GET /capabilities | Supported descriptors and availability |
| POST /drafts | Create typed or AI-assisted draft |
| GET /drafts/{id} | Read draft and revision |
| PATCH /drafts/{id} | Expected-revision edit |
| POST /briefs | Finalize exact reviewed draft revision |
| GET /briefs/{id} | Read finalized immutable brief |
| POST /jobs | Submit finalized brief ID plus idempotency key |
| GET /jobs/{id} | Read lifecycle, progress and result |
| POST /jobs/{id}/cancel | Cancel nonterminal work |
| GET /jobs/{id}/artifacts/{format} | Download only available allowlisted files |

Job submissions cannot override finalized physical values. Same idempotency key/content reuses the job; conflicting content returns 409. Use 422 for invalid/unsupported structured requests, 429 for admission/rate limits, 409 for state/revision conflicts, 410 for known expired artifacts and 503 for unavailable preparation/runtime services. A started job records later execution failure in its terminal state. Polling never triggers new execution.

Apply the existing deployment access-control policy. This V1 is trusted local/controlled use, not a new multi-tenant hosting guarantee.

## Reproducibility and release boundary

Replay the saved finalized brief using the pinned builder/verifier/runtime without AI. Require matching verdicts and physical measurements within declared tolerances. Independent STEP exports may contain metadata/timestamps, so do not promise identical bytes unless tested explicitly.

Typed-only operation can be demonstrated before live-provider qualification. Complete V1 requires B01–B14 and V01–V15 with actual live-provider evidence. New physical/manufacturing claims require the later family-specific experimental gates.
