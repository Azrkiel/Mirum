# Mirum

**Deterministic Text-to-Assembly Orchestration for Precision Engineering**

Mirum converts a natural-language part description into a validated,
3D assembly as a mathematically exact B-Rep solid — not a polygon mesh —
typically in 30-90 seconds. It targets aerospace, defense, and
precision-mechanical applications where dimensional accuracy and topological
correctness are non-negotiable.

Output is a verified starting point for an engineer, not a drawing-released
part: read Limitations before relying on it.

---

## The Problem

Current LLMs fail at engineering CAD in three specific ways:

1. **Assembly hallucination** — models generate monolithic blobs; they cannot
   reason about internal structure (ribs, spars, bores, mating surfaces).
2. **Mathematical imprecision** — natural language is too vague for involute
   gear tooth geometry, NACA airfoil coordinates, or ISO thread profiles.
3. **No compilation feedback** — zero-shot generators cannot see that their
   output crashes the geometry kernel, so they never self-correct.

Mirum solves all three.

---

## The Solution: Chief Engineer Architecture

Mirum abandons zero-shot generation. Instead a hierarchical multi-agent
system acts as a Chief Engineer — planning, delegating, compiling, checking,
and assembling — with every step isolated and verifiable.

```
                        ┌─────────────────────────┐
  Natural language  ──► │  REA — Requirements     │ Gemini Flash Lite
                        │  Extraction Agent       │
                        └────────────┬────────────┘
                                     │ DesignRequirements
                        ┌────────────▼────────────┐
                        │  Planner                │ Gemini Flash
                        │  AssemblyManifest:      │
                        │  parts + mating rules   │
                        └────────────┬────────────┘
                                     │ (concurrent per part)
              ┌──────────────────────▼──────────────────────┐
              │                                             │
  ┌───────────▼──────────┐              ┌───────────────────▼──┐
  │ Domain Classifier    │ Flash Lite   │ ... part N            │
  │ A / B / C / D / E   │              └───────────────────────┘
  └───────────┬──────────┘
              │
  ┌───────────▼──────────┐
  │ Machinist            │ Gemini Flash
  │ CadQuery script      │
  └───────────┬──────────┘
              │
  ┌───────────▼──────────┐
  │ AST Security Scanner │ (allowlist: cadquery, cq, math, cad_utils)
  └───────────┬──────────┘
              │
  ┌───────────▼──────────┐
  │ Subprocess Sandbox   │ 30 s · 1.5 GB · SIGKILL process group
  │ (OCC geometry kernel)│
  └───────────┬──────────┘
              │ success / traceback
  ┌───────────▼──────────┐
  │ Critic Loop          │ Gemini Flash   (up to 3 retries)
  │ Domain C: G2 + Gauss │ OCC
  └───────────┬──────────┘
              │ validated code + .step
  ┌───────────▼──────────┐
  │ Geometry Certificate │ OCC  (6 structural checks + mass)
  └───────────┬──────────┘
              │
  ┌───────────▼──────────┐
  │ Assembler            │ deterministic, zero-LLM
  │ .step files →        │
  │ manifest → .glb      │
  └───────────┬──────────┘
              │
         SSE stream → glb · script · geometry_certificate · aero_report
```

---

## Five Geometric Domains

The domain classifier routes every part to one of five strategies before
any code is generated. The wrong strategy produces wrong geometry; the right
one produces exact geometry.

| Domain | Description | Key template functions |
|--------|-------------|----------------------|
| **A** Primitives / Structural | Boxes, plates, brackets, enclosures | `.box()`, `.cylinder()`, boolean ops |
| **B** Mechanical / Parametric | Gears, threads, cams, bearings | `make_involute_spur_gear`, `make_metric_thread`, ISO tables |
| **C** Organic / Ergonomic | Handles, shells, fairings, dragsters | `.loft()`, `.sweep()`, `.spline()` cross-sections |
| **D** Aerospace / Aerodynamics | Airfoils, wing segments, ribs, spars | `make_naca_airfoil` (80-pt cosine + OCC B-spline) |
| **E** Sheet Metal / Thin-Wall | Enclosure panels, PCB trays, bent brackets | `make_bent_bracket`, `make_enclosure_panel` |

Priority rules: **D overrides everything** when aerospace keywords appear.
**E overrides A** when gauge/bend keywords appear.

---

## Geometry Verification

Every compiled STEP file receives an OCC-based **Geometry Certificate** before
assembly. Six checks run automatically:

- Volume > 0 (catches degenerate solids)
- Surface area > 0 (catches empty shells)
- No degenerate faces (area > 10⁻⁶ mm²)
- Bounding box finite and < 10⁶ mm
- BRep topology valid (`BRepCheck_Analyzer`)
- Shape contains ≥ 1 solid sub-shape

