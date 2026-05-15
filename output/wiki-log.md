# Log

Chronological record of all operations.

## [2026-05-14] planning | Gap audit + Tier 4 Subagent Architecture added to mirum-implementation-todo.md

Full gap audit of the t2cIII codebase against the implementation plan and airfoil pipeline. 15 new tasks added and synced to `output/mirum-implementation-todo.md` + `wiki/synthesis/mirum-implementation-todo.md` (1,738 → 2,300 lines).

**Gap audit findings — 5 new tasks:**
- **T2-14** (Gemini Model String Centralization): All 5 backend files hardcode model strings despite `config.py` now defining `GEMINI_MODEL_HEAVY`/`GEMINI_MODEL_LITE`. 0.5-day mechanical fix.
- **T2-15** (Airfoil Phase 2 Validation): `_run_aero_stage()` is implemented in `main.py` but untested end-to-end. Needs: live XFOIL test on Windows, NaN guard, Dockerfile `xfoil` package, CI mock integration using `analysis/tests/mock_xfoil`.
- **T2-16** (Airfoil Phase 3 Frontend): `aero_report` field is populated in SSE payload but Streamlit frontend silently drops it. Needs Plotly polar chart with stall annotation + CSV download.
- **T2-17** (Domain D Eval Harness Prompts): Zero prompts with `regression_for: ["D"]` in the eval harness despite Domain D being the richest implementation. 8 NACA/wing prompts specified.
- **T3-09** (VLM Zebra Rendering Pipeline): Two production stubs (`continuity_check.py:247`, `geometry_analysis.py:357`) both blocked on a STEP→zebra-rendered-image pipeline. Shared implementation path via pyrender + OSMesa + Gemini Vision.

**Tier 4 — Subagent Architecture (10 tasks, T4-01 through T4-10):**
Breaking the monolithic `agents.py` (1,422 lines) into a proper subagent system. Design: each agent (`Planner`, `Machinist`, `Critic`, `Assembler`, `Clarifier`, `REA`, `AeroAnalyst`) runs as an independent class with own context window, communicates via `SubagentMessage` TypedDict, and can be upgraded independently via `ModelRegistry`. Key tasks:
- T4-01: `SubagentBase` ABC + `SubagentMessage` TypedDict + `SubagentContext` dataclass
- T4-02: `Orchestrator` state machine replacing 80+ lines of inline pipeline logic in `main.py`
- T4-03–T4-07: Extract each agent (Planner, Machinist, Critic, Assembler, Clarifier/REA) — backward-compatible shims maintained
- T4-04: Machinist spawned in parallel via `asyncio.gather` (currently sequential per part) — target ≤1.5× wall-clock at 4× concurrency
- T4-08: `AeroSubagent` wrapping `analysis/airfoil.py`; can run in parallel with assembler for Domain D
- T4-09: Per-subagent telemetry with `trace_id`/`parent_id` propagation; `subagent_spans.jsonl` JSONL sink
- T4-10: `ModelRegistry` singleton + `POST /admin/model-config` hot-swap without process restart

**Files updated:** `output/mirum-implementation-todo.md` (T2CIII repo) + both vault copies synced.

## [2026-04-19] implementation | All 8 Tier 3 tasks implemented in t2cIII codebase

Executed all Tier 3 research-bet tasks from `mirum-implementation-todo.md`. All 8 tasks marked ✅ IMPLEMENTED in `output/mirum-implementation-todo.md`. Each task delivers a complete, production-ready scaffold or research module that is dormant until its activation criterion is met.

**New research modules (research/):**
- `research/semantic_mate_classifier.py` — T3-01: OntoBREP 3-layer GIN classifier; 12 leaf mate types; 48-dim joint feature vectors from OCCT face geometry; rule-based bootstrap; trains on Fusion 360 Gallery; `SEMANTIC_MATE_TYPE_FIELD_SCHEMA` ready to inject into Planner; activation criterion: annotated dataset ≥ 500 assemblies
- `research/joinable_gnn.py` — T3-02: JoinABLe-style GNN cross-check (Willis et al., CVPR 2022); FaceGraph extraction from STEP via OCCT; `MateConfidenceLevel` enum (HIGH/MEDIUM/LOW/VERY_LOW); `score_mate_confidence()` compares GNN vs. Planner specs; `run_mate_cross_check()` integrates after Machinist; activation criterion: GNN checkpoint exists at `checkpoints/joinable_gnn.pt`
- `research/sketchgraphs_pretrain.py` — T3-03: Transfer learning bridge; 11 SketchGraphs constraint types → GIN pre-training; `export_transfer_weights()` → `apply_transfer_weights()` onto JoinABLe; `run_ablation_study()` measures ≥5% accuracy gain; activation criterion: 15M SketchGraphs HDF5 downloaded

**New training and inference modules:**
- `training/rlaif_machinist.py` — T3-04: RLAIF/PPO fine-tuning for Machinist; hybrid reward 0.7 × execution_reward + 0.3 × preference_reward; macro-action credit assignment via AST method-chain decomposition; `RLAIFMachinist.train()` with KL monitoring and early stopping; `load_prompt_dataset()` mines telemetry for failed generations; activation criterion: SFT checkpoint + GPU cluster (2× A100)
- `inference/constrained_decoding.py` — T3-05: Grammar-constrained decoding via Outlines + CadQuery EBNF grammar; 17 known hallucinated methods blacklisted; `validate_cadquery_structure()` with 4 AST checks; `analyze_structural_error_rate()` gates deployment (only activate if structural errors >20% of failures post-SFT); `benchmark_latency()` ensures <20s p50; activation criterion: structural error rate >20%

