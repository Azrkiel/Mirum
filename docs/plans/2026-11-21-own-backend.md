# Own backend design study: rebuilding a Zoo/Leo-class CAD backend, verification first

Status: design study, docs only. No code, no Zoo account, key or API call, no benchmarking against Zoo.
Date: 2026-11-21. Owner direction (2026-11-21): "tear Zoo's architecture apart and rebuild it ourselves and better";
Zoo is research and experimental validation only.
Inputs: public Zoo material (MIT source on GitHub, zoo.dev docs and research pages), public Leo material, open-kernel
projects and 2024-2026 LLM-CAD papers, read 2026-11-21 with WebFetch/WebSearch. WebFetch returns summaries, so wording
is paraphrased; items marked UNVERIFIED were not confirmed from a primary page. Earlier intake:
[E05-zoo-backend.md](checked-engineering-v1/proposals/E05-zoo-backend.md) (access, cost, terms, API shape).
This study plugs into the existing Phase 3 plan
([phase-3-framework-language](checked-engineering-v1/phase-3-framework-language/README.md)); it does not replace it.

## 1. Summary

- Zoo's stack is four layers: a client app where source code is the single truth, a language (KCL) interpreted in
  Rust, a stream of typed modeling commands sent over a WebSocket, and a closed, hosted GPU B-rep engine that returns
  results and a video stream of the viewport. An LLM agent (Zookeeper) writes and edits KCL, runs it and inspects
  snapshots and mass properties.
- Leo is a different product: retrieval over parts catalogs and PLM data, calculations in a code interpreter, and
  assembly of existing parts. It is not a verified part-synthesis backend. Little of it transfers.
- Mirum already has what Zoo does not publish at all: typed requirement contracts, reviewed briefs, registered
  physics checks, independent re-reading of delivered geometry, revision-bound evidence packages, and AI values that
  must be grounded and labelled. Mirum lacks four things Zoo has: a geometry-construction language, a source-span to
  geometry map (artifact graph), a kernel service protocol with fast mock execution, and an iterative agent edit loop.
- Recommendation: build those four on the open OCCT stack (CadQuery/OCP now, build123d evaluated in F03), inside the
  Phase 3 order (F02 registry, F03 adapters, F04/F05 language, F07 evidence, F08 interfaces, F09 AI). "Better than
  Zoo" means: requirements are first-class and immutable during an edit loop, every build is deterministic and
  testable offline, units are tracked through arithmetic, geometry identity is semantic, and acceptance comes only from
  Mirum checks. No own kernel (F11 stays a later go/no-go).

## 2. Zoo architecture, layer by layer (public material)

| Layer | What is public | Source |
|---|---|---|
| Client | modeling-app (MIT): React, XState, CodeMirror; Electron and web build files. "All GUI (or point-and-click) interactions should be actions performed on this code representation." | https://github.com/KittyCAD/modeling-app |
| Language | KCL, Rust crate `rust/kcl-lib` (MIT). Unit-carrying numbers (`mm`, `in`, `deg`...; `@settings(defaultLengthUnit)`), modules with `import`/`export`, pipelines `\|>`, functions, sketch blocks with constraints, per-project `kclVersion` pin. WASM build and an LSP exist; interpreter-in-browser is implied, UNVERIFIED as a stated fact. | docs/kcl-lang/numeric.md, modules.md in modeling-app; rust/kcl-lib/Cargo.toml |
| Command stream | Execution produces an ordered batch of `(WebSocketRequest, SourceRange)`; each kernel command carries the source span that made it. `kittycad-modeling-cmds` (MIT) defines `ModelingCmd` (Extrude, Revolve, BooleanUnion, ...). Crate does not follow semver. | rust/kcl-lib/src/engine/mod.rs; https://docs.rs/kittycad-modeling-cmds |
| Engine | Closed and hosted. GPU (Vulkan) B-rep engine; viewport "is just a video stream from our hosted geometry engine". GPU surface-surface intersection claimed. Export formats from the engine: UNVERIFIED in this pass. | modeling-app README; https://zoo.dev/research/zoo-cad-engine-overview |
| Artifact graph | `ExecOutcome` returns an `artifact_graph` and `operations` (feature tree). 23 artifact kinds (Plane, Path, Segment, Sweep, Wall, Cap, SweepEdge, EdgeCut, Pattern, ...), each with a `CodeRef` (range, node path). Built from engine responses keyed to source spans: this is what makes code and point-and-click edits interchangeable. | rust/kcl-lib/src/execution/artifact.rs |
| Geometry identity | Tags name faces; edges are named by adjacent faces (index only as a tiebreak), "more stable when earlier modeling operations change the body's topology". | docs/kcl-lang/edge-references.md; https://zoo.dev/docs/kcl-book/tags.html |
| Testing | "Simulation tests": a directory per case with `input.kcl`, snapshots of variables, renders and the engine command list. Regenerating snapshots needs `$ZOO_API_TOKEN`, so they appear to depend on the live engine. A mock connection (`ContextType::Mock`, `MockConfig.sketch_block_id`) re-runs one sketch block without geometry. | rust/kcl-lib/CONTRIBUTING.md; execution/mod.rs |
| Agent | Text-to-CAD (2023) became Zookeeper (Design Studio v1.1, Jan 2026). It writes, runs and debugs KCL, reviews multi-view snapshots, computes mass, volume, area and centre of mass, reads docs, keeps per-project `AGENTS.md` and its own notes. Output is KCL only. Client surface: `copilot_ws(conversation_id, replay)`, `reasoning_ws`, KCL completions, feedback. Models and training are not public. | https://zoo.dev/research/zookeeper; https://zoo.dev/docs/zoo-design-studio/zookeeper; https://docs.rs/kittycad/latest/kittycad/ml/struct.Ml.html |
| Known limits | Imported geometry is not editable; coplanar booleans unsupported; chamfer and region-tag fixes gated behind newer `kclVersion`; live runs need the hosted engine and a token; arithmetic does not carry units (`10in * 25.4` is `254in`). Determinism and offline use are not documented. | https://zoo.dev/docs/kcl-lang/known-issues; numeric.md |

