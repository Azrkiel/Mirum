# Mirum

Mirum turns a text description of a mechanical part into a 3D CAD model.
You type something like "NACA 2412 wing rib, 200 mm chord, 3 bolt holes",
and it returns a STEP file, a GLB file for viewing, and the CadQuery script
that produced them.

It is a research project, pre-1.0. See [What doesn't work yet](#what-doesnt-work-yet)
before relying on the output.

## What it does

- Reads a prompt and pulls out dimensions, material, and part type.
- Splits the request into parts and the rules for how they fit together.
- Asks an LLM (Gemini) to write a CadQuery script for each part.
- Runs each script against the OpenCASCADE geometry kernel. If the script
  crashes, the error goes back to the LLM for another attempt, up to 3 retries.
- Runs basic validity checks on the resulting solid.
- Positions the parts into one assembly and exports it.

The output is B-Rep geometry (exact surfaces), not a triangle mesh. The GLB
is only for the browser viewer.

## How it works

A request to `/generate` goes through these steps, and progress is streamed
back as server-sent events:

1. **Requirements extraction.** An LLM call turns the prompt into a
   structured `DesignRequirements` object.
2. **Planner.** An LLM call produces an `AssemblyManifest`: a list of parts
   and mating rules.
3. **Domain classifier.** Each part is assigned one of seven categories (see
   below). The category decides which instructions and helper functions the
   code-writing step gets.
4. **Machinist.** An LLM call writes a CadQuery script for the part. Similar
   past scripts that compiled are added to the prompt as examples (TF-IDF
   lookup over telemetry).
5. **Security scan.** The script is parsed and rejected if it imports
   anything outside `cadquery`, `cq`, `math`, `cad_utils`, `cad_aero`, or
   uses banned builtins such as `eval`, `exec`, `open`, `getattr`.
6. **Sandboxed run.** The script runs in a subprocess with a 30 second
   timeout and a 1.5 GB memory cap. The script is passed on stdin and the
   API keys are not passed to the subprocess.
7. **Critic loop.** On failure, the traceback is sent back to the Machinist.
   Domain C parts also get surface continuity and curvature checks.
8. **Geometry certificate.** Six checks on the compiled solid: volume > 0,
   surface area > 0, no degenerate faces, finite bounding box, valid B-Rep
   topology, at least one solid. A mass estimate is added from a material
   density table.
9. **Assembler.** Plain code, no LLM. It loads the STEP files and applies
   the transforms from the manifest.

Steps 3 to 8 run concurrently per part.

### Part categories

| Domain | Covers | Approach |
|--------|--------|----------|
| A | Plates, brackets, boxes, enclosures | Extrusions and boolean operations |
| B | Gears, threads, cams, bearings | Parametric formulas and ISO tables in `cad_utils.py` |
| C | Handles, shells, bottles, other smooth shapes | Lofts and sweeps over spline cross-sections |
| D | Airfoils, wing sections, ribs, spars | NACA 4-digit profile generation, wing helpers in `cad_aero.py` |
| E | Sheet metal panels, trays, bent brackets | Thin-wall helpers with a material thickness parameter |
| F | Nozzles, combustion chambers, nacelle cowls | Revolved profiles |
| G | Nose cones, fuselages, fairings | Revolved and multi-station lofted bodies |

When categories overlap, D wins over everything, F wins over B, G wins over
C, and E wins over A.

### Optional analysis

These are off by default and set per request:

- `run_aero`: runs XFOIL on Domain D parts and returns lift, drag, and
  moment coefficients against angle of attack, plus a stall estimate. This
  is 2D section analysis.
- `run_struct`: computes ISA atmosphere values, FAR-23 maneuver and gust
  load factors, stall and corner speeds, and single-cell wing-box bending
  and shear margins.

Results can also be returned as plots (`/analysis/plots`) or a PDF
(`/analysis/pdf`).

## Running it

You need Docker, Docker Compose, and a Gemini API key.

Create a `.env` file in the repository root:

```
GEMINI_API_KEY_HEAVY=your_key
```

`GEMINI_API_KEY_PRO` is optional. Set it only if your Pro-model quota is on
a different key. `ADMIN_API_KEY` is needed only for the `/admin` endpoints.

Then:

```bash
docker compose up --build
```

- API: http://localhost:8000
- Web UI: http://localhost:8501

Example request:

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "NACA 2412 wing rib, 200mm chord, 3 bolt holes, 1.5mm 6061 aluminium"}' \
  --no-buffer
```

The response is an event stream. The final `complete` event contains the
GLB (base64), the CadQuery script, and the geometry certificate.

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/generate` | POST | Full pipeline, streamed |
| `/engineering/generate` | POST | Deterministic typed cantilever CAD + measured STEP checks + bounded beam FEA; no LLM (API only) |
| `/plan` | POST | Planner only, returns the manifest |
| `/requirements` | POST | Requirements extraction only |
| `/clarify` | POST | Returns clarifying questions for a vague prompt |
| `/run` | POST | Runs a CadQuery script you supply and returns a GLB |
| `/refine` | POST | Edits an existing result through follow-up prompts |
| `/pmi/extract`, `/pmi/annotate` | POST | Read or add tolerance annotations on a STEP file |
| `/export/parasolid` | POST | Parasolid export (see limitations) |
| `/analysis/plots`, `/analysis/pdf` | POST | XFOIL results as plots or a PDF |
| `/stats` | GET | Failure rates by domain and operation |
| `/admin/model-config` | GET, POST | Read or change which model each agent uses |