**Modified backend files:**
- `backend/classifier.py`, `backend/strategies.py`, `backend/cad_utils.py` — T3-06: Domain E (Sheet Metal) added as fifth domain; keyword triggers for sheet metal / thin wall / enclosure / K-factor; design rules: bend radius ≥ 1× thickness, flange ≥ 2× (thickness + bend_radius), K-factor 0.33, standard gauges 0.8–2.0mm; `make_bent_bracket()` and `make_enclosure_panel()` template functions; Domain E takes precedence over Domain A when sheet metal keywords present
- `backend/geometry_analysis.py` — T3-07: Gaussian curvature analysis via OCCT GeomLProp_SLProps; per-face UV sampling (n=25–30); passes if <25% saddle points (K<−1e⁻⁴); `curvature_check_for_critic()` wired into `run_critic_loop()` in agents.py after G2 check; VLM zebra stub for future multimodal integration; `evaluate_domain_c_curvature()` for eval harness
- `backend/schemas.py`, `backend/postprocessing.py`, `backend/main.py` — T3-08: PMI annotation pipeline; `PMIAnnotation`/`PartPMI`/`PMIGenerationRequest` Pydantic models; 6 regex patterns for ISO fits, bilateral tolerances, surface finish (Ra/Rz), GD&T, IT grades, ISO 2768; `extract_pmi_from_prompt()`, `has_tolerance_callouts()`, `attach_pmi_to_step()` (XCAF + JSON sidecar fallback); `extract_pmi_via_llm()` async Gemini path; `/pmi/extract` and `/pmi/annotate` REST endpoints; `pmi_detected` flag added to `/generate` response payload; activation criterion: ISO AP242 OCCT build with XCAF enabled

**Key architectural notes:**
- All T3 modules are imports-guarded or lazily loaded — missing optional deps (PyTorch, trl, outlines, owlready2) do not crash the running server
- JoinABLe (T3-02) and SketchGraphs pretrain (T3-03) are layered: T3-03 pre-trains weights that T3-02 can consume via `apply_transfer_weights()`
- RLAIF (T3-04) consumes the SFT checkpoint produced by T2-11 (`finetune_machinist.py`) — sequential dependency documented in code comments
- Domain E (T3-06) is immediately live; all other T3 modules remain dormant pending activation criteria

**External blockers documented:**
- T3-01/T3-02/T3-03: Require Fusion 360 Gallery download (public dataset, Apache 2.0) and/or SketchGraphs HDF5 (Princeton, CC-BY)
- T3-02/T3-03: Require PyTorch + PyG installation (`pip install torch torch-geometric`)
- T3-04: Requires SFT base checkpoint (T2-11) + GPU cluster (2× A100 min) + `pip install trl`
- T3-05: Requires `pip install outlines>=0.0.40`; only activate if structural error rate >20% post-SFT
- T3-08: Full XCAF PMI embedding requires OCCT built with `HAVE_TK_XCAF=ON`; JSON sidecar fallback is always active

## [2026-04-18] implementation | All 13 Tier 2 tasks implemented in t2cIII codebase

Executed all Tier 2 tasks from `mirum-implementation-todo.md`. All 13 tasks marked ✅ IMPLEMENTED in both `output/mirum-implementation-todo.md` and `wiki/synthesis/mirum-implementation-todo.md`.

**Files modified:** `backend/schemas.py`, `backend/agents.py`, `backend/main.py`, `backend/strategies.py`, `backend/cad_utils.py`, `frontend/app.py`

**New backend files:** `backend/continuity_check.py` (G2 continuity, T2-04), `backend/exporters.py` (multi-format export including Parasolid scaffold, T2-09)

**New data pipeline files:** `data_pipeline/prompt_stratification.py` (T2-05), `data_pipeline/deepcad_to_cadquery.py` (T2-06), `data_pipeline/abc_assembly_pipeline.py` (T2-07), `data_pipeline/finetune_machinist.py` (T2-11)

**New output files:** `output/demos/aerospace/demo_prompts.md` (T2-13), `output/sbir-phase1-draft.md` (T2-13)

**Key changes per task:**
- T2-01: `FEATURE_VOCABULARY` constant + `rule_feature_vocabulary` injected into Domain A/B strategy prompts
- T2-02: `dof_min`/`dof_max`/`dof_unit` fields in `MatingRule`; `validate_dof_ranges()` in agents; `/plan` returns `dof_violations`
- T2-03: `GEAR`/`SCREW`/`RACK_PINION`/`CAM` added to `MateType`; `coupling_ratio` in `MatingRule`; `extract_kinematic_metadata()` in agents
- T2-04: `continuity_check.py` with OCCT analytical G2 check + VLM Zebra stub; wired into Domain C critic loop
- T2-05: Threaded stratification pipeline with resume support; mines telemetry for seed pairs
- T2-06: DeepCAD JSON → CadQuery converter + OCCT validation + Gemini annotation pipeline
- T2-07: ABC STEP classifier (SQLite index) + constraint-based pair assembly + Gemini annotation
- T2-08: `ManifestDiff`/`RefinementRequest` schemas; `run_refiner()`/`apply_manifest_diff()` in agents; `/refine` endpoint; Streamlit refinement UI
- T2-09: `exporters.py` with Parasolid (CAD Exchanger, graceful fallback to STEP), STL, DXF; `/export/parasolid` endpoint
- T2-10: 6 template functions in `cad_utils.py` (flanged housing, shaft with keyway, L-bracket, bearing housing, spacer, boss with counterbore)
- T2-11: QLoRA SFT training scaffold for Qwen2.5-7B + `FinetunedMachinist` inference wrapper + eval harness
- T2-12: `DesignRequirements` schema; `run_rea()` agent; wired into `/generate` and `/plan` pipelines; `/requirements` endpoint
- T2-13: 5 aerospace demo prompts with validation checklists; full SBIR Phase I application draft (8 sections)

**External blockers documented:**
- T2-06/T2-07: Require DeepCAD/ABC dataset downloads (public, no license)
- T2-09: Requires CAD Exchanger commercial license (~$3K/year; eval available)
- T2-11: Requires GPU cluster (2× A100 min) and T2-05/T2-06 training data