Licensing: modeling-app, kcl-lib, kittycad-modeling-cmds and the generated clients are MIT; the engine, the agent's
models and prompts, and `create_proprietary_to_kcl` are closed. Owner rule: learn from ideas, do not copy Zoo code;
any code copy would need the reuse ledger in [AGENTCAD-REUSE.md](checked-engineering-v1/phase-3-framework-language/AGENTCAD-REUSE.md)
style (path, SHA, licence, attribution). Zoo's terms (E05-zoo-backend.md section 2.3) bind account holders; we hold
no account and run nothing against Zoo.

## 3. Leo (public material)

"Large Mechanical Model" that uses machine parts as tokens; text, sketch, image and CAD input; search over 120M+
vendor parts and PLM data; calculations in a code interpreter; assemblies said to open with a feature tree in
SolidWorks, Onshape, CATIA and Inventor, with mates closed manually (https://getleo.ai/llms.txt;
https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/). No public API, architecture or
benchmark method; the 96% accuracy claim is unexplained. Transferable: cite sources and formulas in every
calculation answer (Mirum already labels origins); parts retrieval could become a later capability. Not a template
for the backend.

## 4. Mirum today, mapped onto the same layers

| Layer | Zoo | Mirum today | Gap |
|---|---|---|---|
| Requirements | none published | typed `GeneratorDescriptor` per capability (beam, plate, dragster, glider_cem, regen_chamber), reviewed briefs, policy versions, claims (`checked_design`, `screening_only`, `geometry_checks_only`) | Mirum ahead |
| Language | KCL, general geometry | MEL v0.1 planned (F04): declarative capability fields only, no geometry operations | **G1: construction layer** |
| Command stream / IR | ModelingCmd batch with source spans | per-capability adapters call CadQuery/PicoGK directly; F02 plans ProgramIR + OperationDescriptor | **G2: span-tagged op stream + artifact graph** |
| Kernel | closed hosted GPU B-rep, needs network | owned bounded native workers (CadQuery/OCCT, PicoGK), Linux sandbox; legacy `/generate` runs LLM CadQuery under `compiler.py` | **G3: kernel service protocol, mock mode, preview** |
| Identity | tags + adjacency-named edges | FRAMEWORK.md semantic identity (logical names bound by predicates) planned, not built | part of G2 |
| Verification | agent self-checks (snapshots, mass properties) | independent re-read of delivered STEP/STL, registered physics checks, acceptance policy, evidence packages | Mirum ahead |
| AI | iterative agent over KCL | one-shot structured extraction + grounding per capability; no edit loop | **G4: verification-first edit loop** |
| Tests | snapshots that depend on the hosted engine | CI native shards, offline fixtures, release suites | Mirum ahead; extend to G1-G3 |
| Versioning | per-project `kclVersion` | ExecutionLock planned (FRAMEWORK.md), capability/policy versions in briefs | aligned |

## 5. Target architecture ("better", concretely)

