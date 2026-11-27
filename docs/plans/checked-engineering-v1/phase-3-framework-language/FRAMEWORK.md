# Framework and adapter specification

## End-to-end architecture

Input (.mir / form / AI proposal)
→ parse or normalize
→ typed ProgramIR
→ capability/backend negotiation
→ immutable requirement/check plan + candidate construction plans
→ user review of exact semantics and execution lock
→ owned bounded worker execution
→ independent reread/measurement + registered physics checks
→ atomic evidence package and revision publication.

A language frontend cannot bypass the existing reviewed brief. AI is a proposal source. Builders construct geometry, inspectors measure artifacts, solvers evaluate declared physics, and the acceptance policy alone determines which claims the evidence supports.

Frozen in F01 (see [F01-DECISIONS.md](F01-DECISIONS.md) D3): the AI edit loop is built offline and fixture-driven first (F08 tools, F09 loop). The agent may edit construction only, never requirements. Any live qualification needs its own owner authorization, call budget and tag namespace.

## Required records

ProgramIR contains schema/version, named design, capability version, fixed physical requirements, candidate space, material/source references, assumptions, requested exports, semantic feature IDs and source-span mapping. Keep build operations and check requirements separate: operation success does not establish requirement satisfaction.

OperationDescriptor contains operation ID/version, typed arguments, dimensional units, preconditions, output types, supported representations and backend requirements. V0.1 uses a small registry of supported capability build/inspect/analyze/export actions. It is not a universal CAD feature editor.

Frozen in F01 (see [F01-DECISIONS.md](F01-DECISIONS.md) D1): F02 reserves, without implementing, the RequirementPlan record separate from a ConstructionGraph, and OperationDescriptor entries for sketch, extrude, revolve, sweep, loft, fillet/chamfer, shell, pattern and boolean. The typed construction block is MEL v0.2, specified after the v0.1 slice.

Frozen in F01 (see [F01-DECISIONS.md](F01-DECISIONS.md) D7): a span-tagged operation stream and an ArtifactGraph (operation to faces, edges and solids with source span) are F02 records and F03 adapter outputs.

Frozen in F02 (see [F02 handoff](handoffs/F02.md)): record names and versions are ProgramIR `mirum_program_ir_v0_1`, semantic payload `mirum_semantics_v0_1`, OperationDescriptor registry `mirum_operation_registry_v0_1`, ExecutionLock `mirum_execution_lock_v0_1`, ConstructionGraph `mirum_construction_graph_v0_1` (empty in v0.1), OpStream `mirum_op_stream_v0_1` and ArtifactGraph `mirum_artifact_graph_v0_1`, in backend/engineering/program/. The v0.1 ExecutionLock pins IR, semantics and registry versions, capability id/version, numerical and resource policies, backend and exports; native library, solver, representation and tolerance fields are added under a new lock version by F03/F05.

Frozen in F05 (see [F05 handoff](handoffs/F05.md)): ExecutionLock `mirum_execution_lock_v0_2` (record `ExecutionLockV0_2` in program/kernel.py) carries every v0_1 field plus `language_version` "0.1", `compiler_version` `mirum_mel_compiler_v0_1` and the embedded native `BackendLock`. Its `registry_version` is the default v0_2 registry. A mock lock cannot be embedded. `ir.load_execution_lock_json` reads v0_1 and v0_2 by `schema_version`. A v0_1 lock stays readable but cannot authorize a run. The run gate (`program/planning.py`) needs four things before any adapter build: a finalized brief whose digest equals the compiled brief_sha256, an equal semantic hash, a v0_2 lock hash, and an equal lock from a fresh probe. Otherwise it returns `unavailable`/`lock_mismatch`, which means re-review. The registry exports are `mirum_operation_registry_v0_1.json` (REGISTRY_V0_1, byte-identical to the F02 export) and `mirum_operation_registry_v0_2.json`.

ExecutionLock pins language/compiler/IR versions, capability/numerical/resource policies, backend/native library/build versions, required solver versions, representations, tolerances and selected exports. Resolve it before review. Native deployment changes are detected by a fresh availability probe; incompatible locks require re-review or an explicitly documented reproducible old environment.

CheckPlan contains requirement IDs, measurement predicates, thresholds/units, supported semantic bindings, analysis method, coverage and required/unavailable policy. Requirements originate in reviewed meaning. Building a different shape never changes these thresholds.

CheckEvidence records actual artifact hash, measured values, errors/tolerances, method/version, geometric bindings, result and explicit coverage. Use passed/failed/not_evaluated/unavailable states and preserve claim scope.

Revision contains parent revision, source hash, semantic hash, reviewed brief identity, execution lock hash, attempt history, native artifact IDs, checks and publication state. Source/provenance hashes are separate from normalized semantics; whitespace/comments need not revoke semantic approval, but an edited load, material, candidate set or backend/tolerance policy must invalidate applicable review.