The certificate is augmented with **estimated mass** from a 20-material
density table (aluminum through Inconel). Results appear in a collapsible
card in the frontend UI. A failing cert is advisory — it never blocks
delivery of the model, but it surfaces issues immediately.

---

## Aerodynamic Analysis (Domain D)

For Domain D parts, opt-in XFOIL polar analysis computes:

- Lift coefficient (Cl) vs. angle of attack
- Drag coefficient (Cd) vs. angle of attack
- Pitching moment coefficient (Cm) vs. angle of attack
- Lift-to-drag ratio (L/D)
- Stall prediction (dCl/dα sign-change + 5% Cl_max drop)

A reusable structural / flight-envelope module (`analysis/structures.py`)
computes ISA atmosphere, cruise CL, FAR-23 maneuvering + gust load
factors (limit & ultimate), 1-g stall speed, corner speed, V_NE, and
single-cell wing-box bending/shear margins against a small metallic
allowables table. Standalone today; pipeline-wired via opt-in `run_struct`.

Results stream back in the SSE `complete` event and render as interactive
charts in the frontend PerformanceCard.

---

## Security Sandbox

All LLM-generated code is scanned by a pure-AST allowlist scanner before
execution. The scanner runs in < 1 ms (pure Python, no subprocess).

**Only four modules may be imported**: `cadquery`, `cq`, `math`, `cad_utils`.
Every other import — including `os`, `sys`, `subprocess`, `importlib`,
`ctypes`, `socket`, `pickle` — is rejected at the AST level, not at runtime.

Bare use of dangerous names (e.g. `importlib.import_module(...)` without an
import statement) is caught via a secondary `ast.Name` node scan.

Approved code runs in a sandboxed subprocess: stdin delivery (no temp file),
30-second timeout, 1.5 GB memory cap (`RLIMIT_AS`), and `SIGKILL` sent to the
entire process group on timeout.

---

---

## Limitations

Known and deliberate, as of 2026-09. An engineer evaluating Mirum should read
this section first.

**Threads are cosmetic.** `make_metric_thread` and the bolt/nut helpers produce
a *simplified thread representation* (ISO 6410 / ASME Y14.6 sense): the major
diameter is correct to caliper, but the ribs are annular, not helical. Usable
for envelope, mass and clearance studies; not cuttable geometry and not a
mating thread form.

**No PMI / GD&T.** STEP output carries geometry only — no tolerances, datums,
surface finish or annotations. Tolerance callouts in a prompt are parsed but
never applied to geometry.

**No 2D drawings.** No dimensioned views, section views, title blocks or
drawing sheets.

**Millimetres only.** There is no imperial mode and no unit selector.

**Structural and propulsion reports are sizing-grade, computed from the
*stated* spec — not measured from the generated solid.** They use closed-form
beam idealisations, not FEA. A part whose modelled section differs from the
stated one can still show a positive margin. Aerodynamic polars (XFOIL/AVL)
*are* run on the generated geometry.

**Dimensional conformance is checked to 5% relative tolerance** against
requirements extracted from the prompt, and only against the bounding box.
Internal feature dimensions are checked for impossibility, not accuracy.

**No manufacturability checks** — no draft angle, minimum wall thickness,
undercut, tool-access or machinability analysis.

**Assembly scale is limited.** Concurrency is 3 parts at a time and the client
holds a single streaming request, so assemblies beyond roughly 15-20 unique
parts risk timing out. There is no job queue or resumability yet.

**Nothing persists.** There is no project history or part library; a browser
refresh clears the session. Generated files remain downloadable for 6 hours.

**Domain coverage is aerospace-weighted.** Strong: airfoils/wings (real NACA
maths and XFOIL), nose cones, nozzles, involute gears, ISO hex fasteners.
Absent: weldments, sheet-metal flat patterns and bend tables, injection-mould
draft, O-ring grooves, piping, structural extrusions, fastener libraries beyond
M3-M20 hex.


## Quick Start

```bash
# Prerequisites: Docker + Docker Compose + a Gemini API key

# REQUIRED. The backend raises at import without it.
export GEMINI_API_KEY_HEAVY="your_key"

# OPTIONAL. Planner/REA (Pro tier). Falls back to the Heavy key when unset —
# set it only if your Pro quota lives on a separate key.
# export GEMINI_API_KEY_PRO="your_pro_key"

cp .env.example .env    # then fill in the values above plus MIRUM_API_KEY
docker compose up --build
# → http://localhost:8000   (API)
# → http://localhost:8501   (Streamlit frontend)
```