```
.mir source (requirements block + construction block)
  -> parse (spans) -> typed ProgramIR: RequirementPlan (immutable once reviewed) + ConstructionGraph
  -> ExecutionLock (language, registry, kernel, policies)
  -> review (requirements + chosen backend + exact plan)
  -> kernel worker: ordered OpBatch[(op, span)] -> OCCT  -> artifacts + ArtifactGraph(span <-> faces/edges)
  -> independent inspect (re-open delivered STEP/STL) + registered checks -> CheckEvidence
  -> acceptance policy -> revision + package
AI agent: proposes source edits to the construction block only; each iteration runs the same pipeline
```

Design rules that make it better than the public Zoo design:

1. **Requirements are separate from construction and frozen during an edit loop (G4).** The agent may change how the
   part is built, never what it must satisfy. A requirement edit is a new review. This closes the "make the check pass
   by changing the check" failure that self-checking agents have.
2. **Deterministic and offline (G3).** Same source + same lock gives geometry equal within declared tolerance; CI runs
   every program snapshot on the open kernel with no network. Zoo's snapshot regeneration needs its hosted engine.
3. **Units through arithmetic (G1).** Dimensional analysis on every expression (length * length is area; length *
   number keeps the unit); Zoo's numbers drop units after arithmetic.
4. **Semantic identity, fail loud (G2).** Faces/edges named by tag or by a query on the artifact graph (made by op X,
   normal +Z, adjacent to tagged faces); ambiguity blocks a required load or measurement instead of guessing.