## [2026-04-18] implementation | All 11 Tier 1 tasks implemented in t2cIII codebase

Executed all Tier 1 tasks from `mirum-implementation-todo.md` directly in the t2cIII codebase. All 11 tasks marked ✅ IMPLEMENTED in both `output/mirum-implementation-todo.md` and `wiki/synthesis/mirum-implementation-todo.md`.

**Files modified:** `backend/schemas.py`, `backend/agents.py`, `backend/main.py`, `backend/telemetry.py`, `frontend/app.py`

**New files created:** `backend/retrieval.py` (TF-IDF few-shot retrieval), `backend/postprocessing.py` (parametric variable extraction), `backend/evaluation.py` (LVM scoring via Gemini), `backend/failure_analysis.py` (operation-type failure attribution), `backend/cost_analysis.py` (Gemini API cost tracking), `backend/output/icp-interview-guide.md` (ICP discovery interview guide)

**Key architectural changes:**
- T1-01: `MateType` enum added to schemas; `run_assembler_constraint()` with `cq.Constraint` + `assembly.solve()` replaces XYZ translation path; Planner prompt updated to emit mate types instead of coordinates
- T1-02: `check_assembly_interpenetration()` added; bounding box overlap detection pre-assembly
- T1-03: `/plan` endpoint added to `main.py`; `render_plan_review()` in Streamlit frontend; two-phase clarify→review→generate flow
- T1-04: `run_clarifier()` agent added; `/clarify` endpoint; frontend detects ambiguity before planning
- T1-05: Pure Python TF-IDF retrieval index; domain-boosted similarity; few-shots injected into Machinist on first attempt
- T1-06: `parameterize_script()` extracts named variables (RADIUS_1, HEIGHT_1) from CadQuery method arguments; parameter sidebar in Streamlit
- T1-07: `compute_lvm_score()` uses Gemini to score semantic_accuracy, feature_completeness, geometric_plausibility; fire-and-forget from Critic Loop
- T1-08: `classify_failure()` pattern-matches 17 operation types from tracebacks; `/stats` endpoint returns breakdown
- T1-09: Domain-stratified metrics exposed via `/stats`; failure_operation logged per attempt in telemetry
- T1-10: `compute_cost_report()` reads token_usage telemetry events; pricing constants for Gemini 2.5 Flash
- T1-11: 7-section ICP interview guide with 6-dimension scoring rubric and go/no-go threshold (7/10 with assembly_bottleneck ≥ 4 AND concept_resonance ≥ 7)

## [2026-04-18] synthesis | Mirum Implementation To-Do — developer-ready task list, 27 tasks across 3 tiers

Generated a comprehensive implementation to-do list from `mirum-capability-gaps.md` and `mirum-technical-architecture.md`. Saved to `wiki/synthesis/mirum-implementation-todo.md` and `output/mirum-implementation-todo.md`.

**Coverage:** 19 Tier 1/2 tasks (days–weeks, high impact) + 8 Tier 3 research bets (months). Every task includes implementation steps, files/modules involved, dependencies, effort estimate, and success criteria.

**Tier 1 (Days):** T1-01 Constraint-based assembly v1 migration (XYZ → cq.Constraint + assembly.solve()); T1-02 PMC interpenetration detection; T1-03 Assembly Manifest review UI; T1-04 ProCAD clarification gate; T1-05 Few-shot retrieval augmentation (vector store from telemetry); T1-06 Parametric design history exposure (named variables + UI sliders); T1-07 LVM Score evaluation metric; T1-08 F1 per operation type / error attribution telemetry; T1-09 Domain-stratified evaluation; T1-10 SaaS pricing calibration to compute cost; T1-11 ICP discovery interview guide.

**Tier 2 (Weeks):** T2-01 Features-based vocabulary in strategy prompts; T2-02 Limit mates / DoF range annotation; T2-03 Mechanical mate vocabulary (Gear, Screw, Cam, Rack-Pinion); T2-04 G2 continuity validation for Domain C; T2-05 Multi-level prompt stratification for SFT dataset; T2-06 DeepCAD → CadQuery training data pipeline (~100K pairs); T2-07 ABC Dataset → synthetic assembly pipeline (~500K 2-part assemblies); T2-08 Multi-turn refinement /refine endpoint; T2-09 SOLIDWORKS Parasolid .x_t export via CAD Exchanger; T2-10 Program sketching / expand cad_utils.py; T2-11 Fine-tuned Machinist v2 (Qwen2.5-7B); T2-12 Requirements Engineering Agent pre-Planner; T2-13 Aerospace beachhead demo assemblies + SBIR Phase I application.

**Tier 3 (Research Bets):** T3-01 Semantic Mate bootstrapping via OntoBREP classifier; T3-02 JoinABLe GNN post-Machinist geometric cross-check; T3-03 SketchGraphs 2D constraint pre-training for mate prediction transfer; T3-04 RLAIF fine-tuning for Machinist (hybrid execution + AI preference reward); T3-05 AST-aware grammar-constrained decoding; T3-06 Domain E sheet metal classification; T3-07 Gaussian curvature analysis for Domain C; T3-08 Tolerance and PMI validation for manufacture-ready output.

**Recommended quick-start sequence** included: T1-01 → T1-03 → T1-04 → T1-02 → T1-08 → T1-05 → T1-06 → T1-07 → T1-09 → T1-11 (parallel) → T2-10 → T2-05 → T2-06 → T2-11 → T2-08 → T2-09 → T2-13 → T3-04 → T3-01.

**Dependency graph** included. Index updated. Total vault: ~200+ pages.

## [2026-04-18] synthesis | Mirum Capability Gaps — full-vault audit, 30+ gaps across 8 dimensions

Conducted a systematic audit of all ~190 vault pages (sources, entities, concepts, synthesis) to surface ideas not yet applied to or planned for Mirum. Saved to `wiki/synthesis/mirum-capability-gaps.md` and `output/mirum-capability-gaps.md`.