## KernelAdapter protocol

- describe(): backend ID/version, capabilities, representation, export/import formats and tolerance conventions.
- probe(): observed availability/native versions and runtime requirements; metadata alone is not readiness.
- supports(build_plan, evidence_plan): explicit compatibility or structured rejection with reasons.
- build(build_plan, context): bounded private artifacts and semantic-binding candidates.
- inspect(artifact, measurement_plan): independently reopen actual output and report measurements/uncertainty.
- export(artifact, format, policy): supported serialization/conversion with fidelity metadata.
- cleanup/cancel hooks: use existing owned-process lifecycle rather than detached native tasks.

Execution context includes locked versions, deadlines, memory/output budgets, cancellation, workspace scope and provenance. Backends do not decide engineering acceptance or mutate requirements. No credential inheritance beyond a documented necessary subset.

Frozen in F03 (see [F03 handoff](handoffs/F03.md)): the protocol lives in backend/engineering/program/kernel.py (records version `mirum_kernel_v0_1`: BackendDescription, ProbeResult, SupportDecision, ExecutionContext, BuildPlan, BuildResult, InspectResult, ExportResult, BackendLock) with adapters in backend/engineering/program/adapters/ (`occt`, `picogk`, `mock`; `adapter_for` never falls back to another backend). probe/build/inspect are async; cancellation propagates through the existing ProcessRunner/JobRunner lifecycle. Outcomes are `unsupported`, `unavailable` and `precision_failure`, each with its own reason allowlist. Every build runs `require_executable` over the plan's OpStream before any probe or native call. Mock records are labelled `mock` and cannot be completed, accepted, exported or locked. Native versions, representations and tolerances go in `BackendLock`; ExecutionLock v0.1 is unchanged. Operation registry `mirum_operation_registry_v0_2` adds `capability.build/inspect/export@0.2.0` for regen_chamber@1.0.0; `REGISTRY_V0_1` stays byte-identical.

Initial backends: OCCT via existing CadQuery for native B-rep; PicoGK for its existing voxel-derived geometry/STL path. A third backend, `mirum-native` (the Mirum kernel), is registered for `editable_plate@1.0.0` only, under `mirum_kernel_v0_2` records and operation registry v0_6. It is selected only by an explicit plan, never as a default or fallback, and its STEP is always remeasured by the OCCT plate worker. A geometry adapter is distinct from a CapabilityAdapter such as beam, regen geometry or glider CEM; retain the domain adapter as planner/analysis coordinator above it.

Frozen in F01 (see [F01-DECISIONS.md](F01-DECISIONS.md) D2, D8): the native B-rep binding for v0.x is CadQuery/OCP; build123d is evaluated in F03 conformance. The two qualified backends are beam (rectangular_cantilever) on OCCT (CadQuery) and regen_chamber on PicoGK. glider_cem is a domain capability, not a kernel; rectangular_plate and dragster come later.

## Representation and conversion rules

B-rep, meshes and volumetric representations carry different fidelity and editability. An adapter must declare which operations and measurements it supports. Do not manufacture STEP by merely wrapping a tessellation and claim parametric editability.

For a conversion, preserve source/target hashes, units/frame, operation parameters, numerical tolerances, topology changes and measured deviation where supported. Reject a requested evidence claim that cannot survive conversion. Retain backend-native artifacts and the accepted representation. Independent reread may use the same kernel library, so it detects generation/delivery inconsistencies rather than proving independent mathematical correctness.

Conformance tests: analytical simple shapes for supported overlap, bounds/volume/units/orientation, boolean behavior where implemented, degenerate geometry, serialization/reread, supported measurement error, limits, cancellation and missing runtime. Compare geometry within declared tolerances, not raw serialization bytes. Never require a mesh to preserve a B-rep feature tree.

## Semantic identity

Use logical names such as beam.root_section and beam.free_end, each bound to measured geometry under a capability-specific predicate. After rebuilding, rebind and validate uniqueness/shape/orientation; an old face index is insufficient. Ambiguous or missing identities block a required load/measurement. Imported geometry lacking bindings is limited to checks actually possible. Drawing tags are inspiration, not a solution to arbitrary 3D topology changes.

## Transaction and evidence policy

Reuse B09/B10 jobs, admission, persistent storage and publication. Stage complete revisions privately; failed batches do not advance the current accepted pointer. Preserve previous accepted revisions and their evidence. A compare view must pair each report with its own geometry.

Rollback selects an existing immutable historical revision and records a new history event/pointer with its original requirements/lock. It does not assert that it satisfies the current brief. Expired or altered artifacts cannot be relabelled as restored; a rebuild is a new checked run. Crash markers and journal transactions reconcile file/database state; cancellation cannot expose partial accepted work.

