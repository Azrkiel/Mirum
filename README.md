# Mirum

**Deterministic Text-to-Assembly Orchestration for Precision Engineering**

Mirum converts a natural-language part description into a validated,
manufacturing-ready 3D assembly — a mathematically exact B-Rep solid,
not a polygon mesh — in under 60 seconds. It targets aerospace, defense,
and precision-mechanical applications where dimensional accuracy and
topological correctness are non-negotiable.

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
- Pressure distribution (Cp)
- Stall prediction (ΔCl/Δα threshold)

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

## Quick Start

```bash
# Prerequisites: Docker + Docker Compose + two Gemini API keys

export GEMINI_API_KEY_HEAVY="your_heavy_tier_key"
export GEMINI_API_KEY_LITE="your_lite_tier_key"

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
| LLM (heavy) | `gemini-3-flash-preview` via `google-generativeai` |
| LLM (lite) | `gemini-3.1-flash-lite-preview` |
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
  analysis/
    airfoil.py       — STEP slicer + XFOIL runner + stall predictor
  tests/
    test_classifier.py         — 28 tests, all 5 domains + priority rules
    test_compiler_security.py  — 31 tests, 15 attack patterns blocked
    test_geometry_verification.py — 18 tests (OCC-gated), TypedDict checks
    test_subagents_base.py     — 23 tests, pure stdlib
    test_vlm_stubs.py          — 22 tests, VLM zebra rendering stubs
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
kernel. Failures return tracebacks to the Machinist for self-correction (up to
3 retries). The loop has produced > 90% first-attempt compile success rates on
the internal QA harness.

**Manufacturing-grade output** — Output is STEP (ISO 10303-21), not STL or
GLB alone. STEP files carry exact B-Rep geometry, tolerance annotations (PMI),
and can be imported directly into CAM software (Fusion 360, Mastercam, CATIA).