**8 dimensions audited:**
1. **Geometric Representation** — Features-based vocabulary in prompts/validation (High/Easy); G2 continuity validation for Domain C via Zebra Analysis + VLM (High/Medium); Gaussian curvature as Domain C quality metric (Medium/Medium); PMC-based interpenetration detection after `assembly.solve()` (High/Easy); Non-manifold/sheet metal Domain E classification (Medium/Hard)
2. **Mate Vocabulary and Constraints** — Mechanical mate vocabulary (Gear, Screw, Cam, Slot coupling constraints) (High/Medium); Semantic Mate bootstrapping pipeline via OntoBREP classifier over Fusion Gallery (High/Hard); JoinABLe GNN as bottom-up post-Machinist cross-check (Medium/Hard); Limit mates / DoF range annotation in MatingRule schema (Medium/Medium)
3. **Training and Fine-Tuning** — Multi-level prompt stratification from Text2CAD (High/Easy); Visual DPO for assembly-level rendering from CADFusion (High/Medium); DeepCAD → CadQuery conversion pipeline (~180K training pairs) (High/Medium); SketchGraphs 2D constraint pre-training for mate prediction transfer (Medium/Hard); ABC Dataset → synthetic assembly pipeline (High/Medium); HistCAD 5-modality alignment including `.f3d` native files (Medium/Medium)
4. **Evaluation** — LVM Score for semantic intent matching without reference geometry (High/Easy); Zebra Analysis as Domain C quality metric (Medium/Medium); F1 per CadQuery operation type for error attribution (Medium/Easy); Domain-stratified evaluation by Domain A/B/C/D (Medium/Easy); Tolerance/PMI validation for manufacture-ready output (Medium/Hard)
5. **Data Sources** — CAD Exchanger for cross-format ingestion of SOLIDWORKS native files (High/Medium); Multi-turn refinement sessions as (original, refinement, revised) training triples (High/Medium); SMLib as NURBS quality upgrade for Domain C (Medium/Hard)
6. **Code Generation** — Few-shot retrieval augmentation: vector store of telemetry successes injected at inference (High/Easy); Program sketching: expand `cad_utils.py` with parametric template functions (Medium/Medium); AST-aware grammar-constrained decoding for structural correctness (Medium/Hard); Parametric design history exposure: extract named variables for UI slider editing (High/Medium)
7. **Agent Design** — ProCAD-style proactive clarification gate pre-Planner (High/Easy); Assembly Manifest review UI before execution (High/Easy); Requirements Engineering Agent structured pre-extraction (Medium/Medium); Macro-action RL credit assignment at method-chain granularity (Medium/Hard)
8. **Business/GTM** — SOLIDWORKS `.x_t` Parasolid output via CAD Exchanger (High/Medium); Aerospace beachhead operationalization: 5 demo assemblies + SBIR Phase I application (High/Medium); ICP discovery interview plan: 10 structured interviews before v1 (High/Easy); Multi-turn conversational refinement as product differentiator via `/refine` endpoint (High/Medium); SaaS pricing calibration to Gemini API compute cost per assembly (Medium/Easy)

**Key finding:** The vault contains substantially more actionable technique than the current Mirum design plan has absorbed. The highest-ROI cluster (Easy/High-Impact) includes items that require no model changes and could be implemented in days: retrieval-augmented few-shot injection, parametric variable extraction for the UI, PMC interpenetration detection, Assembly Manifest review UI, ProCAD clarification gate, and LVM Score evaluation.

Index updated. Total vault: ~195+ pages.

## [2026-04-18] synthesis | Mirum — The Next Era of Text-to-CAD Generation

Wrote the capstone analysis document synthesizing the full vault into a forward-looking vision and argument. Saved to `wiki/synthesis/mirum-next-era-analysis.md` and `output/mirum-next-era-analysis.md`.

**Structure:** Six-part analytical argument — (I) Diagnosing the current state: the DeepCAD trap, three generations hitting the same ceiling, Zoo.dev as commercial reference, what "stuck" means for industrial buyers; (II) Theoretical foundation: B-rep and NURBS as the geometric substrate, assembly as a constraint graph not a collection of parts, the CodeAct/Self-Debugging agentic paradigm, fine-tuning as capability concentration; (III) Mirum's architectural thesis: CadQuery defended on five grounds (convergence, executability, assembly extension, feedback quality, deterministic geometry library), the agentic scaffold as a principled decomposition of reasoning vs. computation tasks, typed assembly constraints as the v1 load-bearing change, the outer geometric validation loop from CADSmith, RLAIF as the training path; (IV) v0→v4 roadmap with specific papers and datasets enabling each step; (V) Competitive moat: why Zoo's head start doesn't close the assembly gap, why fine-tuning-only approaches can't replicate the agentic scaffold moat, the data flywheel hypothesis, commercial differentiation through aerospace/defense beachhead; (VI) Five open questions: constraint solver reliability, mate vocabulary bootstrapping, Machinist context isolation, architecture scaling for multi-part assemblies, evaluation standard definition.

Index updated. Total vault: ~190+ pages.

## [2026-04-18] ingest | CadQuery generation + Mirum gap research — 11 new source/entity/concept/synthesis pages

Ran two parallel search fronts: (1) CadQuery-specific code generation research; (2) broader Mirum gaps across training data, assembly, evaluation, input modalities, model architecture, and commercialization.

**Raw files created (5):** cad-recode-paper.md, cadvlm-paper.md, joinable-paper.md, dont-mesh-paper.md, rlaif-code-paper.md

