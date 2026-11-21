# F01 decision record: Phase 3 scope and reuse

Status: Accepted 2026-11-21 by owner decision; reviewed in the F01 PR.
Base: 456e622 (main). Date: 2026-11-21.
Task: [F01](tasks/F01-scope-and-reuse.md). Companion inventory: [F01-INVENTORY.md](F01-INVENTORY.md). Reuse ledger: [REUSE-LEDGER.md](REUSE-LEDGER.md).

## 1. Context

Phase 3 builds Mirum's own engineering execution framework and the Mirum Engineering Language (MEL): a declarative .mir language compiled into typed programs executed through registered geometry backends (see [README.md](README.md)). Prerequisites B14 and E05 are complete per parent [../STATUS.md](../STATUS.md) (B14: RC1 frozen at a7f1953; E05 complete, with E01, E02 and E03 also complete; E04 remains blocked).

The own-backend study (PR #122, docs/plans/2026-11-21-own-backend.md on branch docs/own-backend; not yet on main) compared Mirum with a hosted-kernel CAD agent stack and named five gaps:

- G1: geometry construction layer (MEL v0.1 has declarative capability fields only).
- G2: span-tagged operation stream plus artifact graph.
- G3: kernel service protocol, mock mode, preview.
- G4: verification-first AI edit loop.
- G5: fate of the legacy free-form /generate path.

On 2026-11-21 the owner approved the study's recommendations ("go with the one that you recommend"). D1-D6 record the study's Q1-Q6; D7-D9 freeze the structural consequences.

## 2. Decisions

### D1 (study Q1): MEL v0.1 stays the declarative vertical slice; construction is v0.2

Decision: MEL v0.1 remains the declarative capability-field vertical slice as planned in [LANGUAGE.md](LANGUAGE.md). A typed geometry-construction block (gap G1) is MEL v0.2, inside Phase 3, specified after the v0.1 slice. F02 must reserve the record shapes without implementing them: RequirementPlan separate from ConstructionGraph, and OperationDescriptor entries for sketch, extrude, revolve, sweep, loft, fillet/chamfer, shell, pattern and boolean.

Rationale: keeps the first slice small and testable against the existing beam; reserving shapes now avoids a later IR break.

Consequence: F02 reserves and tests record shapes only (no execution). F04 implements v0.1 only. The v0.2 specification is a later Phase 3 deliverable that must precede any G1 execution.

### D2 (study Q2): kernel binding is CadQuery/OCP for v0.x

Decision: the native B-rep binding for v0.x is CadQuery/OCP (the existing path). build123d is evaluated, not adopted, in F03 conformance.

Rationale: CadQuery is already integrated, sandboxed and qualified in CI; switching adds risk without a gap it closes.

Consequence: F03 builds the OCCT adapter over CadQuery and records a build123d conformance finding; any switch needs a new decision.

### D3 (study Q3): AI edit loop is offline and fixture-driven first

Decision: F08 tools and the F09 loop are built and tested offline against fixtures first. Any live qualification needs its own owner authorization, call budget and tag namespace. The agent may edit construction only, never requirements.

Rationale: live spend and provider behavior are separately gated; requirements must stay as reviewed.

Consequence: F08 exposes the read/propose/compile/execute tools with requirement plan read-only; F09 implements the budgeted loop on fixtures. No live call in Phase 3 without a separate authorization.

### D4 (study Q4): preview is a client-side mesh of the saved artifact

Decision: preview is rendered client-side from a mesh of the saved artifact. No server-side streaming preview.

Rationale: preview then shows the delivered artifact, not a generation-time stream; no new server transport or budget.

Consequence: F08/F09 serve the saved mesh through the existing artifact download path; P13 checks the actual artifact.

### D5 (study Q5, gap G5): legacy /generate stays, labelled geometry-only

Decision: the legacy free-form /generate path stays and is labelled separately as geometry-only. Re-decide at F10. No change to backend/compiler.py, backend/script_policy.py or allowlists.

Rationale: removing or re-expressing it is a larger change than Phase 3 needs; its claim scope is already narrow.

Consequence: F09 UI keeps it visually separate from reviewed checked-design flows; F10 records the re-decision.

### D6 (study Q6): reuse is ideas only from Zoo (MIT) and AgentCAD

Decision: from both Zoo (MIT) and AgentCAD, adopt ideas only. Any future code copy must pass through [REUSE-LEDGER.md](REUSE-LEDGER.md) first.

Rationale: AgentCAD has no standalone root licence file in the reviewed tree ([AGENTCAD-REUSE.md](AGENTCAD-REUSE.md)); Zoo sources are not SHA-pinned.

Consequence: F01 (owner of P01) keeps the ledger; F02-F09 independently implement. No upstream logs, transcripts, benchmark data or generated assets enter the repository.

### D7: span-tagged op stream and ArtifactGraph are F02 records and F03 adapter outputs

Decision: the span-tagged operation stream and the ArtifactGraph (gap G2: operation to faces/edges/solids with source span) are F02 record definitions and F03 adapter outputs.

Rationale: semantic identity ([FRAMEWORK.md](FRAMEWORK.md)) needs a record that rebinding and diagnostics can query.

Consequence: F02 defines the records; F03 adapters emit them; F07 consumes them for semantic bindings and independent checks.

### D8: support matrix for F10

Decision: two qualified backends. Beam (rectangular_cantilever) on OCCT (CadQuery) and regen_chamber on PicoGK. glider_cem is a domain capability, not a kernel. rectangular_plate and dragster come later. See section 6.

Rationale: matches README "two qualified geometry backends, not an untested universal compatibility claim".

Consequence: F03 qualifies exactly these two backends; F10 states the release claims in section 6 and no more.

### D9: module boundaries

Decision: new modules live under backend/engineering/program/ (IR, registry, compiler, revisions, adapters) and backend/engineering/language/ (lexer, parser, AST, formatter). Verified against current code: backend/engineering/ at 456e622 has no subpackages and no program or language modules (flat modules only, via ls). Existing flat modules (capabilities.py, contracts.py, jobs.py, bundles.py, store.py, api_v1.py and the *_adapter.py files) are reused, not moved.

Rationale: new code is isolated from the qualified beam path; no import churn.

Consequence: F02 creates program/; F04 creates language/; F03 adapters under program/ wrap the existing *_adapter.py and native builders.

## 3. Existing interfaces

Full inventory: [F01-INVENTORY.md](F01-INVENTORY.md). Summary at 456e622:

| Capability id | Version | Claim | Source |
|---|---|---|---|
| rectangular_cantilever | 1.0.0 | checked_design (assigned in the API record, not a descriptor field) | backend/engineering/capabilities.py:95-96 (id, version); backend/engineering/api_v1.py:380 (claim) |
| co2_dragster | 2.0.0 | geometry_checks_only | backend/engineering/capabilities.py:467 (id, version), :503 (claim) |
| regen_chamber | 1.0.0 | geometry_checks_only | backend/engineering/capabilities.py:511, :544 |
| glider_cem | 1.0.0 | screening_only | backend/engineering/capabilities.py:556, :586 |
| rectangular_plate | 1.0.0 | checked_design | backend/engineering/capabilities.py:602, :623 |

The claim field is declared at backend/engineering/capabilities.py:214 as Literal['geometry_checks_only', 'screening_only', 'checked_design'].

Interface rules frozen here:

- The /engineering/v1 router (prefix declared at backend/engineering/api_v1.py:188; routes GET /capabilities, POST /drafts, GET and PATCH /drafts/{draft_id}, POST /briefs, GET /briefs/{brief_id}, POST /jobs, GET /jobs/{job_id}, POST /jobs/{job_id}/cancel, GET /jobs/{job_id}/artifacts/{format}) is kept unchanged. New transports are versioned separately and defined in F08.
- Legacy Literal-based beam contracts are kept. A generic versioned envelope plus explicit translators carry other shapes, per [FRAMEWORK.md](FRAMEWORK.md); validation is not globally relaxed.
- Existing capability, numerical and policy versions are unchanged by F01.

## 4. New modules and versions

Modules: see D9. Version identifiers:

| Item | Identifier | Owner |
|---|---|---|
| MEL source version | "0.1" (as in [examples/beam.mir](examples/beam.mir): language mirum "0.1") | F04 |
| MEL construction block | "0.2" reserved, not defined | later Phase 3 spec (D1) |
| ProgramIR schema, OperationDescriptor registry, ExecutionLock schema | assigned by F02, recorded in its handoff | F02 |
| Bundle schema | assigned by F06, recorded in its handoff | F06 |
| Existing capability and policy versions | unchanged | n/a |

## 5. Frozen translation

The beam fixture field material_source_kind is mapped to the B03 schema by an explicit translation frozen in [LANGUAGE.md](LANGUAGE.md): "illustrative", "user_supplied" and "documented" map one-to-one to `material.source_kind` (backend/engineering/contracts.py:102); any other value, a missing value or a non-string is a compilation error; no default. MEL v0.1 lexical rules and error codes MEL-L001 to MEL-L023 are frozen in the same file.

## 6. Support matrix (D8)

| Capability | Backend | Representation | Phase 3 release claim | Qualifying task |
|---|---|---|---|---|
| rectangular_cantilever | OCCT (CadQuery) | B-rep | checked_design via the existing reviewed path; bounded verdict (P07: 5 and 10 mm rejected, 15 mm accepted at the reference case) | F03 (adapter), F05/F07 (language path), F10 |
| regen_chamber | PicoGK | voxel-derived geometry, STL | geometry-only (geometry_checks_only) | F03, F10 |
| glider_cem | none; domain capability above a geometry adapter | n/a | screening_only; not a kernel | F10 (label check only) |
| rectangular_plate | not in the Phase 3 release matrix | n/a | later | none in Phase 3 |
| co2_dragster | not in the Phase 3 release matrix | n/a | later | none in Phase 3 |

Claim strings match backend/engineering/capabilities.py as cited in section 3. Physical and manufacturing claims remain with E03/E04.

## 7. Exclusions

- No new solid-modeling kernel; F11 is a later go/no-go research gate only.
- MEL v0.1 is not a universal CAD feature editor.
- No G1 execution before the v0.2 specification exists.
- No server-side streaming preview.
- No live AI calls without separate owner authorization, budget and tag namespace.
- No code, logs, transcripts or benchmark data copied from Zoo or AgentCAD.
- No change to backend/compiler.py, backend/script_policy.py or allowlists.
- No physical or manufacturing claims; E03/E04 own them, and E04 is blocked.
- No relaxation of legacy Literal contracts.
- No change to parent B/E status rows.

## 8. Acceptance owners

[ACCEPTANCE.md](ACCEPTANCE.md) gates (from each task card's "Applicable release checks") and the deliverable each F task owns ([INTEGRATION.md](INTEGRATION.md): F01 freezes interface decisions; F02 owns generic IR/registry; F03 backend adapters; F04 lexical syntax/AST; F05 semantic compilation/review; F06 revisions; F07 delivered-artifact evidence; F08 transports; F09 UI/AI; F10 release qualification; F11 own-kernel feasibility).

| Task | Owned deliverable | P gates evidenced |
|---|---|---|
| [F01](tasks/F01-scope-and-reuse.md) | Interface decisions, ADR, inventory, reuse ledger | P01 |
| [F02](tasks/F02-typed-ir.md) | Generic IR and operation registry | P02, P03 |
| [F03](tasks/F03-kernel-adapters.md) | Backend adapters | P04, P05 |
| [F04](tasks/F04-language-frontend.md) | Lexical syntax and AST | P02, P06 |
| [F05](tasks/F05-compiler-and-review.md) | Semantic compilation and review | P03, P06, P07 |
| [F06](tasks/F06-transactional-revisions.md) | Revisions and transactions | P08, P09 |
| [F07](tasks/F07-independent-verification.md) | Delivered-artifact evidence | P05, P07, P10 |
| [F08](tasks/F08-interfaces.md) | Transports | P11, P12 |
| [F09](tasks/F09-editor-and-ai.md) | UI and AI | P03, P08, P11, P13 |
| [F10](tasks/F10-phase-release.md) | Release qualification | P01-P14 |
| [F11](tasks/F11-own-kernel-research.md) | Own-kernel go/no-go | P04, P05 |

P14 is evidenced by F10 only (no other card lists it).

## 9. Open risks

- AgentCAD tree lacks a root licence file; code copy is blocked until resolved.
- Zoo sources are not SHA-pinned; ideas only.
- build123d conformance is unknown until F03.
- E01 PicoGK carries the shared 420 s v1 deadline limitation.
- E04 is blocked, so no physical or manufacturing claims.
- v0.2 scope growth may conflict with "not a universal CAD feature editor".
- Own-backend study PR #122 is not yet merged; this record cites it by name.
- Six divergences in existing code (V1-V6 in [F01-INVENTORY.md](F01-INVENTORY.md#divergences-and-how-phase-3-resolves-them)) are not fixed by F01; Phase 3 rules there keep new code off them. V1 (router assurance `checked_design` for every match) and V5 (`conform` does not check origin completeness, prompt SHA-256 or version) are reported to the lead.
- material_source_kind translation is frozen in [LANGUAGE.md](LANGUAGE.md) (identity mapping to `material.source_kind`, backend/engineering/contracts.py:102 at 456e622); if that enum changes, MEL needs a new versioned translation.