Frozen in F06 (see [F06 handoff](handoffs/F06.md)): revisions are `mirum_program_revision_v0_1` with a `mirum_program_journal_v0_1` journal and `mirum_revision_diff_v0_1` compare records, in backend/engineering/program/ (`revisions.py` pure records, `revision_store.py` additive tables with an append-only journal and CAS head, `transactions.py` `RevisionService`). A run is `authorize_run` -> `jobs.admit` -> link -> `jobs.start` on a fresh B09/B10 job; promotion, restore and the head move happen in one store transaction and never fall back to an older revision. Restore adds a new row that serves the target's own validated artifacts and evidence, and never extends expiry, probes or re-assesses; expired, missing or altered artifacts give typed refusals, and rebuild is a new checked run. A stale parent conflicts. Payloads are compacted after the 7-day state retention (hashes kept). There is no runtime wiring until F08.

Frozen in F07 (see [F07 handoff](handoffs/F07.md)). `program/evidence.py` defines CheckPlan `mirum_check_plan_v0_1` from reviewed inputs and DeliveredEvidence `mirum_delivered_evidence_v0_1` with geometry-only beam feature bindings. `program/delivered.py` rereads the exact delivered STEP and GLB and stores the resulting evidence in the additive `program_evidence` table. The evidence declares `same_kernel_reread` independence. `RevisionService` runs the verifier after policy acceptance and job commit. The evidence gate can only withhold promotion. Recovery uses stored evidence, and restore requires the target's verified job and bundle evidence, including rows from earlier method versions. The client-checked `evidence(revision_id, *, client)` read follows the evidence revision on restore. STEP supplies the beam size check. The GLB extent check can detect a bad preview, while its mesh bound leaves 0.01 mm fidelity `not_evaluated`.

Frozen in F08 (see [F08 handoff](handoffs/F08.md)). `program/interface.py` defines the operation registry, `mirum_program_api_v0_1` request and response models, and the `mirum_interface_error_v0_1` error envelope. One in-process `ProgramInterface` applies the review, run and evidence checks. The HTTP routes are additive under `/engineering/v1`, and the CLI and stdio MCP server use the HTTP SDK. CLI offline mode shares the server's validate, format and plan functions. The facade enforces per-client quotas, expires staged revisions and binds review and run to the revision's own draft. Repeated runs of a revision admit one job. MCP exposes scoped tools and requires the current head's fixed requirements hash when proposing an edit. `MIRUM_ENGINEERING_PROGRAMS` defaults off. The [K51 retention workflow](K51-RETENTION.md) frees active program slots after durable archival and supports automatic capture on a separately configured volume. Public enablement remains a release decision.

Frozen in F01 (see [F01-DECISIONS.md](F01-DECISIONS.md) D4): preview is a client-side mesh of the saved artifact; there is no server-side streaming preview.

Retain V1-compatible package fields. A new explicit bundle schema additionally includes program.mir, program.ir.json, build-plan.json, check-plan.json, execution-lock.json, compiler diagnostics and revision metadata. Hash payloads in a manifest and store archive/manifest digests outside to avoid circularity. Budget source/IR/history/preview size at admission and keep active downloads safe.

## Suggested implementation boundaries

Use new backend/engineering/program/ modules for IR, registry, compiler, semantics, revisions and adapters, and backend/engineering/language/ for parser/AST/formatter. Frozen in F01 (see [F01-DECISIONS.md](F01-DECISIONS.md) D9): backend/engineering/ at 456e622 has no subpackages and no program or language modules, so these names are accepted as the module boundaries. Existing flat modules (capabilities.py, contracts.py, jobs.py, bundles.py, store.py, api_v1.py, *_adapter.py) are reused, not moved.

Existing beam Literal-based contracts are intentionally narrow. Keep legacy consumers compatible; add a versioned generic envelope and explicit translators rather than globally relaxing validation. Reuse native builders, physics solvers, jobs and publication. New endpoints/tools are thin bindings over a common service layer.

## Resource and threat boundary

V0.1 source budget: 64 KiB, 10,000 tokens, AST depth 16, 2,000 AST nodes, 256 elements per generic list; capability limits can be smaller (beam remains four candidates). Compilation deadline 2 seconds after worker admission; validate in F04 and change only through versioned operational policy with evidence.

No source-driven filesystem/network access, imports, Python escape, arbitrary recursion/loops, backend auto-install or dynamic executable plugins. Kernels remain owned native workers. Worker isolation and trusted-local deployment scope are inherited; a language parser is not a replacement for native process containment.

F08 defines aggregate transport quotas and operator-configurable budgets consistent with capability limits. Resource exhaustion is unavailable, not an engineering rejection or permission to weaken numerical thresholds.
