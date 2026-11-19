# Phase 3 task status

Planning only. All new tasks are not_started; parent B/E status is preserved.

| Complete | ID | Task | State | Prerequisites | Evidence |
|---|---|---|---|---|---|
| [ ] | F01 | [Freeze the framework scope and AgentCAD reuse decisions](tasks/F01-scope-and-reuse.md) | not_started | B14, E05 | Not produced |
| [ ] | F02 | [Define Mirum's typed engineering program and operation registry](tasks/F02-typed-ir.md) | not_started | F01 | Not produced |
| [ ] | F03 | [Implement and qualify the kernel adapter framework](tasks/F03-kernel-adapters.md) | not_started | F02, E01, E02 | Not produced |
| [ ] | F04 | [Implement Mirum Engineering Language v0.1](tasks/F04-language-frontend.md) | not_started | F02 | Not produced |
| [ ] | F05 | [Compile language programs into reviewed executable engineering plans](tasks/F05-compiler-and-review.md) | not_started | F03, F04 | Not produced |
| [ ] | F06 | [Add reversible program revisions and transactional execution](tasks/F06-transactional-revisions.md) | not_started | F05 | Not produced |
| [ ] | F07 | [Bind semantic features and independent checks to delivered geometry](tasks/F07-independent-verification.md) | not_started | F05, F06 | Not produced |
| [ ] | F08 | [Expose one runtime through Python CLI HTTP and agent tools](tasks/F08-interfaces.md) | not_started | F06, F07 | Not produced |
| [ ] | F09 | [Add language editing, AI proposals and visible revision comparison](tasks/F09-editor-and-ai.md) | not_started | F08 | Not produced |
| [ ] | F10 | [Qualify and release the language and multi-kernel framework](tasks/F10-phase-release.md) | not_started | F03, F04, F05, F06, F07, F08, F09 | Not produced |
| [ ] | F11 | [Decide whether a native Mirum geometry kernel is justified](tasks/F11-own-kernel-research.md) | not_started | F10 | Not produced |

F01–F10 form the language/framework release. F11 is optional native-kernel research. Follow [INTEGRATION.md](INTEGRATION.md); do not start blocked work or label an unavailable backend qualified.
