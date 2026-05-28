# Mirum Architecture

Text-to-CAD pipeline that converts natural-language part descriptions into
validated, exportable 3D geometry (GLB + STEP) via a hierarchical
multi-agent system built on Gemini and CadQuery/OpenCASCADE.

---

## 1. System Overview

```
User prompt
    │
    ├─► REA (Requirements Extraction Agent)   Gemini Flash Lite
    │   └─ DesignRequirements: dims, material, domain, flight_env
    │
    ├─► Planner                               Gemini Flash
    │   └─ AssemblyManifest: parts list + mating rules (MateType×10)
    │
    │   ┌── per part, concurrent ──────────────────────────────────┐
    ├─► │ Domain Classifier                   Gemini Flash Lite    │
    │   │ └─ A / B / C / D / E                                    │
    │   │                                                          │
    │   │ Machinist + Critic Loop             Gemini Flash         │
    │   │ ├─ CadQuery code → AST scan → sandbox subprocess        │
    │   │ ├─ On fail: traceback → retry (max 3)                   │
    │   │ ├─ Domain C: G2 continuity + Gaussian curvature         │
    │   │ └─ Domain D: optional XFOIL polar post-compile          │
    │   │                                                          │
    │   │ Geometry Certificate               OCC (post-compile)   │
    │   │ └─ 6 structural checks + material mass                  │
    │   └──────────────────────────────────────────────────────────┘
    │
    ├─► Assembler (deterministic, zero-LLM)   CadQuery
    │   └─ Loads .step files → applies manifest translations
    │
    └─► SSE "complete" event
        ├─ glb (base64)
        ├─ script (CadQuery source)
        ├─ geometry_certificate
        ├─ aero_report (Domain D, opt-in)
        └─ parameters, export_formats, pmi_detected
```

---

## 2. Five Geometric Domains

The NLP classifier routes each part to a domain before the Machinist runs.
Domain selection controls: which CadQuery strategy template is applied, which
post-compile checks fire, and which retry hints are injected on failure.

### Domain A — Primitives / Structural

Box extrusions, boolean operations, holes, fillets, chamfers. Used for base
plates, L-brackets, enclosures, standoffs, spacers. Key signals: described
by length × width × height and hole patterns.

**Strategy**: single-workplane sketch → `.extrude()` → boolean refinement.

### Domain B — Mechanical / Parametric

Parts requiring mathematical curves: involute spur/helical gears, lead
screws, cams, bearings, threads, sprockets. Key signals: teeth, module,
pressure angle, pitch, helix angle, bore diameter.

**Strategy**: parametric formulas in `cad_utils.py` (`make_involute_spur_gear`,
`make_metric_thread`, `make_hex_bolt`, etc.) + ISO dimension tables (M3–M20).

### Domain C — Organic / Ergonomic

Smooth swept/lofted surfaces: ergonomic handles, mouse shells, bottle bodies,
CO2 dragsters, derby cars, fairing bodies. Key signals: "smooth", "ergonomic",
"organic", cross-sections that vary along an axis.

**Strategy**: loft or sweep along a spine with `.spline()` / `.ellipse()`
cross-sections. `.polyline()` is forbidden.

**Post-compile checks** (in Critic Loop, before STEP save):
1. G2 continuity — `continuity_check.check_g2_continuity()` via OCC BSpline analyser. Fail → VLM zebra feedback → retry.
2. Gaussian curvature — `geometry_analysis.curvature_check_for_critic()`. Saddle-point density > threshold → retry.

### Domain D — Aerospace / Aerodynamics

Airfoil sections (NACA 4-digit), wing segments with ribs and spars, control
surfaces, stabilizer fins, turbine blade blanks. Key signals: "NACA", "airfoil",
"wing", "camber", "chord", "span", "rib", "spar", or any NACA designation
(e.g. `2412`, `0012`, `4415`).

**Priority**: Domain D overrides all other domains when aerospace keywords
are present. "Wing rib" → D (not A). "Airfoil fairing" → D (not C).
"Turbine blade blank" → D (not B).

**Strategy**: `cad_utils.make_naca_airfoil` (80-pt cosine spacing, OCC B-spline
fit) + `make_co2_void` for internal structure.

**Post-compile (opt-in)**: XFOIL polar analysis via `analysis.airfoil`
(Cl, Cd vs. AoA). Requires `run_aero=True` flag. Results in `aero_report`
(PolarReport) and frontend PerformanceCard.

### Domain E — Sheet Metal / Thin-Wall