Generate a part:
```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "NACA 2412 wing rib, 200mm chord, 3 bolt holes, 1.5mm 6061 aluminium"}' \
  --no-buffer
# Streams SSE events: planner → machinist:rib → export → complete{glb, cert, aero_report}
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM (pro)   | `gemini-3.1-pro-preview` via `google-generativeai` (Planner, REA) |
| LLM (heavy) | `gemini-3.5-flash` (Machinist, Critic, Refiner, eval) |
| LLM (lite)  | `gemini-3.5-flash` (classifier, PMI, clarifier — same key as Heavy) |
| Geometry kernel | CadQuery 2.x / OpenCASCADE Technology (OCCT) |
| Aerodynamics | XFOIL (wrapped via `analysis.airfoil`) |
| Backend | FastAPI + Uvicorn, SSE streaming |
| Frontend | Streamlit + Babylon.js (WebGPU/WebGL 3D viewer) |
| Security | AST allowlist + subprocess sandbox (`RLIMIT_AS`) |
| Retrieval | FAISS few-shot index (description → code) |
| Environment | Miniconda3 / conda-forge (OCC requires native conda build) |

---

## Repository Layout

```
backend/
  agents.py          — subagent orchestrator (Planner, Machinist, Assembler, …)
  classifier.py      — NLP domain classifier (A/B/C/D/E)
  compiler.py        — AST scanner + subprocess sandbox
  cad_utils.py       — deterministic geometry functions + ISO tables
  schemas.py         — Pydantic models (AssemblyManifest, DesignRequirements, …)
  strategies.py      — per-domain CadQuery strategy templates
  rendering.py       — zebra render pipeline (pyrender + OSMesa fallback)
  continuity_check.py — G2 continuity checks for Domain C
  geometry_analysis.py — Gaussian curvature checks for Domain C
  verification/
    geometry.py      — GeometryCertificate (6 OCC checks)
    material.py      — density table + mass_properties()
  subagents/
    base.py          — SubagentBase ABC + TelemetryMixin
    planner.py / machinist.py / critic.py / assembler.py
    rea.py / clarifier.py / aero.py / orchestrator.py
  tests/
    test_classifier.py         — 28 tests, all 5 domains + priority rules
    test_compiler_security.py  — 31 tests, 15 attack patterns blocked
    test_geometry_verification.py — 18 tests (OCC-gated), TypedDict checks
    test_subagents_base.py     — 23 tests, pure stdlib
    test_vlm_stubs.py          — 22 tests, VLM zebra rendering stubs
analysis/                — standalone analysis modules (no pipeline imports)
  airfoil.py             — STEP slicer + XFOIL runner + stall predictor
  structures.py          — ISA atmosphere + load-factor envelope + wing-box margins
  plots.py / pdf_report.py
  tests/
    test_airfoil.py      — 2-D airfoil + XFOIL contract (XFOIL-gated B-group)
    test_structures.py   — 27 unit tests, stdlib only (FAR-23 envelope, margins)
frontend/
  app.py             — Streamlit UI: 3D viewer, geometry cert card, aero charts
architecture.md      — full system architecture (this generation's source of truth)
DIRECTIVES.md        — 6 operational directives with bad/good code examples
```

---

## Key Differentiators

**Deterministic assembly** — The assembler is zero-LLM. It reads pre-compiled
`.step` files produced by each Machinist and applies manifest-specified
topological translations. Coordinate hallucination is structurally impossible.

**Domain-first routing** — The five-domain classifier prevents the Machinist
from applying organic-surface strategies to structural parts (and vice versa),
eliminating an entire class of generation failures before they occur.

**Compile-and-verify loop** — Every part is compiled against the live OCC
kernel, then verified: geometry certificate (volume, topology, degenerate
faces), a dimension check against the extracted requirements at 5% tolerance,
and Domain C surface-continuity gates. Any failed check consumes a retry and
feeds measured feedback plus the previous script back to the Machinist.

Measured on the internal 82-prompt harness (`output/qa-reports/`): **95% of
prompts complete within 3 retries** (78/82), median 27s, p95 98s for the
part-generation stage. "Complete" means the script compiled and produced a
valid solid — not that the geometry matches intent. Planner and REA latency is
additional.

**B-Rep output** — Every run produces STEP (ISO 10303-21) alongside STL and
GLB, downloadable from the UI. STEP carries exact B-Rep geometry and imports
into SolidWorks, NX, Onshape, Fusion 360 and CAM packages. Assembly STEP
preserves per-part names.

STEP files do **not** currently carry PMI / GD&T annotations — see Limitations.
