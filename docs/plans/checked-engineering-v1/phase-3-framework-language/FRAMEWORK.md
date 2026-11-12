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

## Required records

ProgramIR contains schema/version, named design, capability version, fixed physical requirements, candidate space, material/source references, assumptions, requested exports, semantic feature IDs and source-span mapping. Keep build operations and check requirements separate: operation success does not establish requirement satisfaction.

OperationDescriptor contains operation ID/version, typed arguments, dimensional units, preconditions, output types, supported representations and backend requirements. V0.1 uses a small registry of supported capability build/inspect/analyze/export actions. It is not a universal CAD feature editor.

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

Initial backends: OCCT via existing CadQuery for native B-rep; PicoGK for its existing voxel-derived geometry/STL path. A geometry adapter is distinct from a CapabilityAdapter such as beam, regen geometry or glider CEM; retain the domain adapter as planner/analysis coordinator above it.

## Representation and conversion rules

B-rep, meshes and volumetric representations carry different fidelity and editability. An adapter must declare which operations and measurements it supports. Do not manufacture STEP by merely wrapping a tessellation and claim parametric editability.

For a conversion, preserve source/target hashes, units/frame, operation parameters, numerical tolerances, topology changes and measured deviation where supported. Reject a requested evidence claim that cannot survive conversion. Retain backend-native artifacts and the accepted representation. Independent reread may use the same kernel library, so it detects generation/delivery inconsistencies rather than proving independent mathematical correctness.

Conformance tests: analytical simple shapes for supported overlap, bounds/volume/units/orientation, boolean behavior where implemented, degenerate geometry, serialization/reread, supported measurement error, limits, cancellation and missing runtime. Compare geometry within declared tolerances, not raw serialization bytes. Never require a mesh to preserve a B-rep feature tree.

## Semantic identity

Use logical names such as beam.root_section and beam.free_end, each bound to measured geometry under a capability-specific predicate. After rebuilding, rebind and validate uniqueness/shape/orientation; an old face index is insufficient. Ambiguous or missing identities block a required load/measurement. Imported geometry lacking bindings is limited to checks actually possible. Drawing tags are inspiration, not a solution to arbitrary 3D topology changes.

## Transaction and evidence policy

Reuse B09/B10 jobs, admission, persistent storage and publication. Stage complete revisions privately; failed batches do not advance the current accepted pointer. Preserve previous accepted revisions and their evidence. A compare view must pair each report with its own geometry.

Rollback selects an existing immutable historical revision and records a new history event/pointer with its original requirements/lock. It does not assert that it satisfies the current brief. Expired or altered artifacts cannot be relabelled as restored; a rebuild is a new checked run. Crash markers and journal transactions reconcile file/database state; cancellation cannot expose partial accepted work.

Retain V1-compatible package fields. A new explicit bundle schema additionally includes program.mir, program.ir.json, build-plan.json, check-plan.json, execution-lock.json, compiler diagnostics and revision metadata. Hash payloads in a manifest and store archive/manifest digests outside to avoid circularity. Budget source/IR/history/preview size at admission and keep active downloads safe.

## Suggested implementation boundaries

Use new backend/engineering/program/ modules for IR, registry, compiler, semantics, revisions and adapters, and backend/engineering/language/ for parser/AST/formatter. Names are provisional until F01 maps actual integrated code.

Existing beam Literal-based contracts are intentionally narrow. Keep legacy consumers compatible; add a versioned generic envelope and explicit translators rather than globally relaxing validation. Reuse native builders, physics solvers, jobs and publication. New endpoints/tools are thin bindings over a common service layer.

## Resource and threat boundary

V0.1 source budget: 64 KiB, 10,000 tokens, AST depth 16, 2,000 AST nodes, 256 elements per generic list; capability limits can be smaller (beam remains four candidates). Compilation deadline 2 seconds after worker admission; validate in F04 and change only through versioned operational policy with evidence.

No source-driven filesystem/network access, imports, Python escape, arbitrary recursion/loops, backend auto-install or dynamic executable plugins. Kernels remain owned native workers. Worker isolation and trusted-local deployment scope are inherited; a language parser is not a replacement for native process containment.

F08 defines aggregate transport quotas and operator-configurable budgets consistent with capability limits. Resource exhaustion is unavailable, not an engineering rejection or permission to weaken numerical thresholds.