Parts from thin flat stock: enclosure panels, PCB trays, bent-plate brackets,
chassis panels, air-duct flanges. Key signals: sheet metal, gauge (0.8–3.0 mm),
bend radius, flange depth, stamped.

**Priority**: Domain E overrides Domain A when gauge/bend keywords appear.
"Sheet metal bracket" → E (not A).

**Strategy**: `cad_utils.make_bent_bracket` / `make_enclosure_panel` with
`material_thickness` parameter.

---

## 3. Subagent System

`backend/subagents/` is a typed multi-agent framework on `SubagentBase` (ABC).

Each subagent provides:
- `agent_type` property — string identifier used in telemetry
- `async _run(ctx: SubagentContext) → SubagentMessage` — domain logic
- Public `run()` wrapper — catches all exceptions, never raises, captures timing
- `TelemetryMixin._emit_span()` — writes `SubagentSpan` JSONL to `/tmp/mirum/telemetry/subagent_spans.jsonl`
- `ModelRegistry` integration — per-agent `ModelConfig` (model_id, temperature, max_tokens, lite_fallback)

Runtime model configuration: `POST /admin/model-config` (bearer-gated).

| Subagent | Model tier | Role |
|---|---|---|
| `PlannerSubagent` | HEAVY | Wraps `run_planner` |
| `MachinistSubagent` | HEAVY | Wraps `run_machinist` |
| `CriticSubagent` | HEAVY | Wraps `run_critic_loop` |
| `AssemblerSubagent` | `""` deterministic | Wraps `run_assembler` |
| `REASubagent` | LITE | Wraps `run_rea` |
| `ClarifierSubagent` | LITE | Wraps `run_clarifier` |
| `AeroSubagent` | `""` | Wraps `_run_aero_stage` (Domain D only) |
| `OrchestratorSubagent` | — | Top-level coordinator |

Model strings (from `config.py`):
- `GEMINI_MODEL_PRO   = "gemini-3.1-pro-preview"`  (Planner, REA)
- `GEMINI_MODEL_HEAVY = "gemini-3.5-flash"`        (Machinist, Critic, Refiner, eval)
- `GEMINI_MODEL_LITE  = "gemini-3.5-flash"`        (classifier, PMI, clarifier — same key as Heavy; the previous `gemini-3.1-flash-lite-preview` was retired by Google in May 2026)

---

## 4. AST Security Scanner

`compiler._check_ast_security()` gates all LLM-generated code before subprocess
execution. Uses an allowlist architecture — anything not explicitly permitted is
rejected.

**Import allowlist**: `{"cadquery", "cq", "math", "cad_utils"}` — all other
modules rejected on `import` / `from … import`.

**Banned builtin calls**: `eval`, `exec`, `open`, `compile`, `__import__`,
`getattr`, `setattr`, `delattr`, `globals`, `locals`, `vars`, `breakpoint`,
`input`, `memoryview`, `type`.

**Banned dunder attributes**: `__import__`, `__subclasses__`, `__globals__`,
`__builtins__`, `__loader__`, `__spec__`, `__code__`.

**Banned bare names** (importlib gap fix — added 2026-05-15): `importlib`,
`ctypes`, `subprocess`, `socket`, `shutil`, `pickle`, `pathlib`, `tempfile`,
`pty`, `fcntl`, `mmap`, `multiprocessing`, `threading`. These are caught as
`ast.Name` nodes even without an import statement, preventing pre-loaded
reference exploitation.

**Subprocess sandbox**:
- Code delivered via stdin (`python -`) — no temp file, no AV-scanner window
- 30 s timeout (`CADQUERY_TIMEOUT_S`)
- 1.5 GB `RLIMIT_AS` memory cap
- `SIGKILL` to process group on timeout (`os.killpg` + `os.setpgrp`)
- Writable `cwd` = `/tmp/mirum/work` (tmpfs) so OCC temp files don't hit the read-only `/app` mount

---

## 5. Geometry Certificate

Every compiled STEP file (all domains) receives a `GeometryCertificate` from
`backend/verification/geometry.py`. Six OCC checks:

| Check | Condition |
|---|---|
| `volume_positive` | solid volume > 0 mm³ |
| `surface_area_positive` | surface area > 0 mm² |
| `no_degenerate_faces` | every face area > 1×10⁻⁶ mm² |
| `bounding_box_finite` | extents finite and < 10⁶ mm |
| `brep_valid` | `BRepCheck_Analyzer` reports no errors |
| `is_solid` | shape contains ≥ 1 solid sub-shape |

The certificate is augmented with **material mass** via
`backend/verification/material.py`: 20-entry density table
(`resolve_material(material_class)` → density kg/m³ → `mass_properties()` → mass_g).

