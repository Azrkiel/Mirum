# Phase 3 reuse ledger

Record every external idea or code reuse here before it lands in Mirum. Use `reimplemented-not-copied` for ideas implemented in Mirum's own code and `copy` for code approved through the copy gate.
Owner decision D6 (2026-11-21) limits current reuse from AgentCAD and Zoo to ideas.
Status 2026-11-21: no upstream code, examples, transcripts, logs, evidence ZIPs, viewers or benchmark data copied into Mirum.
See [AGENTCAD-REUSE.md](AGENTCAD-REUSE.md), P01 in [ACCEPTANCE.md](ACCEPTANCE.md), and the own-backend study, PR #122 (docs/plans/2026-11-21-own-backend.md, merged to main at b85b61e).

## Copy gate

Before merging any upstream code copy:

1. Pin the upstream repository, full commit SHA and exact file path for every file to be copied.
2. Locate the full licence text and copyright notice in the pinned tree. Record their paths and the applicable terms in the ledger. README and pyproject declarations alone are insufficient. AgentCAD code copy stays blocked until the licence evidence is resolved because the reviewed tree has no root LICENSE file.
3. Review and record the licence terms for dependencies and bundled assets.
4. Retain upstream attribution in each copied file's header and record it in the ledger.
5. List all modifications and name the targeted Mirum tests that cover the copied code.
6. Obtain explicit owner approval and quote it verbatim in the ledger.
7. Add a new row with decision `copy` before merge. Record modifications and the approval quote beneath the table, keyed by row ID.

## Row template

```text
| ID | Upstream | Source (pinned URL or path + read date) | Idea | Mirum task | Decision | Licence evidence | Attribution | Tests |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |
```

## Idea rows

