# Phase 3 task status

F01 complete (2026-11-21, docs/contracts only); F02 complete (2026-11-21, records/contracts only); F03 complete (2026-11-22, adapters with native CI evidence); F04 complete (2026-11-21, offline frontend); F05-F11 not_started. Parent B/E status is preserved.

| Complete | ID | Task | State | Prerequisites | Evidence |
|---|---|---|---|---|---|
| [x] | F01 | [Freeze the framework scope and AgentCAD reuse decisions](tasks/F01-scope-and-reuse.md) | complete | B14, E05 | Docs/contracts only, base 456e622 (2026-11-21): [decision record](F01-DECISIONS.md), [inventory](F01-INVENTORY.md), [reuse ledger](REUSE-LEDGER.md), frozen MEL v0.1 lexical rules and material_source_kind translation in [LANGUAGE.md](LANGUAGE.md). P01 only. [F01 handoff](handoffs/F01.md) |
| [x] | F02 | [Define Mirum's typed engineering program and operation registry](tasks/F02-typed-ir.md) | complete | F01 | Records/contracts only, no execution; tested 92f9088 (2026-11-21): backend/engineering/program/ (ProgramIR `mirum_program_ir_v0_1`, registry `mirum_operation_registry_v0_1`, ExecutionLock `mirum_execution_lock_v0_1`, four hashes, beam bridge, committed JSON Schemas), P02 corpus (IR/registry part) and P03 form/AI/language fixtures. [F02 handoff](handoffs/F02.md) |
| [x] | F03 | [Implement and qualify the kernel adapter framework](tasks/F03-kernel-adapters.md) | complete | F02, E01, E02 | Tested d8f430d (2026-11-22): backend/engineering/program/kernel.py (KernelAdapter protocol, records `mirum_kernel_v0_1`, unsupported/unavailable/precision_failure allowlists, BackendLock) and adapters/ (OCCT over the CadQuery beam path, PicoGK over the regen path, labelled mock); registry `mirum_operation_registry_v0_2` (regen capability ops; v0_1 byte-identical). Native conformance in CI: OCCT 9/9 in the native shards (not skipped), PicoGK 19/19 in picogk-runtime. P04, P05. build123d evaluated in writing only. [F03 handoff](handoffs/F03.md) |
| [x] | F04 | [Implement Mirum Engineering Language v0.1](tasks/F04-language-frontend.md) | complete | F02 | Offline frontend, no execution (2026-11-21): backend/engineering/language/ (lexer MEL-L001-L023, recursive-descent parser MEL-P001-P011 with FRAMEWORK.md budgets, syntax tree, canonical formatter, provisional beam lowering to ProgramIR equal to the F02 P03 semantics). P02 grammar part, P06 parser part. [F04 handoff](handoffs/F04.md) |
| [ ] | F05 | [Compile language programs into reviewed executable engineering plans](tasks/F05-compiler-and-review.md) | not_started | F03, F04 | Not produced |
| [ ] | F06 | [Add reversible program revisions and transactional execution](tasks/F06-transactional-revisions.md) | not_started | F05 | Not produced |
| [ ] | F07 | [Bind semantic features and independent checks to delivered geometry](tasks/F07-independent-verification.md) | not_started | F05, F06 | Not produced |
| [ ] | F08 | [Expose one runtime through Python CLI HTTP and agent tools](tasks/F08-interfaces.md) | not_started | F06, F07 | Not produced |
| [ ] | F09 | [Add language editing, AI proposals and visible revision comparison](tasks/F09-editor-and-ai.md) | not_started | F08 | Not produced |
| [ ] | F10 | [Qualify and release the language and multi-kernel framework](tasks/F10-phase-release.md) | not_started | F03, F04, F05, F06, F07, F08, F09 | Not produced |
| [ ] | F11 | [Decide whether a native Mirum geometry kernel is justified](tasks/F11-own-kernel-research.md) | not_started | F10 | Not produced |

F01–F10 form the language/framework release. F11 is optional native-kernel research. Follow [INTEGRATION.md](INTEGRATION.md); do not start blocked work or label an unavailable backend qualified.