Delivered in SSE `complete` event as `geometry_certificate`. Rendered in
the frontend as a collapsible card (`_render_geometry_cert_card`).
Certificate is advisory — a failing cert never blocks GLB delivery.

---

## 6. VLM Zebra Rendering

`backend/rendering.py` provides shared `render_zebra()` used by Domain C
post-compile checks:

- `continuity_check.check_g2_vlm_zebra()` — G2 continuity feedback
- `geometry_analysis.check_curvature_via_vlm()` — Gaussian curvature scoring

**Pipeline**:
1. STEP → OCC `STEPControl_Reader` + `BRepMesh_IncrementalMesh` → STL
2. STL → `trimesh` → `pyrender` scene, normal-encoded vertex colours
3. Rendered image → post-process: green channel (normal-Y) → zebra stripes
   `stripe_idx = int(normal_y * n_stripes) % 2`
4. PNG bytes → Gemini Vision API → parsed JSON verdict

**Graceful degradation** (6 levels): pyrender+OSMesa → numpy synthetic stripes
→ stdlib minimal PNG. `MIRUM_NO_PYRENDER=1` forces synthetic path in CI/dev.

---

## 7. Gemini Prompt Rules

Six invariants applied on every Gemini call:

1. **Context isolation** — each subagent receives only the context it needs; no cross-agent contamination.
2. **System-first** — system prompt establishes domain constraints before any user content.
3. **JSON schema mode** — `response_mime_type: "application/json"` + `response_schema` wherever structured output is needed.
4. **Traceback-injected retries** — retry prompt includes full traceback + active strategy rules from `get_active_rules()`.
5. **Model tier separation** — HEAVY for code generation; LITE for classification and REA.
6. **Conservative temperature** — defaults ≤ 0.5 for code-generation agents; overridable via `ModelRegistry`.

---

## 8. OCC Crash Prevention Directives

Five rules enforced in all CadQuery generation strategies:

1. **Loft chaining** — all loft cross-sections must chain on a single `cq.Workplane` stack; never assign intermediate cross-sections to separate variables.
2. **Shelling** — use `.shell(-thickness)` on a selected face; never boolean-subtract a scaled copy ("Russian doll" pattern produces non-manifold BRep).
3. **Fillet radius** — `radius < (thinnest_edge_length / 2)`; validate before calling `makeFillet()` / `chamfer()`.
4. **Mesh before STEP** — call `BRepMesh_IncrementalMesh` before `STEPControl_Writer` when the shape has not been tessellated.
5. **Explorer safety** — never modify the shape being walked by `TopExp_Explorer`; collect results first, process after loop.

---

## 9. Retrieval-Augmented Generation

`backend/retrieval.py` maintains a FAISS-backed index of successful
(description, code) pairs:

- `retrieve_few_shots(description)` — injects similar examples as few-shot demonstrations into the Machinist prompt
- `retrieve_for_failure(traceback, code)` — injects failure-specific examples on retry
- `add_to_index(description, code, domain)` — called on every successful compilation
- `extract_techniques(code)` — extracts method signatures for retry context

---

## 10. RLAIF Scoring

`backend/evaluation.py` implements async LVM scoring (fire-and-forget via
`asyncio.create_task`, non-blocking):

- `RLAIF_EXEC_WEIGHT = 0.7` — execution success
- `RLAIF_PREF_WEIGHT = 0.3` — Gemini-graded design quality

Scores logged for offline RLAIF fine-tuning of the Machinist.

---

## 11. API Surface

| Endpoint | Method | Description |
|---|---|---|
| `/generate` | POST | Full SSE pipeline (main generation flow) |
| `/plan` | POST | Planner only — preview AssemblyManifest |
| `/run` | POST | Execute CadQuery script directly → GLB |
| `/refine` | POST | Multi-turn conversational refinement |
| `/clarify` | POST | Pre-planner clarifier questions |
| `/requirements` | POST | REA extraction only |
| `/pmi/extract` | POST | Extract PMI annotations from STEP |
| `/pmi/annotate` | POST | Annotate STEP with PMI callouts |
| `/export/parasolid` | POST | Export compiled STEP as Parasolid .x_t |
| `/analysis/plots` | POST | XFOIL polar plots as base64 PNGs |
| `/stats` | GET | Failure-rate statistics by domain + operation |
| `/admin/model-config` | POST | Update per-agent model configuration (admin) |
| `/admin/model-config` | GET | Read current ModelRegistry snapshot (admin) |