5. **Kernel metrics gate, vision is advisory (G4).** Research supports this: CADSmith's outer loop on OCCT bbox,
   volume and validity checks lifted median IoU 0.81 to 0.96 on its own 100-prompt set
   (https://arxiv.org/abs/2603.26512), while VLM-only self-verification gave single-digit gains
   (CADCodeVerify, https://arxiv.org/abs/2410.05340). Property tests over typed requirements beat similarity scores
   (CADTestBench, https://arxiv.org/abs/2605.07807). Snapshots may help the agent, never acceptance.
6. **First-class sweeps and lofts (G1).** BenchCAD found frontier models replace sweeps, lofts and twist-extrudes with
   plain extrudes (https://arxiv.org/abs/2605.10865); the operation registry makes these typed operations with checks
   that detect the substitution.
7. **Client-side preview from exported meshes**, not a server video stream: cheaper, works offline, and the preview
   is always the saved artifact (F09 rule: render only associated saved artifacts).
8. **AI is still a proposal.** Every agent-written value keeps the E05 grounding and origin labels; budgets and the
   owner gates for live calls are unchanged.

## 6. Gaps, with Phase 3 placement

| Gap | What to build | Phase 3 task | Notes |
|---|---|---|---|
| G1 construction layer | MEL v0.2 `construct` block: sketch (lines, arcs, constraints later), extrude, revolve, sweep, loft, fillet/chamfer, shell, pattern, boolean; typed, unit-checked, no loops/recursion beyond bounded patterns; budgets per FRAMEWORK.md | F02 registry ops; F04/F05 after the v0.1 vertical slice | Needs owner scope decision (Q1): Phase 3 currently says "not a universal CAD feature editor" |
| G2 op stream + artifact graph | OperationDescriptor execution emits `(op, span)`; adapter returns ArtifactGraph (op -> faces/edges/solids with span); semantic binding queries | F02 (records), F03 (adapter emits it), F07 (bindings used by checks) | Basis for code <-> UI editing in F09 |
| G3 kernel service | KernelAdapter (describe/probe/supports/build/inspect/export/cancel) as a bounded worker protocol; mock mode that type-checks and plans without geometry; per-program snapshot tests (op log + measurements, compared within tolerance) | F03, F08 | OCCT via CadQuery/OCP first; build123d conformance in F03 (Q2) |
| G4 edit loop | Agent tools: read program, propose construction diff, compile, execute, read check evidence, read artifact-graph queries; loop budget (iterations, calls, wall clock); requirement plan read-only; stop on pass or budget | F08 (tools), F09 (loop + UI) | Offline fixture-driven first; any live run needs owner authorization (Q3) |
| G5 legacy path | Decide the fate of free-form `/generate` (LLM CadQuery in `compiler.py` sandbox): keep as `geometry_checks_only`, or re-express through G1 | F01 decision | Q5; no change to `compiler.py`/allowlists without the owner |

## 7. Build order

0. This study merged; owner answers Q1-Q6.
1. **F01** scope freeze, with the answers folded in (construction layer yes/no and its v0.2 timing; kernel binding;
   legacy path).
2. **F02** ProgramIR, RequirementPlan/ConstructionGraph split, OperationDescriptor registry including the G1 ops as
   records (no execution), ArtifactGraph and binding-query records.
3. **F03** OCCT adapter emitting the span-tagged op log and artifact graph; PicoGK adapter; mock mode; conformance
   suite on analytical shapes (incl. sweep/loft); offline snapshot tests in CI.
4. **F04/F05** MEL v0.1 (beam vertical slice, as planned), then v0.2 construction block compiled to the registry.
5. **F06/F07** revisions; checks bound through semantic identity on delivered geometry.
6. **F08** one runtime for Python/CLI/HTTP/MCP, including the agent tool set (G4 tools).
7. **F09** editor with code <-> feature-tree selection via the artifact graph; agent edit loop (offline fixtures, then
   an owner-authorized live qualification with its own budget and tag namespace).
8. **F10** release qualification. **F11** own-kernel go/no-go only after measured OCCT limits; current evidence
   (Fornjot archived June 2026, truck's STEP maturity unclear) says no-go for now.

Experimental validation (owner: Zoo is "experimental validation only") stays at the level of public behaviour we can
reproduce on our own stack, for example confirming that adjacency-named edges survive an upstream edit in our
adapter. Nothing is run against Zoo's service.

## 8. Open-stack choices (licences to re-check at F01)

| Component | Licence (public page, 2026-11-21) | Role |
|---|---|---|
| OCCT | LGPL-2.1 + Open CASCADE exception | B-rep kernel |
| CadQuery | Apache-2.0 | current Python binding; already in the sandbox |
| build123d | Apache-2.0 | candidate typed binding (F03) |
| Manifold | Apache-2.0 | optional fast mesh booleans for previews/meshes; no B-rep |
| PicoGK | already integrated (E01) | voxel/implicit backend, geometry-only claims |
| truck, Fornjot | Apache-2.0 / 0BSD; Fornjot archived | not used |

## 9. Owner questions

1. **Construction layer:** should MEL grow a typed geometry-construction block (G1, KCL-class but requirement-bound)
   after the v0.1 vertical slice? Recommendation: yes, as MEL v0.2 inside Phase 3.
2. **Kernel binding:** keep CadQuery/OCP for v0.x and evaluate build123d in F03 conformance? Recommendation: yes.
3. **Agent loop:** build it offline first against fixtures; a live qualification later with its own authorization,
   call budget and tag namespace? Recommendation: yes.
4. **Preview:** client-side mesh preview of saved artifacts instead of server-side streaming? Recommendation: yes.
5. **Legacy `/generate`:** keep as a separately labelled geometry-only path, or retire once G1 lands?
   Recommendation: keep, labelled, until G1 covers its prompts; decide at F10.
6. **Reuse:** ideas from Zoo's MIT code only, no code copy? Recommendation: yes (owner's "rebuild ourselves").

## 10. Sources (read 2026-11-21)

Zoo: https://github.com/KittyCAD/modeling-app (README; rust/kcl-lib Cargo.toml, CONTRIBUTING.md,
src/engine/mod.rs, src/execution/mod.rs, src/execution/artifact.rs; docs/kcl-lang/numeric.md, modules.md,
edge-references.md); https://docs.rs/kittycad-modeling-cmds; https://docs.rs/kittycad/latest/kittycad/ml/struct.Ml.html;
https://zoo.dev/research/zoo-cad-engine-overview; https://zoo.dev/research/zookeeper;
https://zoo.dev/docs/zoo-design-studio/zookeeper; https://zoo.dev/docs/kcl-lang/known-issues;
https://zoo.dev/docs/kcl-book/tags.html.
Leo: https://getleo.ai/llms.txt; https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/;
https://machinedesign.com/automation-iiot/article/55330891/leo-ai-leo-ai-how-cad-aware-ai-is-changing-mechanical-design-and-engineering-workflows.
Open stack: https://dev.opencascade.org/doc/overview/html/occt_public_license.html; https://github.com/CadQuery/cadquery;
https://github.com/gumyr/build123d; https://github.com/elalish/manifold; https://github.com/ricosjp/truck;
https://github.com/hannobraun/fornjot; https://wiki.freecad.org/Topological_naming_problem/en.
Research: https://arxiv.org/abs/2409.17106 (Text2CAD); https://arxiv.org/abs/2505.19713 (CAD-Coder);
https://arxiv.org/abs/2412.14042 (CAD-Recode); https://arxiv.org/abs/2410.05340 (CADCodeVerify);
https://arxiv.org/abs/2603.26512 (CADSmith); https://arxiv.org/abs/2605.10865 (BenchCAD);
https://arxiv.org/abs/2605.07807 (CADTestBench); https://arxiv.org/abs/2605.17448 (FEA-feedback agents).