## What doesn't work yet

The new **[deterministic engineering API](docs/implementation/deterministic-physics.md)**
adds typed cantilever generation, independent STEP measurements, beam finite-element
checks and bounded solver-feedback sizing without model calls. It is API-only and
limited to a slender rectangular cantilever under a transverse end force. It is not
3D solid FEA, a PINN, or engineering standards certification; unsupported requests
must not be presented as verified.

- **Not every prompt produces a part.** In the last recorded full run of the
  evaluation set (2026-05-26, `output/qa-reports/2026-05-26-full-litesub.md`),
  78 of 82 prompts passed. The 4 failures were three Domain C parts
  (two "Nothing to loft" errors and one OpenCASCADE `StdFail_NotDone`) and
  one Domain E part (the generated script used a wrong attribute name).
- **Smooth organic shapes are the weakest area.** Lofts and sweeps fail more
  often than anything else, and a part can compile and still not look like
  what was asked for.
- **Speed varies.** In that same run, simple parts took about 10 to 40
  seconds. Harder parts took one to two minutes, and the slowest took over
  four.
- **Generation requires a passing geometry certificate.** `/generate` fails
  closed if a required solid check fails or cannot run. Passing means the solid
  is well-formed, not that it matches the prompt, has correct assembly mates,
  or can be manufactured. Legacy `/run` and `/refine` still have narrower
  validation; they are not equivalent to the hardened Text-2-CAD acceptance pipeline.
- **Parasolid export is not active.** It needs a commercial CAD Exchanger
  license. Without one the endpoint falls back to STEP.
- **Model training has not been run.** The scripts in `training/`,
  `inference/`, and `data_pipeline/` (fine-tuning, RLAIF, the mate-prediction
  GNN, dataset converters) are scaffolds. They are waiting on a GPU and on
  dataset access. The live pipeline uses stock Gemini models.
- **The memory cap does not apply on Windows.** It relies on `RLIMIT_AS`,
  which is Linux-only. Run it in Docker.
- **The sandbox is a filter, not a guarantee.** It is an allowlist over
  LLM-written Python plus a resource-limited subprocess. Escapes have been
  found and fixed before. Do not expose the API to untrusted users without
  further isolation.
- **Offline CI includes native CAD tests.** In addition to lightweight schema
  and mocked analysis gates, `Dockerfile.verify` runs the native geometry,
  process-lifecycle and API tests. Model responses in these tests are fixtures;
  no live LLM generation quality or provider authentication is established.
- **Aerodynamic and structural numbers are estimates.** XFOIL is a 2D
  panel method and the structural module uses a simplified single-cell
  wing-box. Neither has been validated against test data here.
- **No tagged releases.** Everything ships from `main`.

## Verification

Targeted hardening ports and their scope are described in
[the integration notes](docs/implementation/text2cad-hardening-ports.md).

```bash
docker build -f Dockerfile.verify -t mirum-integration-verify .
docker run --rm --network none --read-only --init \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 4g --cpus 2 --tmpfs /tmp:rw,size=1g,mode=1777 \
  -e GEMINI_API_KEY_HEAVY=offline-fixture-never-sent \
  -e GEMINI_API_KEY= -e GOOGLE_API_KEY= mirum-integration-verify
```

In Windows Git Bash, prefix the `docker run` command with
`MSYS_NO_PATHCONV=1`. The image uses CadQuery/OCP on Linux; native Windows
DLL execution is not required. Paid-provider tests are excluded by default.

## Tech stack

| Layer | Technology |
|-------|-----------|
| LLM | Gemini via `google-generativeai`: `gemini-3.1-pro-preview` for the Planner and requirements extraction, `gemini-3.5-flash` for everything else |
| Geometry | CadQuery 2.x on OpenCASCADE |
| Aerodynamics | XFOIL |
| Backend | FastAPI, Uvicorn, server-sent events |
| Frontend | Streamlit with a Babylon.js viewer |
| Environment | Conda (conda-forge), because OpenCASCADE needs a native build |

Model names are set in `backend/config.py`.

## Repository layout

```
backend/
  main.py              FastAPI endpoints and the streaming pipeline
  agents.py            Planner, Machinist, Critic, Refiner, Assembler
  classifier.py        Domain classifier (A to G)
  compiler.py          AST scanner and subprocess sandbox
  cad_utils.py         Geometry helpers and ISO tables (available to generated scripts)
  cad_aero.py          Wing and airfoil helpers (available to generated scripts)
  strategies.py        Per-domain instructions for the Machinist
  schemas.py           Pydantic models
  retrieval.py         Example lookup for few-shot prompts
  continuity_check.py  Surface continuity checks for Domain C
  geometry_analysis.py Curvature checks for Domain C
  rendering.py         Zebra-stripe renders used by the Domain C checks
  exporters.py         Export formats
  verification/        Geometry certificate and material mass
  subagents/           Agent wrappers, telemetry, model registry
  propulsion/          Domain F code
  eval_harness/        Evaluation prompts and runner
  tests/
analysis/              Standalone analysis: airfoil (XFOIL), structures,
                       AVL, lifting line, plots, PDF report
frontend/
  app.py               Streamlit UI
data_pipeline/         Dataset preparation scripts (scaffold)
training/              Fine-tuning scripts (scaffold)
inference/             Constrained decoding and GNN models (scaffold)
docs/                  Design notes
output/                Plans, QA reports, demo assets
architecture.md        More detail on each pipeline stage
SECURITY.md            Security policy and how to report an issue
```

## License

See `LICENSE`.