**Source pages (6):**
- [[CAD-Recode Paper]] (arXiv 2412.14042, ICCV 2025) — point cloud → CadQuery via Qwen2-1.5B + 1M synthetic pairs; 10× CD improvement; validates CadQuery as universal output format
- [[CadVLM Paper]] (arXiv 2409.17457, ECCV 2024) — first multimodal LLM for parametric CAD; dual encoder (sketch images + primitive sequences); autocompletion, autoconstraint, image-conditional generation; Autodesk Research
- [[JoinABLe Paper]] (arXiv 2111.12772, CVPR 2022) — GNN over B-rep face graphs for pairwise joint prediction; 79.53% accuracy (near human 80%); introduces Fusion 360 Gallery assembly dataset (now the primary assembly training source)
- [[Don't Mesh with Me Paper]] (arXiv 2411.15279, Nov 2024) — first fine-tuned LLM for CSG generation; B-rep→CSG conversion creates training pairs without human annotation; 37K pairs, 1.3B model
- [[RLAIF for Code Generation Paper]] (arXiv 2406.20060, June 2024) — RLAIF for API-usage code in lightweight LLMs; AI preference + execution reward hybrid; scalable path to Machinist fine-tuning without human annotators

**Entity pages (2):**
- [[JoinABLe]] — discriminative baseline for Mirum's generative assembly problem; GNN pairwise joint prediction from B-rep
- [[CAD-Recode]] — confirms CadQuery as standard output across text-to-CAD and reconstruction; scan-to-assembly product opportunity

**Concept pages (4):**
- [[Point Cloud to CAD]] — reverse engineering task; CAD-Recode state of art; future Mirum scan-to-assembly input
- [[Multimodal CAD]] — input modality taxonomy; CadVLM (sketch), CAD-Recode (point cloud), CAD-MLLM (multi-view); future Mirum v2/v3 input extensions
- [[CSG Generation]] — simpler-than-CadQuery generation; key insight: B-rep→code pipeline creates training data without human labeling
- [[RLAIF]] — RL from AI feedback; execution + AI preference hybrid reward; practical mechanism for Mirum Machinist RL training

**Synthesis page (1):**
- [[Mirum Research Gaps]] — comprehensive 8-dimension gap analysis (code quality, assembly, training data, evaluation, input modalities, model architecture, output format, commercialization); each gap mapped to specific papers and recommended actions; priority matrix ordering 9 initiatives from "now" to "v3 enterprise"

Key findings from this research round:
1. **Five independent systems now target CadQuery Python as output** (ProCAD, Text-to-CadQuery, CADSmith, t2cIII, CAD-Recode) — format decision is definitively settled
2. **JoinABLe sets the pairwise assembly accuracy ceiling** (79.53%) — Mirum's text-conditioned approach may exceed this because text carries functional context B-rep alone doesn't
3. **B-rep → code conversion pipeline** (from Don't Mesh with Me) is the key to scaling Mirum's training data from Fusion 360 Gallery without human annotators
4. **RLAIF + execution reward** is the practical path to Machinist RL fine-tuning using t2cIII's existing telemetry infrastructure — no human annotation infrastructure needed
5. **OCCT geometric validation outer loop** (CADSmith) is the highest-ROI near-term improvement: pure scaffolding, dramatic CD reduction

Index updated with 6 source entries, 2 entity entries, 4 concept entries, 1 synthesis entry. Total vault: ~185+ pages.

## [2026-04-18] ingest | Agentic architecture research — t2cIII deep read + 5 source pages + 2 entities + 1 synthesis

Completed the full t2cIII codebase read (all modules: agents, classifier, compiler, main, schemas, strategies, cad_utils, telemetry, debug, frontend/app.py, docker-compose.yml, requirements.txt). Key findings from remaining modules:

**telemetry.py**: JSONL rotating log (50MB × 5 files) at `/tmp/mirum/telemetry/critic_loop.jsonl`. Captures per-attempt records: `part_id`, `domain_classification`, `attempt_number`, `generated_code`, `traceback_error`, `success_status`. This telemetry dataset is directly usable as RL training signal for a ToolCAD-style fine-tuning pipeline. Failures are swallowed (best-effort analytics) so broken telemetry never breaks the pipeline.

**debug.py**: Unsanitized JSONL error log at `/tmp/mirum_debug.jsonl`. Captures full tracebacks (not path-redacted) alongside pipeline stage, error type, and context dict. Developer diagnostic tool separate from client-facing `_safe_error()` path redaction in main.py.

**frontend/app.py**: Streamlit UI. SSE streaming from `/generate` endpoint displayed as live step-by-step progress (⏳/✅/❌ icons). `render_glb_viewer()` uses Google's `<model-viewer>` web component (via CDN) for in-browser 3D. `streamlit-ace` editor lets users modify and re-run CadQuery code after generation. `/run` endpoint allows direct script execution outside the full pipeline. Status bar tracks pipeline state.

**docker-compose.yml**: Two services — `backend` (FastAPI, port 8000) and `frontend` (Streamlit, port 8501). Backend mounts as read-only with tmpfs `/tmp` (256MB). Named volumes for `tmp_parts` and `telemetry_logs`. `.env` file for `GOOGLE_API_KEY`. No GPU requirement.

Searched web for agentic code generation and CAD generation research. Created the following new pages:

**Source pages (5):**
- [[CodeAct Paper]] (arXiv 2402.01030, ICML 2024) — Executable Python as LLM agent action space; multi-turn self-debugging; foundational reference for t2cIII's architecture
- [[Self-Debugging Paper]] (arXiv 2304.05128, ICLR 2024) — Rubber duck debugging for LLMs; companion "silver bullet" paper shows self-repair gains require careful evaluation against matched baselines
- [[CADSmith Paper]] (arXiv 2603.26512, March 2026) — Dual nested loops: inner (execution errors) + outer (OCCT bounding box/volume measurements + VLM judge); 100% execution rate, CD 28→0.74; RAG over CadQuery docs
- [[ToolCAD Paper]] (arXiv 2604.07960, April 2026) — CAD modeling gym + curriculum RL; trains open-source models to match GPT-4o/Gemini on text-to-CAD; CAD-CoT reasoning traces
- [[From Idea to CAD Paper]] (arXiv 2503.04417, March 2025) — V-Model-inspired MAS: Requirements Engineering Agent + CAD Engineering Agent + Vision QA Agent; multi-view VLM feedback

**Entity pages (2):**
- [[CodeAct]] — Entity stub with t2cIII relationship table
- [[CADSmith]] — Entity stub with architectural comparison to t2cIII

**Synthesis page (1):**
- [[Agentic Architecture Landscape]] — Three-generation history of agentic CAD systems (2024–2026); t2cIII position table (leads/lags); self-repair quality ceiling analysis; 5 prioritized Machinist improvement recommendations (geometric validation outer loop → telemetry analysis → rubber duck step → RL training → assembly)

Index updated with 5 new source entries, 2 new entity entries, 1 new synthesis entry. Total vault: ~165+ pages.

## [2026-04-18] todo-complete | Worked through full output/todo.md backlog (tasks 3–8)

Completed all six deferred tasks in sequence. Summary:

**Task 3 — Text-to-CadQuery paper clipped and ingested.**
Clipped arXiv 2505.06507 (Xie & Ju, May 2025) via web search (arxiv.org blocked by egress proxy). Created `raw/text-to-cadquery-paper.md`, `wiki/sources/text-to-cadquery-paper.md`, and `wiki/entities/text-to-cadquery.md`. Key finding: Gemini self-correction annotation pipeline (JSON → CadQuery → subprocess → retry on error) produces 170K training pairs; Qwen2.5-3B achieves 69.3% exact match; confirms ProCAD builds on this work. Single-part only.

**Task 4 — Assembly dataset survey.**
Researched Fusion 360 Gallery, ABC Dataset, SketchGraphs, HistCAD, and STEP AP242 format. Created entity pages for [[Fusion 360 Gallery]], [[ABC Dataset]], [[SketchGraphs]], [[HistCAD]], and synthesis page [[Assembly Datasets for Mirum]]. Key finding: Fusion 360 Gallery (8,251 assemblies, 32,148 joint annotations, public) is the strongest existing foundation; needs VLM+LLM text annotation. STEP AP242 can encode kinematic constraints but most CAD tools strip them on export — raw `.sldasm`/`.f3d` files are required for mate preservation.

**Task 5 — Onshape Assembly API clipped.**
Researched Onshape REST API via web search (onshape-public.github.io blocked). Created `wiki/sources/onshape-assembly-api.md`. Documents: 9 mate types (Fastened/Revolute/Slider/Cylindrical/Pin Slot/Planar/Ball/Parallel/Tangent) with DoF specs; `getAssemblyDefinition` endpoint with `includeMateFeatures`, `includeMateConnectors` query params; JSON mate feature structure (`BTMMate-64`, `mateType` enum, `matedEntities` with occurrence paths, `matedCS` coordinate systems). Key implication: Onshape API mate schema reveals the minimal ML generation target — `(mateType, occurrenceA, occurrenceB, csA, csB)` tuples.

**Task 6 — t2cIII codebase read and documented.**
Mounted `C:\Users\Kevin\Developments\T2CIII\` and read all 8 backend modules (README, architecture, agents, classifier, compiler, main, schemas, strategies). Created `wiki/entities/t2ciii-codebase.md`. Critical finding: t2cIII is an **agentic orchestration system** (not a trained model) using Gemini 2.5 Flash zero-shot. Pipeline: Planner → [Domain Classifier + Machinist + Critic Loop] × N (concurrent, ≤3) → Deterministic Assembler. Current assembly model is **translation-only** (`cq.Location(cq.Vector(X, Y, Z))`) — no constraint solving, no mate types. Output is CadQuery → OCCT → .glb + .step. AST security scanner with import allowlist.

**Task 7 — Mirum Technical Architecture synthesis.**
Created `wiki/synthesis/mirum-technical-architecture.md` — the primary engineering reference page. Covers: current state (t2cIII gaps), output format decision (CadQuery confirmed), assembly model migration path (v0 translation-only → v1 typed CadQuery constraints → v2 fine-tuned Machinist → v3 assembly training data → v4 full system), LLM strategy (agentic scaffold + fine-tuned subagents), training data strategy (Fusion 360 Gallery Phase 1), evaluation suite (6 metrics), evolution roadmap (v0→v4), and 4 open architecture questions (constraint solver reliability, Domain D assembly, Machinist context isolation, CadQuery vs. sequences).

**Task 8 — Deferred lint stubs.**
Created 5 stub pages: [[T-Splines]] (concept), [[Knot Insertion]] (concept), [[Degree Elevation]] (concept), [[LLaVA-NeXT]] (entity), [[Mixtral-50B]] (entity).

Index updated with 20 new entries across Sources, Entities, Concepts, and Synthesis. `output/todo.md` updated — all items marked complete. Total vault: ~150+ pages.

## [2026-04-18] synthesize | Text-to-CAD Landscape 2026 + Assembly-First: The Open Problem

Created two new synthesis pages from existing vault content (no new raw sources needed):

**[[Text-to-CAD Landscape 2026]]** (`wiki/synthesis/text-to-cad-landscape-2026.md`): Full competitive map of Text2CAD, CADFusion, ProCAD, Zoo.dev, and Mirum across output format, architecture generation, training data size/quality, assembly support, and commercial status. Key findings: (1) field has moved from scratch Transformers → pre-trained LLM backbones → multi-agent decomposition in ~18 months; (2) data quality compounds — ProCAD's 1.6K curated beats Text2CAD's 150K; (3) every player is single-part only; (4) Zoo is the only commercial entity with live product. Includes performance snapshot table (IR, CD, LVM score across all three research systems).

**[[Assembly-First: The Open Problem]]** (`wiki/synthesis/assembly-first-open-problem.md`): Structural analysis of why assembly generation is qualitatively harder than single-part. Four root causes: no assembly training dataset, unsettled mate vocabulary (SW compositional vs. Onshape DoF vs. Semantic Mates), harder evaluation (part validity + mate satisfaction + assembled geometry + kinematic correctness), and larger generation search space (parts × mate graph × per-part geometry). Documents all three mate vocabulary options with ML cost trade-offs, proposed minimum assembly evaluation metric suite, candidate architectures (hierarchical two-stage, graph-structured decoding, retrieval+adaptation), and five open research questions. Also contains Mirum's formal thesis statement (3-part).

**`output/todo.md`**: Created deferred task backlog tracking items 3–8 from the post-lint recommendation: Text-to-CadQuery clip, assembly dataset survey, Onshape FeatureScript docs, t2cIII codebase docs, Mirum Technical Architecture synthesis, deferred lint stubs (T-Splines, Knot Insertion, Degree Elevation, LLaVA-NeXT, Mixtral-50B).

Index updated with 2 new synthesis entries. Total vault: ~116 pages.

## [2026-04-17] setup | Vault initialized
Created vault "second-brain" as a business partner for Mirum, an assembly-first text-to-CAD generation model — covering math/engineering foundations (B-rep, splines), the t2cIII codebase, and business strategy.
Agent configs: CLAUDE.md.

## [2026-04-17] clip | Seed batch of 22 articles into raw/
Clipped articles via WebFetch across B-rep foundations, NURBS/spline continuity, CAD assembly semantics (Onshape + SOLIDWORKS), text-to-CAD research (Text2CAD, Text-to-CadQuery, CADFusion, ProCAD), the Zoo.dev commercial landscape, and B2B SaaS GTM. Two sources could not be clipped: the Semantic Mates TUM paper (PDF binary, needs manual download) and the BK Engineering assembly-constraints post (404). Ready for `/second-brain-ingest`.

## [2026-04-18] ingest | First batch of 13 raw sources
Compiled the empty wiki from 13 raw sources spanning B-rep foundations, NURBS theory, continuity, CAD kernels, assembly mates, and text-to-CAD research. Created:

- 13 source summary pages covering Wikipedia B-rep, Rhino Essential Math Ch.3, Wikipedia NURBS, CAD Exchanger BREP page, PLM Tech Talk continuity explainer, Analysis Situs kernel landscape tour, GoEngineer SOLIDWORKS mates walkthrough, Onshape mates help page, OCCT BRep format spec, OCCT Bottle Tutorial, TUM/fortiss Semantic Mates paper (including user study), Text2CAD NeurIPS paper, and the Text2CAD project page.
- 33 entity pages: kernels (OCCT, Parasolid, ACIS, C3D, SMLib, RGK, Catia CGM), CAD tools (SOLIDWORKS, Onshape, Rhinoceros 3D), research orgs (DFKI, RPTU, fortiss, TU München), datasets (DeepCAD), frameworks (Text2CAD), companies (CAD Exchanger, McNeel, GoEngineer, Tata Technologies, Analysis Situs, Boeing), historical figures (Bézier, de Casteljau, Versprille, Braid, Baumgart, Schoenberg), contemporary researchers (Khan, Perzylo, Rickert, Knoll), and Mirum itself as the anchor project.
- 36 concept pages covering the math (NURBS, B-splines, Béziers, Hermite, de Casteljau/de Boor, knots, control points, parametric curves/surfaces, curvature, Gaussian curvature, polysurfaces, trimmed surfaces, singularities), representation schemes (B-rep, CSG, faceted, voxel), continuity (geometric, parametric, Class A, Zebra analysis), standardization (STEP/ISO 10303, IGES), B-rep anatomy (topology vs geometry, non-manifold, features-based modeling, PMC), mates vocabulary (cad-mates, standard/advanced/mechanical mates, semantic mates, mate references, OntoBREP), and text-to-CAD (text-to-cad concept, design history).
- 5 synthesis pages: CAD Kernel Landscape, BRep vs CSG, Mates Across CAD Tools, Continuity Summary, Text2CAD for Mirum.

All pages cross-linked via [[wikilinks]] using Title Case. Total: ~87 pages. Index.md fully populated across all four categories. Lint recommended after next ingest (vault started empty; cross-reference gaps likely).

## [2026-04-18] lint-fix | Resolved all issues from first lint pass
Worked through all items in `output/lint-2026-04-18.md`. Changes made:

**Source title drift (§1):** Renamed H1 titles and frontmatter `name` fields in all 9 affected source pages to match canonical short forms used throughout the wiki. Titles now match: Parametric Curves and Surfaces (Rhino), Surface Continuity (PLM Tech Talk), OCCT BRep Format, OCCT Bottle Tutorial, Semantic Mates Paper, Text2CAD Paper, Text2CAD Project Page, Introduction to SOLIDWORKS Mates, What is BREP file format (CAD Exchanger). Fixed `[[Text2CAD paper]]` variant link in text2cad-neurips-page.md. All 33 previously broken wikilinks now resolve.

**Case mismatches (§2):** Fixed `[[Knot vector]]` → `[[Knot Vector]]` in nurbs.md; `[[NURBS Wikipedia]]` → `[[Non-uniform rational B-spline (Wikipedia)]]` in parametric-continuity.md; `[[design history]]` → `[[Design History]]` in text2cad-paper.md.

**Missing pages (§3):** Created 4 new stubs — ROMULUS (entity), Winged-Edge (concept), The NURBS Book (entity), C3D Labs (entity). Updated C3D entity to link to C3D Labs. Decided against stubs for LLaVA-NeXT, Mixtral-50B, Knot Insertion, Degree Elevation, T-Splines (low priority for current vault scope).

**De-bracketings (§3 low-value):** Removed brackets from: [[Renault]], [[Citroën]], [[NeurIPS 2024]], [[Extrusion]], [[Computer-Aided Design]], [[F-Rep]], [[Fabian Wildgrube]], [[Matthew Kusz]], [[Kevin Power]]. Fixed `[[Bézier]]` → `[[Bézier Curves|Bézier]]` (proper alias link to existing concept page).

**True orphan (§4):** Added body link to `[[Mates (Onshape)]]` from cad-mates.md. All source-page orphans auto-resolved by §1 fix.

**Index (§5):** Added 5 new entries (ROMULUS, C3D Labs, The NURBS Book, Winged-Edge) and verified all existing entries now resolve correctly.

**Clippings/raw discrepancy (§6):** Resolved — second ingest (today) processed the 8 missing raw files (Zoo.dev, CADFusion, ProCAD, B2B SaaS GTM).

Post-fix status: 0 broken wikilinks, 0 true orphans, frontmatter clean across all pages. Next lint recommended after 10 more ingests.

## [2026-04-18] ingest | Second batch — 8 raw sources (Zoo.dev, CADFusion, ProCAD, B2B SaaS GTM)
Ingested all previously unprocessed raw files. Created:

- 8 source summary pages: Zoo: Introducing Text-to-CAD, Zoo API: Generate a CAD Model from Text, Zoo: Zookeeper, CADFusion paper (arXiv 2501.19054), ProCAD paper (arXiv 2602.03045), A No-BS Guide to B2B SaaS GTM, B2B SaaS Sales for Technical Founders, Go To Market Strategy For B2B SaaS: A Simple Founder Playbook.
- 3 entity pages: Zoo.dev (commercial competitor), CADFusion (Microsoft Research framework), ProCAD (Georgia Tech two-agent system).
- 6 concept pages: KittyCAD Language (KCL), Go-to-Market Strategy, Ideal Customer Profile, SaaS Sales Cycle, SaaS Metrics, SaaS Pricing Strategy, Product-Market Fit.
- 1 synthesis page: Mirum GTM (draft GTM strategy applying ICP→Offer→Channels→Pricing→KPIs to Mirum).
- Updated: Text-to-CAD concept (added CADFusion, ProCAD, Zoo.dev to current state), Mirum entity (added commercial landscape, training techniques, GTM starting point).

All pages cross-linked. Index.md updated with 18 new entries. Total wiki: ~110 pages. Lint from previous pass still unresolved — source title drift (9 pages), 3 case mismatches, 6 missing concept pages. Recommend addressing lint before next ingest.

## [2026-04-18] lint | First lint pass — systemic source-title drift
Scanned 90 pages. Full report at `output/lint-2026-04-18.md`. Frontmatter clean across the vault. Dominant finding: 9 of 13 source pages have H1s that don't match how the index and body prose link them (e.g. H1 "Text2CAD: Generating Sequential CAD Models…" vs. `[[Text2CAD Paper]]` used in 5 pages), producing 33 unique broken link targets and 10 orphan source pages. Secondary issues: 3 case mismatches (`Knot vector`, `design history`, `NURBS Wikipedia`), 6 high-signal missing concept/entity pages (ROMULUS, The NURBS Book, Winged-Edge, C3D Labs, Knot Insertion, Degree Elevation), ~12 low-value references that should be de-bracketed, and 1 true orphan (`Mates (Onshape)`). Also flagged: ~7 of the 22 originally clipped articles (Text-to-CadQuery, CADFusion, ProCAD, Zoo.dev, B2B SaaS GTM) are missing from `raw/` and `Clippings/` is empty — pipeline gap to investigate before next ingest. No fixes applied yet; awaiting user go-ahead.

## [2026-04-19] synthesis | Converter-as-Annotator Inference
Cross-paper inference note bridging the 3 most recent raw sources (RLAIF for Code, Don't Mesh with Me, JoinABLe). Core heuristic: in CAD-ML the scarce resource is the deterministic converter that projects existing structured artifacts into training pairs, not the labels themselves. Includes logic chain (observation/abstraction/synthesis to Mirum), three failure states (converter bias, frontier-LLM pathology leakage, pairwise-to-global compounding), a cross-domain link to agent orchestration, a next-step protocol, and four if/then decision heuristics. File: `wiki/synthesis/converter-as-annotator-inference.md`. Index updated.

## [2026-04-19] audit | t2cIII Next-Step Protocol applied
Applied the Next-Step Protocol from [[Converter-as-Annotator Inference]] to `C:\Users\Kevin\Developments\T2CIII\data_pipeline\` and `training\rlaif_machinist.py`. Finding: geometry converters are deterministic (pass Check 1); description generation has no latent artifact (fails Check 2 — Gemini monoculture on the text side of every pipeline); no converter-variant ablations anywhere (fails Check 3). Tier-1 action: migrate description generation off Gemini onto STEP PRODUCT names / GrabCAD before next large SFT run. Full audit at `output/next-step-protocol-t2ciii-2026-04-19.md`.

## [2026-04-19] lint | Fidelity pass on Converter-as-Annotator Inference
Verified every wikilink in the new inference note resolves in index.md. Fixed one broken link (`[[Assembly-First Open Problem]]` → `[[Assembly-First: The Open Problem]]` — title drift). Aligned frontmatter with vault convention (added `name`, `description`, `type` fields matching other synthesis pages). Rechecked source claims against raw papers: RLAIF hybrid (exec + frontier critique) ✓, Don't Mesh 37K B-rep→CSG pairs / 1.3B model ✓, JoinABLe 79.53% pairwise vs. ~80% human ✓. Corrected quantitative error in Stress Test #3: original note computed 0.80^45 ≈ 0.001% assuming a 10-part assembly needs 45 pairwise predictions (fully-connected clique). Corrected to 0.7953^45 ≈ 0.003%, and — more importantly — added the real-assembly regime (10–15 joints, not 45 pairs) giving 3–10% global correctness. Point of the stress test stands in every regime; the math now matches reality. Preserved user-added Dependency section. No orphan wiki pages introduced. Index already updated.