| ID | Upstream | Source (pinned URL or path + read date) | Idea | Mirum task | Decision | Licence evidence | Attribution | Tests |
|---|---|---|---|---|---|---|---|---|
| R01 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/ops/registry.py> | Typed operation metadata and schemas for a physics-aware registry | F02 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F02 registry schema and unknown-operation rejection |
| R02 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/spec.py> | Compile a spec into operations and an immutable requirement plan | F05 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F05 fixed requirements across beam candidates |
| R03 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/engine.py> | Prevalidate batches and discard failed edits using staged revisions in Mirum jobs | F06 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F06 (reimplemented, not copied; no AgentCAD code): `test_rejected_batch_leaves_the_head_and_its_download`, `test_cancelled_batch_leaves_the_head_and_its_download`, `test_publication_failure_batch_leaves_the_head_and_its_download`, `test_stale_parent_at_staging_writes_nothing`, native `test_rejected_batch_leaves_rev1_and_its_download_unchanged` |
| R04 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/store.py> | Checkpoints, pending commits, journals and recovery under Mirum's storage and expiry policy | F06 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F06 (reimplemented, not copied; no AgentCAD code): `test_journal_is_append_only`, `test_restart_*` and `test_real_crash_at_each_seam_recovers_on_restart` in `test_program_revisions_crash.py`, `test_expired_publication_refuses_restore_then_rebuild_runs_a_new_job`, `test_compacted_payload_restore_rebuild_compare` |
| R05 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/tree/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/validate> | Reread delivered STEP/STL and require measurement coverage | F07 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F07 native reread and missing coverage |
| R06 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/tests/test_e_tamper.py> | Alter delivered geometry and evidence independently of the generator | F07/F10 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F07/F10 tamper adversaries |
| R07 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/errors.py> | Structured errors with retryability, partial-change state and source or requirement spans | F08 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F08 diagnostic and recovery parity across transports |
| R08 | AgentCAD | <https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/README.md> | One runtime for Python/CLI/HTTP/MCP and revision comparison tied to saved evidence | F08/F09 | reimplemented-not-copied | MIT declared in README and pyproject at e9f2e90; no root LICENSE file; copy blocked (see Copy gate) | Credited in this ledger; no code | F08 transport parity and F09 revision comparison |
| R09 | Zoo (modeling-app) | <https://github.com/KittyCAD/modeling-app> `README.md` (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Source as the single truth for UI edits and feature-tree selection through the artifact graph | F09 (G2 UI) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F09 source edits and feature-tree selection |
| R10 | Zoo (kcl-lib) | <https://github.com/KittyCAD/modeling-app> `docs/kcl-lang/numeric.md` (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Unit-carrying numbers with units kept through arithmetic | F02/F04/F05 (G1) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F04/F05 dimensional arithmetic and unit mismatches |
| R11 | Zoo (kcl-lib) | <https://github.com/KittyCAD/modeling-app> `rust/kcl-lib/src/engine/mod.rs` (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Ordered kernel operation stream with a source span on each operation | F02/F03/F07 (G2) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F03 operation order and source spans |
| R12 | Zoo (kcl-lib) | <https://github.com/KittyCAD/modeling-app> `rust/kcl-lib/src/execution/artifact.rs` (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Artifact graph with code references linking operations to faces, edges and solids | F02/F03/F07 (G2), F09 (UI) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F03 artifact graph emission and F07 binding checks |
| R13 | Zoo (modeling-app and KCL docs) | <https://github.com/KittyCAD/modeling-app> `docs/kcl-lang/edge-references.md` and <https://zoo.dev/docs/kcl-book/tags.html> (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Tag faces and name edges by adjacency. Ambiguity blocks a required load or measurement. | F02/F03/F07 (G2) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed. KCL tags docs page, no code; idea only | Credited in this ledger; no code | F03 adjacency after edits and F07 ambiguous binding rejection |
| R14 | Zoo (kcl-lib) | <https://github.com/KittyCAD/modeling-app> `rust/kcl-lib/CONTRIBUTING.md` (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Per-program simulation snapshots of operation logs and measurements, run offline within tolerance | F03/F08 (G3) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F03 offline snapshots and tolerance comparisons |
| R15 | Zoo (kcl-lib) | <https://github.com/KittyCAD/modeling-app> `rust/kcl-lib/src/execution/mod.rs` (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Mock execution mode that type-checks and plans without constructing geometry | F03/F08 (G3) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F03 offline planning and F08 mock interface |
| R16 | Zoo (KCL docs) | <https://zoo.dev/docs/kcl-lang/known-issues> (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Per-project language pin (`kclVersion`), extended by Mirum's ExecutionLock to registry, kernel and policies | F02/F04/F05 | reimplemented-not-copied | docs page, no code; idea only | Credited in this ledger; no code | F04 language version mismatch and F05 locked beam execution |
| R17 | Zoo (Zookeeper docs) | <https://zoo.dev/research/zookeeper> and <https://zoo.dev/docs/zoo-design-studio/zookeeper> (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Agent proposes edits, runs the program and inspects results. Requirements stay frozen, vision is advisory and Mirum checks gate acceptance. | F08/F09 (G4) | reimplemented-not-copied | docs page, no code; idea only | Credited in this ledger; no code | F08 inspection tools and F09 offline edit fixtures for frozen requirements and loop budgets |
| R18 | Zoo (kittycad-modeling-cmds) | <https://docs.rs/kittycad-modeling-cmds> (read 2026-11-21). SHA not pinned (ideas only; pin before any copy) | Typed modeling command set with first-class sweeps and lofts and checks for operation substitution | F02/F04/F05 (G1), F03 (conformance) | reimplemented-not-copied | MIT per study section 2 (public repo page); engine/agent closed | Credited in this ledger; no code | F02 registry typing, F03 sweep/loft conformance and F05 construction compilation |

## Not reused

- DXF handles and drawing tags as a solution for arbitrary 3D face naming, or drawing validity as evidence of beam, thermal or flow performance.
- Warning-only handling of unknown mandatory requirement fields or upstream sandbox claims in place of Mirum's native-process controls.
- AgentCAD drawing-specific code and optional AutoCAD/FreeCAD integrations. Drawing support remains a possible future 2D adapter.
- Closed Zoo engine, agent models and prompts, and `create_proprietary_to_kcl`.
- Upstream examples, agent transcripts, logs, evidence ZIPs, generated viewers and benchmark datasets.
- Published benchmark outcomes as Mirum evidence.
- Server-side video preview. Owner decision D4 selects client-side preview from saved artifact meshes.
- Leo as a backend model. The study found no public code or method.

## Runtime dependencies (not code reuse)

| Component | Licence as stated in the study (2026-11-21) | Role | Status |
|---|---|---|---|
| OCCT | LGPL-2.1 + Open CASCADE exception | B-rep kernel | in use |
| CadQuery | Apache-2.0 | Current Python binding in the sandbox | in use |
| build123d | Apache-2.0 | Candidate typed binding | candidate, evaluated in F03; not added |
| Manifold | Apache-2.0 | Fast mesh booleans for previews and meshes | optional, not added |
| PicoGK | Not stated in the study; to be recorded from the pinned E01 dependency | Voxel/implicit backend for geometry-only claims | in use (E01) |
| truck | Apache-2.0 | Rust kernel evaluated for possible future use | not used |
| Fornjot | 0BSD | Archived Rust CAD kernel | not used |

Re-check licences from the pinned dependency before any version bump or addition.
