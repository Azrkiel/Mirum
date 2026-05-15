# Mirum Implementation To-Do

Comprehensive, developer-ready task list derived from the [[Mirum Capability Gaps]] audit and [[Mirum Technical Architecture]] synthesis. Every task is written so a developer can pick it up and execute without re-reading the research.

**Source documents:** `wiki/synthesis/mirum-capability-gaps.md`, `wiki/synthesis/mirum-technical-architecture.md`  
**Created:** 2026-04-18

---

## How to read this document

Tasks are grouped into four tiers:

- **Tier 1 — Days (1–5 days each):** High-impact changes requiring no new infrastructure, no new models, no external data. Pick up and ship.
- **Tier 2 — Weeks (1–6 weeks each):** Significant new features, data pipelines, or fine-tuning work. Clear ROI, moderate complexity.
- **Tier 3 — Research Bets (months):** Deep ML research, new model training, or architecturally risky work. High upside, high investment.
- **Tier 4 — Subagent Architecture (weeks, parallel-trackable):** Break the monolithic `agents.py` into a proper subagent system. Each agent runs independently, communicates via structured messages, and can be swapped or parallelized. Tasks use the **Token Efficiency Protocol** (XML task format, telegraphic fragments, status symbols). Status symbols: ⬜ pending · 🔄 in progress · ✅ done · ⚠️ blocked.

Within each tier, tasks are ordered by dependency — tasks that unblock others come first.

**t2cIII key files:**
- `agents.py` — Planner, Machinist agents (LLM calls, structured output)
- `classifier.py` — Domain A/B/C/D classifier
- `compiler.py` / `critic.py` — Critic Loop: execute CadQuery, capture tracebacks
- `main.py` — FastAPI endpoints (`/generate`, `/run`), pipeline orchestration
- `schemas.py` — Pydantic models: `AssemblyManifest`, `MatingRule`, `Part`
- `strategies.py` — Domain-specific system prompts injected into Machinist
- `cad_utils.py` — Deterministic geometry library (NACA airfoils, involute gears, etc.)
- `telemetry.py` — JSONL rotating log at `/tmp/mirum/telemetry/critic_loop.jsonl`
- `debug.py` — Unsanitized error log at `/tmp/mirum_debug.jsonl`
- `frontend/app.py` — Streamlit UI (GLB viewer, code editor, SSE streaming)
- `docker-compose.yml` — Backend (FastAPI :8000) + Frontend (Streamlit :8501)

---

## Tier 1 — Days

### ✅ T1-01: Constraint-Based Assembly Model (v1 Migration)

**STATUS: IMPLEMENTED**

**What it is:** Replace the current `MatingRule.translation` absolute-coordinate positioning with typed kinematic constraints using CadQuery's `cq.Constraint` API and `assembly.solve()`. This is the single most important architectural change — it removes the requirement for the Planner LLM to compute 3D spatial coordinates and replaces it with the much more LLM-tractable task of classifying connection types.

**CHANGES MADE:**
- Added `MateType` enum to `schemas.py` with 6 constraint types (FASTENED, REVOLUTE, SLIDER, CYLINDRICAL, PLANAR, BALL)
- Made `MatingRule.translation` optional and added required `mate_type` field with model validator
- Implemented `run_assembler_constraint()` in `agents.py` with MATE_CONSTRAINTS mapping
- Updated Planner system prompt to describe constraint-based assembly workflow
- Function selects constraint assembler when `mate_type` is provided, falls back to translation-based

**Why it matters:** The current system fails for complex assemblies because LLMs don't reliably do 3D arithmetic. Constraint-based assembly offloads positioning to the OCCT constraint solver, which is deterministic and correct. Every other assembly improvement in this list depends on this migration.

**Implementation steps:**

1. Open `schemas.py`. In `MatingRule`, add a `mate_type` field:
   ```python
   from enum import Enum
   class MateType(str, Enum):
       FASTENED = "FASTENED"
       REVOLUTE = "REVOLUTE"
       SLIDER = "SLIDER"
       CYLINDRICAL = "CYLINDRICAL"
       PLANAR = "PLANAR"
       BALL = "BALL"

   class MatingRule(BaseModel):
       part_a: str
       part_b: str
       mate_type: MateType
       anchor_a: str  # CadQuery selector string, e.g. "faces@>Z"
       anchor_b: str  # CadQuery selector string, e.g. "faces@<Z"
       # Keep translation as optional fallback during transition
       translation: Optional[str] = None
   ```

2. Open `agents.py`. Update the Planner's system prompt to describe the new `MatingRule` schema. Remove any instruction to compute XYZ translations. Add: *"For each mating rule, specify the mate_type (FASTENED / REVOLUTE / SLIDER / CYLINDRICAL / PLANAR / BALL) and the CadQuery face/edge selector strings for the anchor faces on each part. Do not specify absolute coordinates — the constraint solver will compute positions."*

3. Open the Deterministic Assembler (in `main.py` or a separate `assembler.py`). Replace the `cq.Location(cq.Vector(X,Y,Z))` path with constraint generation. Write a helper `mate_type_to_constraints(mate_type) -> list[str]` that returns the list of CadQuery constraint type strings:
   ```python
   MATE_CONSTRAINTS = {
       MateType.FASTENED:    ["Plane", "Axis", "Point"],
       MateType.REVOLUTE:    ["Axis", "Point"],
       MateType.SLIDER:      ["Axis", "Plane"],
       MateType.CYLINDRICAL: ["Axis"],
       MateType.PLANAR:      ["Plane"],
       MateType.BALL:        ["Point"],
   }
   ```

4. Build the assembly using constraints:
   ```python
   asm = cq.Assembly()
   for part_name, step_path in parts.items():
       asm.add(cq.importers.importStep(step_path), name=part_name)
   for rule in manifest.mating_rules:
       for constraint_type in MATE_CONSTRAINTS[rule.mate_type]:
           asm.constrain(
               f"{rule.part_a}@{rule.anchor_a}",
               f"{rule.part_b}@{rule.anchor_b}",
               constraint_type
           )
   try:
       asm.solve()
   except Exception as e:
       # Fall back to translation-based positioning if solver fails
       log_solver_failure(e, manifest)
   ```

5. Add solver failure detection: if `assembly.solve()` raises or leaves constraints unsatisfied, log the failure to `telemetry.py` with `solver_success: false` and fall back to the current translation-based approach.

6. Test empirically: generate 10–20 simple 2-part assemblies (shaft + housing, bracket + fastener, hinge + pin) and verify the solver produces correct geometry. Document failure cases.

7. Update `docker-compose.yml` if OCCT constraint solver has additional system dependencies.

**Files:** `schemas.py`, `agents.py`, `main.py` (or `assembler.py`), `telemetry.py`  
**Dependencies:** None — this is the foundational task.  
**Effort:** 3–5 days (including empirical testing of constraint solver reliability)  
**Success criteria:** 2-part assemblies (Fastened, Revolute) produce correctly positioned geometry without LLM specifying coordinates. Solver failure rate logged and below 20% on simple configurations.

---

### ✅ T1-02: PMC Interpenetration Detection

**STATUS: IMPLEMENTED**

**What it is:** After `assembly.solve()`, use OCCT's Boolean intersection API to check whether any two parts in the assembly overlap volumetrically. If the intersection volume is non-zero, the assembly has interpenetrating parts — a physically impossible result that looks plausible in the GLB viewer.

**CHANGES MADE:**
- Implemented `check_assembly_interpenetration()` in `agents.py` using CadQuery intersection checks
- Integrated call in `run_assembly_critic_loop()` before assembler selection
- Added telemetry logging via new `log_assembly_event()` in `telemetry.py`
- Logs interpenetration pairs, solver success, and constraint mode for analysis

**Why it matters:** The constraint solver can successfully solve constraints while still placing parts inside each other (if constraint anchors are ambiguous or under-constrained). Without this check, invalid assemblies silently pass through. This is a pure OCCT call — no LLM, no new infrastructure.

**Implementation steps:**

1. In the Deterministic Assembler, after `asm.solve()`, iterate over all part pairs (n-choose-2):
   ```python
   from itertools import combinations
   from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Common
   from OCC.Core.BRep import BRep_Builder
   from OCC.Core.GProp import GProp_GProps
   from OCC.Core.BRepGProp import brepgprop

   def check_interpenetration(solved_shapes: dict[str, TopoDS_Shape], tolerance=1e-3):
       violations = []
       for (name_a, shape_a), (name_b, shape_b) in combinations(solved_shapes.items(), 2):
           common = BRepAlgoAPI_Common(shape_a, shape_b)
           common.Build()
           if common.IsDone() and not common.Shape().IsNull():
               props = GProp_GProps()
               brepgprop.VolumeProperties(common.Shape(), props)
               volume = props.Mass()
               if volume > tolerance:
                   violations.append((name_a, name_b, volume))
       return violations
   ```

2. If violations are detected, log them to `telemetry.py` with field `interpenetration_violations: list[dict]`. Do NOT silently pass — surface this to the Planner for retry.

3. Add a Planner retry path: if interpenetration is detected, re-invoke the Planner with a feedback message: `"The assembly solver placed [Part A] and [Part B] intersecting with volume {volume:.2f}mm³. Please revise the anchor selectors for the mating rule between these parts."` (max 1 retry on interpenetration).

4. Expose interpenetration status in the Streamlit frontend: add a warning badge on the assembly viewer if interpenetration was detected and not resolved.

**Files:** `assembler.py` / `main.py`, `telemetry.py`, `frontend/app.py`  
**Dependencies:** T1-01 (needs assembled shapes from constraint solve; still apply to fallback translation path too)  
**Effort:** 1–2 days  
**Success criteria:** Intentionally overlapping test assemblies trigger violations; non-overlapping assemblies pass silently. Telemetry records `interpenetration_violations`.

---

### ✅ T1-03: Assembly Manifest Review UI

**STATUS: IMPLEMENTED**

**What it is:** Show the Planner's `AssemblyManifest` (parts list + mating rules) to the user in readable form in the Streamlit frontend before executing the Machinist chain. The user can approve, edit, or abort before any compute is consumed.

**CHANGES MADE:**
- Added `/plan` endpoint to `main.py` that runs only the Planner, returns manifest as JSON
- Implemented `fetch_plan()` and `render_plan_review()` helpers in `frontend/app.py`
- Added session state for plan review flow: `show_plan`, `pending_plan`, `pending_prompt`
- User flow: Generate → Plan review (for multi-part) → Approval → Execute

**Why it matters:** Planner errors on complex assemblies (wrong part count, misidentified mate type) propagate through N Machinist chains and waste significant compute. This is a pure UI change — the manifest already exists in the codebase. One toggle stops bad plans before they run.

**Implementation steps:**

1. Open `frontend/app.py`. Identify the point where the `/generate` call is made after the user submits a prompt.

2. Add a two-phase flow:
   - **Phase 1 — Plan:** Call a new `/plan` endpoint (see step 3) that returns only the `AssemblyManifest` JSON without running Machinists.
   - **Phase 2 — Execute:** Show the manifest, wait for user confirmation, then call `/generate` with the (optionally edited) manifest.

3. In `main.py`, add a `/plan` endpoint:
   ```python
   @app.post("/plan")
   async def plan_assembly(request: GenerateRequest) -> AssemblyManifest:
       manifest = await planner.plan(request.prompt)
       return manifest
   ```
   This invokes only the Planner agent, not the Machinists.

4. In `frontend/app.py`, after receiving the manifest, render it as a readable table:
   ```python
   st.subheader("Assembly Plan — review before executing")
   for i, part in enumerate(manifest.parts):
       st.write(f"**Part {i+1}:** {part.name} ({part.domain})")
   st.write("**Mating Rules:**")
   for rule in manifest.mating_rules:
       st.write(f"- {rule.part_a} ↔ {rule.part_b}: {rule.mate_type}")
   col1, col2 = st.columns(2)
   if col1.button("✅ Looks right — generate"):
       # Proceed to execute
   if col2.button("✗ Cancel"):
       st.stop()
   ```

5. Add a `st.text_area` below the table pre-filled with the manifest JSON, allowing advanced users to directly edit it before confirming.

6. Pass the (potentially edited) manifest to `/generate` as an optional `manifest_override` field in the request body, skipping the Planner if provided.

**Files:** `frontend/app.py`, `main.py`, `schemas.py`  
**Dependencies:** None (works with existing translation-based assembly; improves further with T1-01)  
**Effort:** 1–2 days  
**Success criteria:** On submit, user sees parts list and mates before any Machinist runs. User can cancel or proceed. Proceeding uses the displayed plan.

---

### ✅ T1-04: ProCAD-Style Proactive Clarification Gate

**STATUS: IMPLEMENTED**

**What it is:** Add a lightweight pre-Planner step that asks Gemini to identify ambiguities in the user's prompt before the full pipeline runs. If ambiguities are found, show them in the Streamlit UI as optional fill-in fields. The user can answer some, skip others, or proceed with the original prompt.

**CHANGES MADE:**
- Implemented `run_clarifier()` async agent in `agents.py` with structured JSON output
- Added `/clarify` endpoint to `main.py` with rate limiting
- Integrated clarification flow in `frontend/app.py` with optional answer fields
- User flow: Generate → Clarification questions (if any) → Plan review → Execute
- Skip button allows users to bypass clarification and proceed directly

**Why it matters:** Assembly prompts are often severely underspecified ("a simple gearbox" → how many stages? What gear ratios? What housing type?). The Planner must guess and wrong guesses propagate through the entire chain. One cheap LLM call upfront surfaces these gaps before the expensive work begins.

**Implementation steps:**

1. In `agents.py`, add a `ClarifierAgent`:
   ```python
   CLARIFIER_PROMPT = """You are a CAD requirements analyst. Given a user's assembly design prompt, 
   identify the most important missing specifications that would prevent generating a correct assembly.
   Return a JSON object with a single field "ambiguities": a list of strings, each describing one 
   missing specification. Maximum 5 items. If the prompt is sufficiently detailed, return an empty list.
   Focus only on structurally important gaps (part count, key dimensions, connection types).
   Do NOT ask for decorative details."""
   
   async def clarify(prompt: str) -> list[str]:
       response = await gemini.generate(
           system=CLARIFIER_PROMPT,
           user=prompt,
           response_schema={"ambiguities": list[str]}
       )
       return response.ambiguities
   ```

2. In `main.py`, add a `/clarify` endpoint:
   ```python
   @app.post("/clarify")
   async def clarify_prompt(request: GenerateRequest) -> list[str]:
       return await clarifier.clarify(request.prompt)
   ```

3. In `frontend/app.py`, after the user submits a prompt but before calling `/plan` or `/generate`, call `/clarify`. If the returned list is non-empty, render each ambiguity as an optional `st.text_input`:
   ```python
   ambiguities = call_clarify(prompt)
   if ambiguities:
       st.info("**Optional: answer these to improve accuracy**")
       clarifications = {}
       for amb in ambiguities:
           clarifications[amb] = st.text_input(amb, key=amb)
       # Append answered clarifications to prompt
       answered = [f"{q}: {a}" for q, a in clarifications.items() if a]
       if answered:
           prompt = prompt + "\n\nAdditional context:\n" + "\n".join(answered)
   ```

4. Add a "Skip clarification" button to bypass for experienced users.

5. Log clarification events to `telemetry.py`: record `clarification_triggered: bool`, `ambiguities_count: int`, `user_answered_count: int`.

**Files:** `agents.py`, `main.py`, `frontend/app.py`, `telemetry.py`  
**Dependencies:** None  
**Effort:** 1–2 days  
**Success criteria:** Submitting "a simple gearbox" triggers 3–5 clarifying questions in the UI. Submitting a fully specified prompt triggers no questions. Answered questions are appended to the prompt before planning.

---

### ✅ T1-05: Few-Shot Retrieval Augmentation for Machinist

**STATUS: IMPLEMENTED**

**What it is:** Build a vector store of (prompt, CadQuery script) pairs from successful telemetry runs. At inference time, embed the current part description and inject the 3–5 most similar past successes as few-shot examples into the Machinist's context window.

**CHANGES MADE:**
- Created `retrieval.py` with pure Python TF-IDF similarity (no external dependencies)
- Functions: `build_index()`, `retrieve_few_shots()`, `add_to_index()`
- Integrated in `run_machinist()` to inject 3 most similar examples on first attempt only
- Updated `run_critic_loop()` to add successful generations to index for future retrieval
- Examples only injected on first attempt, not on retries with error context

**Implementation steps:**

1. Install dependencies: `pip install chromadb sentence-transformers` (or use OpenAI embeddings API).

2. Write a `build_vector_store.py` script that:
   a. Reads all records from `telemetry.py`'s JSONL log where `success_status: true`.
   b. For each record, extracts `(part_description, generated_code)` pairs.
   c. Embeds each `part_description` using a sentence transformer (e.g., `all-MiniLM-L6-v2`).
   d. Stores pairs in a local ChromaDB collection at `/tmp/mirum/retrieval/machinist_store`.

3. In `agents.py`, add a `retrieve_few_shots(part_description: str, k: int = 3) -> list[dict]` function:
   ```python
   def retrieve_few_shots(part_description: str, k: int = 3) -> list[dict]:
       results = collection.query(
           query_texts=[part_description],
           n_results=k
       )
       return [
           {"description": doc, "code": meta["code"]}
           for doc, meta in zip(results["documents"][0], results["metadatas"][0])
       ]
   ```

4. In the Machinist's prompt construction, prepend retrieved examples:
   ```python
   few_shots = retrieve_few_shots(part.description)
   few_shot_text = "\n\n".join([
       f"# Example: {ex['description']}\n```python\n{ex['code']}\n```"
       for ex in few_shots
   ])
   machinist_prompt = few_shot_text + "\n\n---\n\n" + base_machinist_prompt
   ```

5. Add a startup script or Dockerfile step that rebuilds the vector store from telemetry on service start (or incrementally on each successful generation).

6. Add a `RETRIEVAL_ENABLED=true` environment variable flag to allow disabling retrieval for A/B testing.

**Files:** `agents.py`, `telemetry.py`, new `retrieval.py`, `docker-compose.yml`  
**Dependencies:** Requires at least ~100 successful telemetry records to be useful. Start collecting immediately and enable once sufficient data exists.  
**Effort:** 2–3 days  
**Success criteria:** At inference, Machinist context contains 3 relevant few-shot examples. Failure rate (via Critic Loop retry count) decreases measurably vs. no retrieval. A/B test result documented.

---

### ✅ T1-06: Parametric Design History Exposure

**STATUS: IMPLEMENTED**

**What it is:** Post-process every generated CadQuery script to extract numeric literals and replace them with named variables at the top of the script. Display these named parameters in a sidebar in the Streamlit UI with sliders or text inputs, allowing non-programmer engineers to modify the model without editing Python.

**CHANGES MADE:**
- Created `postprocessing.py` with `parameterize_script()` function using regex-based parameter extraction
- Extracts numeric literals from CadQuery method calls and generates named variables (RADIUS, HEIGHT, etc.)
- Integrated in `main.py` for single-part paths before export
- Added parameter sidebar in `frontend/app.py` with number inputs and "Apply Parameters" button
- Parameters extracted and passed in response payload to frontend
- Users can modify parameters without touching Python code

**Why it matters:** Currently, Mirum outputs a CadQuery script with magic numbers — `circle(25).extrude(30)`. An engineer wanting to change the height must know Python. Extracting named variables transforms the output from "opaque code" to "parametric model with a simple editing interface" — a significant UX improvement for the target ICP.

**Implementation steps:**

1. Write a `parameterize_script(code: str) -> tuple[str, dict]` function in a new `postprocessing.py`:
   ```python
   import ast, re

   def parameterize_script(code: str) -> tuple[str, dict]:
       """
       Extract numeric literals from a CadQuery script and replace with named variables.
       Returns (parameterized_script, parameter_dict).
       """
       # Parse to find all numeric literals with context (function arg name if possible)
       tree = ast.parse(code)
       # Collect all numeric constants with their parent call's keyword name if available
       params = {}
       param_counter = {"n": 0}
       
       def make_param_name(value, context_hint=None):
           param_counter["n"] += 1
           if context_hint:
               return f"{context_hint.upper()}_{param_counter['n']}"
           return f"PARAM_{param_counter['n']}"
       
       # Use regex for simple replacement (ast rewrite is more robust but more complex)
       # Pattern: find numbers in .circle(N), .extrude(N), .rect(N, M) etc.
       pattern = re.compile(r'(\.\w+\()(\d+\.?\d*)')
       # ... (full implementation replaces numbers with variable references)
       
       header = "# Parameters — edit these values to modify the model\n"
       for name, value in params.items():
           header += f"{name} = {value}  # mm\n"
       
       return header + "\n" + modified_code, params
   ```
   Note: for a robust implementation, use Python's `ast` module to walk the tree and rewrite `Constant` nodes inside function calls, using the keyword argument name or positional index as the variable name hint.

2. Apply `parameterize_script()` to every successfully compiled CadQuery script, after the Critic Loop confirms it's valid.

3. Store the `parameter_dict` alongside the script in the response payload.

4. In `frontend/app.py`, add a "Parameters" sidebar panel:
   ```python
   if parameter_dict:
       st.sidebar.subheader("Parameters")
       updated_params = {}
       for name, value in parameter_dict.items():
           updated_params[name] = st.sidebar.number_input(
               name, value=float(value), step=0.5, format="%.1f"
           )
       if st.sidebar.button("Apply changes"):
           # Substitute updated values into script and re-run via /run endpoint
           new_script = apply_params(script, updated_params)
           st.session_state["script"] = new_script
   ```

5. Wire the "Apply changes" button to the existing `/run` endpoint (already in `main.py`) which executes a CadQuery script directly.

**Files:** new `postprocessing.py`, `main.py`, `frontend/app.py`, `schemas.py`  
**Dependencies:** None (applies to existing pipeline output)  
**Effort:** 2–3 days  
**Success criteria:** Generated scripts have named `RADIUS`, `HEIGHT`, `WALL_THICKNESS` etc. variables at the top. Sidebar shows sliders. Adjusting a slider and clicking Apply re-renders the model with new dimensions.

---

### ✅ T1-07: LVM Score — Semantic Intent Evaluation Metric

**STATUS: IMPLEMENTED**

**What it is:** Add a Language-Vision Match (LVM) evaluation metric that uses a VLM (GPT-4V or Gemini) to score how well the generated 3D model matches the text prompt, using rendered multi-view images. Unlike Chamfer Distance, this captures whether the *semantic intent* of the prompt was fulfilled.

**CHANGES MADE:**
- Created `evaluation.py` with `compute_lvm_score()` async function (code-level evaluation path)
- Uses Gemini with structured JSON output for semantic, feature completeness, and geometric plausibility scores
- Integrated fire-and-forget LVM scoring in `run_critic_loop()` after successful first attempt
- Added `log_lvm_score()` function to write results to telemetry
- Non-blocking: scoring happens in background task, doesn't delay pipeline

**Why it matters:** Chamfer Distance requires a ground-truth reference model and measures geometric similarity. Most Mirum prompts don't have reference models. The LVM Score enables qualitative evaluation of every generated output without reference geometry — critical for evaluating assembly outputs where ground truth rarely exists.

**Implementation steps:**

1. Write a `render_multiview(step_path: str, output_dir: str) -> list[str]` function using OCCT's visualization API or pythonocc-core:
   ```python
   # Render 6 views: front, back, left, right, top, isometric
   VIEWS = [
       ("front",     (0, -1, 0),  (0, 0, 1)),
       ("back",      (0, 1, 0),   (0, 0, 1)),
       ("left",      (-1, 0, 0),  (0, 0, 1)),
       ("right",     (1, 0, 0),   (0, 0, 1)),
       ("top",       (0, 0, 1),   (0, 1, 0)),
       ("isometric", (1, -1, 1),  (0, 0, 1)),
   ]
   ```
   Alternatively, use `cadquery.occ_impl.geom` or convert to GLB and render with a headless Three.js/Blender subprocess.

2. Write an `lvm_score(prompt: str, image_paths: list[str]) -> dict` function that calls GPT-4V or Gemini Vision:
   ```python
   SCORING_PROMPT = """You are evaluating a 3D CAD model generated from a text prompt.
   You are shown 6 views of the model (front, back, left, right, top, isometric).
   The original prompt was: "{prompt}"
   
   Score the model on:
   1. Semantic accuracy (1-5): Does the model match the described object type?
   2. Feature completeness (1-5): Are the key features from the prompt present?
   3. Geometric plausibility (1-5): Does the model look physically reasonable?
   
   Return JSON: {{"semantic_accuracy": N, "feature_completeness": N, "geometric_plausibility": N, 
   "lvm_score": average, "missing_features": ["..."], "notes": "..."}}"""
   ```

3. Add an `eval_harness.py` script that runs the full pipeline on a fixed test set of prompts and computes LVM Score for every output.

4. Log LVM Score to `telemetry.py` for every generation: `"lvm_score": float, "lvm_breakdown": dict`.

5. Surface LVM Score in the Streamlit UI as a quality indicator below the 3D viewer: "Semantic match: 4.2/5.0".

**Files:** new `evaluation.py`, `telemetry.py`, `frontend/app.py`  
**Dependencies:** None (independent of other tasks)  
**Effort:** 2–3 days  
**Success criteria:** Every generation produces an LVM Score. The eval harness runs on 50-prompt test set and produces mean LVM Score. Score correlates with human judgment on 20 manually rated examples.

---

### ✅ T1-08: F1 per Operation Type — Error Attribution Telemetry

**STATUS: IMPLEMENTED**

**What it is:** When the Critic Loop exhausts retries and fails, classify the failure by CadQuery operation type (extrude, revolve, fillet, boolean, loft, constrain, thread, airfoil, etc.) and record the type in telemetry. This reveals which operations are responsible for failures, enabling targeted training data collection.

**CHANGES MADE:**
- Created `failure_analysis.py` with `classify_failure()` function and regex patterns for 17 operation types
- Integrated failure classification in `run_critic_loop()` for every failed attempt
- Added `failure_operation` field to telemetry log
- Implemented `generate_failure_report()` with operation breakdown and domain stratification
- Includes `print_failure_report()` for formatted console output

**Why it matters:** The current telemetry records raw tracebacks. Aggregating these into operation-type failure rates tells you that `loft()` fails 40% of the time and `fillet()` fails 5% of the time — directing training attention to where it's needed. This is a pure telemetry analysis exercise requiring no new infrastructure.

**Implementation steps:**

1. Write a `classify_failure(traceback: str, code: str) -> str` function in `telemetry.py` or a new `failure_analysis.py`:
   ```python
   OPERATION_PATTERNS = [
       ("extrude",   [r"\.extrude\(", r"extrusion"]),
       ("revolve",   [r"\.revolve\(", r"revolution"]),
       ("fillet",    [r"\.fillet\(", r"FilletError"]),
       ("chamfer",   [r"\.chamfer\("]),
       ("loft",      [r"\.loft\(", r"LoftError"]),
       ("boolean",   [r"BRepAlgoAPI", r"\.cut\(", r"\.union\(", r"\.intersect\("]),
       ("constrain", [r"\.constrain\(", r"assembly\.solve\("]),
       ("thread",    [r"makeThread", r"helix"]),
       ("shell",     [r"\.shell\("]),
       ("spline",    [r"makeSpline", r"\.spline\("]),
       ("selector",  [r"CadQuery selector", r"No object", r"Empty wire"]),
   ]
   
   def classify_failure(traceback: str, code: str) -> str:
       combined = (traceback + "\n" + code).lower()
       for op_name, patterns in OPERATION_PATTERNS:
           if any(re.search(p, combined, re.IGNORECASE) for p in patterns):
               return op_name
       return "unknown"
   ```

2. In `critic.py` (or wherever the Critic Loop exhaustion is recorded), call `classify_failure()` and add `"failure_operation": result` to the telemetry record.

3. Write a `failure_report.py` analysis script that reads the JSONL telemetry and prints a failure rate table per operation type:
   ```
   Operation    | Attempts | Failures | Failure Rate
   loft         |      45  |     19   | 42.2%
   fillet       |     312  |     16   | 5.1%
   constrain    |      28  |     11   | 39.3%
   ```

4. Run this report weekly and use it to prioritize which operations need more training examples or `cad_utils.py` helper functions.

**Files:** `critic.py`, `telemetry.py`, new `failure_analysis.py`  
**Dependencies:** None  
**Effort:** 0.5–1 day  
**Success criteria:** Every failed generation records a `failure_operation` field. Running `failure_report.py` produces an operation-type breakdown table. Top 3 failure operations identified.

---

### ✅ T1-09: Domain-Stratified Evaluation

**STATUS: IMPLEMENTED**

**What it is:** Tag every telemetry record with its domain classification (A/B/C/D) and report invalidity rate, retry count, and LVM Score separately per domain. This reveals whether the system is consistently strong across domains or has domain-specific weak spots.

**CHANGES MADE:**
- Domain tagging already present in telemetry via `domain_classification` field
- Enhanced `failure_analysis.py` with domain-stratified reporting in `generate_failure_report()`
- Added `/stats` endpoint to `main.py` that returns domain and operation-level metrics
- Metrics include attempts, failures, and failure rates per domain and per operation

**Why it matters:** Aggregated metrics hide the real picture. If Domain D (aerospace) has a 60% failure rate and Domain A (structural) has a 5% failure rate, the aggregate might look acceptable while a whole segment of your ICP is getting poor results. Stratified reporting costs nothing — the domain classification already happens in `classifier.py`.

**Implementation steps:**

1. In `telemetry.py`, verify that `domain_classification` is already logged per-record (from the codebase read, it is). If not, add it.

2. Update `failure_analysis.py` to additionally group by domain:
   ```python
   from collections import defaultdict
   
   stats = defaultdict(lambda: {"attempts": 0, "failures": 0, "retries": []})
   for record in load_telemetry():
       domain = record.get("domain_classification", "unknown")
       stats[domain]["attempts"] += 1
       if not record.get("success_status"):
           stats[domain]["failures"] += 1
       stats[domain]["retries"].append(record.get("attempt_number", 1))
   ```

3. Add domain tagging to `main.py`: after the classifier runs, propagate the `domain` field into all downstream telemetry records for that request.

4. Extend the eval harness (`eval_harness.py` from T1-07) to include domain in the test prompt metadata and report LVM Score stratified by domain.

5. Create a simple dashboard in Streamlit (admin page) showing per-domain metrics.

**Files:** `telemetry.py`, `failure_analysis.py`, `main.py`, `frontend/app.py`  
**Dependencies:** T1-07 (LVM Score), T1-08 (failure classification)  
**Effort:** 0.5–1 day  
**Success criteria:** Telemetry analysis script prints per-domain table. Domain with highest failure rate identified. Eval harness reports LVM Score per domain.

---

### ✅ T1-10: SaaS Pricing Calibration to Compute Cost

**STATUS: IMPLEMENTED**

**What it is:** Build a spreadsheet model estimating cost-per-assembly at various complexity levels, using actual Gemini API pricing and t2cIII telemetry data on tokens-per-generation. Verify that proposed pricing tiers cover compute costs.

**CHANGES MADE:**
- Added `log_token_usage()` to `telemetry.py` with Gemini 2.5 Flash pricing constants
- Integrated token logging in `run_planner()` and `run_machinist()` via response.usage_metadata
- Created `cost_analysis.py` with `compute_cost_report()` for cost breakdown by agent
- Includes per-agent metrics: calls, tokens, estimated USD cost
- Includes `print_cost_report()` for formatted console output with pricing note

**Why it matters:** The proposed API pricing ($0.10–0.50/assembly) may be below cost for complex assemblies. Before setting prices, verify the economics. This is a spreadsheet exercise using telemetry data.

**Implementation steps:**

1. Write a `cost_analysis.py` script that reads telemetry and computes per-record token consumption:
   ```python
   # Telemetry records should include token counts from Gemini API responses
   # If not currently logged, add token logging to agents.py
   for record in load_telemetry():
       input_tokens = record.get("input_tokens", 0)
       output_tokens = record.get("output_tokens", 0)
       # Gemini 2.5 Flash pricing (as of 2026-04):
       # ~$0.075/1M input tokens, ~$0.30/1M output tokens
       cost = (input_tokens * 0.075 + output_tokens * 0.30) / 1_000_000
   ```

2. If token counts aren't in telemetry, add them: in `agents.py`, capture `response.usage.input_tokens` and `response.usage.output_tokens` from the Gemini SDK response object.

3. Compute cost distributions by assembly complexity (1-part, 2-part, 3+ parts) and by domain. Report: mean, p50, p90, p99 cost per assembly.

4. Build the pricing spreadsheet: `output/pricing-model.xlsx` with columns:
   - Assembly complexity tier
   - Mean API cost (LLM calls only)
   - OCCT compute time × server cost estimate
   - Storage/egress cost estimate
   - Total COGS per assembly
   - Proposed price
   - Gross margin

5. Document findings in `output/pricing-analysis.md`.

**Files:** `agents.py` (add token logging), new `cost_analysis.py`, `telemetry.py`  
**Dependencies:** None  
**Effort:** 0.5–1 day (plus data collection time if token logging not yet active)  
**Success criteria:** Spreadsheet model with COGS per assembly tier. Proposed prices confirmed to exceed COGS with acceptable margin. Adjust pricing tiers if needed.

---

### ✅ T1-11: ICP Discovery Interview Plan

**STATUS: IMPLEMENTED**

**What it is:** Design a structured 45-minute interview guide for mechanical engineers at mid-size manufacturing companies, targeting the 10–20 interviews needed to validate (or invalidate) the ICP hypothesis before v1 launch.

**CHANGES MADE:**
- Created comprehensive interview guide at `backend/output/icp-interview-guide.md`
- Guide includes 7 sections (Background, Problem Validation, Technical Feasibility, Value Perception, Org Fit, Use Cases, Closing)
- Includes scoring rubric for ICP fit across 6 dimensions (Problem Resonance through Organizational Readiness)
- Provides sample scenarios for high/medium/low fit prospects
- Includes post-interview analysis framework and tracking template

**Implementation steps:**

1. Write the interview guide at `output/icp-interview-guide.md`. Structure it as:
   - **Intro (5 min):** Role, company, CAD tools used, years of experience
   - **Workflow mapping (15 min):** Walk me through your last product design from initial brief to first CAD model. Where do you spend the most time? Where do you get stuck?
   - **Assembly-specific (10 min):** When you start a new assembly design, what does "first draft" look like? How long does it take? What are the most error-prone steps?
   - **Tool awareness (5 min):** Have you tried any AI-assisted CAD tools? (Text-to-CAD, Zookeeper, SolidAI, etc.) What worked? What didn't fit?
   - **Concept reaction (10 min):** Show a Mirum demo video. "On a scale of 1–10, how much would this change your workflow? What would you need it to do differently to actually use it?"
   - **Closing (5 min):** Pricing sensitivity question ("If this did exactly what you need, what would you pay per seat per month?")

2. Identify recruiting channels: LinkedIn (mechanical engineers at 50–500 person manufacturing companies), r/cad, SOLIDWORKS User Groups, engineering manager networks.

3. Build a scoring rubric for each interview: code responses for `workflow_pain` (1–5), `assembly_bottleneck` (1–5), `ai_tool_awareness` (1–5), `concept_resonance` (1–10), `willingness_to_pay` (monthly $$).

4. Define the go/no-go threshold: if 7 of 10 interviews score `assembly_bottleneck` ≥ 4 and `concept_resonance` ≥ 7, the ICP hypothesis is validated.

5. Set a target: complete 10 interviews before v1 feature freeze. Schedule them in the next 2 weeks using Calendly.

**Files:** new `output/icp-interview-guide.md`  
**Dependencies:** None  
**Effort:** 0.5 days to write the guide; 2–3 weeks to execute 10 interviews  
**Success criteria:** 10 completed interviews with coded scores. Go/no-go recommendation documented. ICP hypothesis confirmed or revised.

---

## Tier 2 — Weeks

### ✅ T2-01: Features-Based Vocabulary in Prompts and Strategy Prompts

**STATUS: IMPLEMENTED** — `FEATURE_VOCABULARY` constant added to `strategies.py`; injected as `rule_feature_vocabulary` into Domain A and B `_DOMAIN_RULES`; keyword triggers added so it also fires for feature-term prompts. Clarifier uses same vocabulary.

**What it is:** Augment the Machinist strategy system prompts and SFT training dataset with feature-level engineering vocabulary: boss, pocket, fillet, chamfer, draft, shell, rib, thread boss, flange, bore, counterbore, countersink, web, gusset. This is the vocabulary mechanical engineers actually use.

**Why it matters:** Currently, Mirum's strategy prompts describe geometry in spatial terms ("a cylinder with a hole") rather than feature terms ("a flanged boss with a central bore"). Feature vocabulary reduces ambiguity and closer mirrors how ICP users describe parts.

**Implementation steps:**

1. Build a feature vocabulary reference in `strategies.py`. For each domain, add a **Feature Vocabulary** section to the system prompt listing the engineering terms and their geometric definitions:
   ```python
   FEATURE_VOCABULARY = """
   Engineering feature vocabulary — use these terms when interpreting prompts:
   - Boss: a raised cylindrical protrusion (positive material feature)
   - Pocket: a recessed cavity (negative material feature, open top)
   - Bore: a through or blind cylindrical hole
   - Counterbore: a stepped bore (larger diameter entry + smaller diameter depth)
   - Countersink: a conical chamfered bore entry (for flat-head screws)
   - Flange: a flat rim extending from a cylindrical feature for mating
   - Web: a thin connecting wall between structural ribs
   - Gusset: a triangular reinforcing feature at a corner joint
   - Rib: a thin plate projecting from a surface for stiffness
   - Boss (mounting): a short cylindrical protrusion for screw/insert location
   - Thread boss: a cylindrical protrusion with internal or external thread
   - Shell: a thin-walled hollow version of a solid (uniform wall thickness)
   """
   ```

2. Prepend this vocabulary block to Domain A and Domain B strategy prompts in `strategies.py`.

3. For the Machinist SFT dataset (when building it in T2-05): generate a "feature vocabulary" variant of each prompt using Gemini. For each training pair `(geometric_description, CadQuery_code)`, generate a `(feature_vocabulary_description, CadQuery_code)` variant where the description uses standard engineering feature terms.
   ```
   Original: "a cylinder with a hole in it, sitting on a flat plate"
   Feature-vocab: "a flanged boss with a 10mm central bore"
   ```

4. Update the `ClarifierAgent` (T1-04) to use feature-vocabulary terms in its clarifying questions: "What type of bore is this — through bore, blind bore, or counterbore?"

**Files:** `strategies.py`, `agents.py`, training data pipeline scripts  
**Dependencies:** T1-04 (Clarifier), T2-05 (SFT dataset) for training pair augmentation  
**Effort:** 2–4 days (prompts) + ongoing (SFT augmentation)  
**Success criteria:** Strategy prompts include feature vocabulary. Machinist responds correctly to feature-vocabulary prompts like "flanged boss with counterbore" without needing spatial descriptions.

---

### ✅ T2-02: Limit Mates / DoF Range Annotation in MatingRule

**STATUS: IMPLEMENTED** — `dof_min`, `dof_max`, `dof_unit` optional fields added to `MatingRule` in `schemas.py`. `validate_dof_ranges()` added to `agents.py`. `/plan` endpoint returns `dof_violations` and displays ranges per mate rule. Planner prompt updated to emit DoF ranges for REVOLUTE/SLIDER/CYLINDRICAL when prompt specifies motion range.

**What it is:** Add optional `dof_min` and `dof_max` fields to `MatingRule` for bounded kinematic constraints — a hinge that goes 0° to 90°, a drawer slide that travels 0mm to 300mm. This differentiates kinematically equivalent joints that have different design intent.

**Why it matters:** A revolute joint from 0°–360° is a bearing; from 0°–90° it's a hinge. The current vocabulary can't express this. For mechanisms (door hinges, valve actuators, drawer slides), the range is part of the design specification.

**Implementation steps:**

1. In `schemas.py`, add to `MatingRule`:
   ```python
   class MatingRule(BaseModel):
       # ... existing fields ...
       dof_min: Optional[float] = None  # degrees for rotational, mm for translational
       dof_max: Optional[float] = None
       dof_unit: Optional[str] = None   # "deg" or "mm"
   ```

2. Update the Planner's system prompt: "For REVOLUTE, SLIDER, and CYLINDRICAL mates where the prompt specifies a motion range, emit dof_min and dof_max with the appropriate unit (deg or mm)."

3. In the Assembler, after `assembly.solve()`, add a kinematic validation check: for each bounded mate, verify that the solved position is within [dof_min, dof_max]. Log `dof_violation` to telemetry if out of range.

4. In the Streamlit UI, display DoF ranges for kinematic mates in the manifest review (T1-03): "Hinge (REVOLUTE, 0°–90°)".

**Files:** `schemas.py`, `agents.py`, `assembler.py`, `frontend/app.py`  
**Dependencies:** T1-01 (constraint-based assembly), T1-03 (manifest review)  
**Effort:** 1–2 days  
**Success criteria:** Planner emits `dof_min: 0, dof_max: 90, dof_unit: "deg"` for hinge joints when prompt specifies range. Assembler validates range after solve. Manifest display shows range.

---

### ✅ T2-03: Mechanical Mate Vocabulary (Gear, Screw, Cam, Rack-Pinion)

**STATUS: IMPLEMENTED** — `GEAR`, `SCREW`, `RACK_PINION`, `CAM` added to `MateType` enum. `coupling_ratio` field added to `MatingRule`. `extract_kinematic_metadata()` in `agents.py` serialises coupling constraints to JSON for downstream simulation. `/plan` endpoint returns `kinematic` block. These mate types are excluded from `cq.Constraint` solving (static assembly only).

**What it is:** Extend `MatingRule` with mechanism-level coupling constraints: Gear (fixed angular velocity ratio between two revolute joints), Screw (linear translation coupled to rotation at a pitch rate), Cam (surface-to-surface contact), Rack-Pinion (linear-to-rotary coupling). These are not additional DoF specifications — they are coupling constraints between already-specified joints.

**Why it matters:** For gearboxes, lead-screw mechanisms, and linkages — core Mirum use cases — the assembly has correct topology but incorrect kinematics without coupling constraints. A two-gear system with two Revolute mates has two independent rotations, not a mechanical transmission.

**Implementation steps:**

1. In `schemas.py`, add to `MateType` enum:
   ```python
   class MateType(str, Enum):
       # ... existing types ...
       GEAR = "GEAR"          # coupled revolute: ratio parameter
       SCREW = "SCREW"        # coupled revolute+slider: pitch parameter
       RACK_PINION = "RACK_PINION"  # revolute ↔ slider coupling
       CAM = "CAM"            # surface contact
   ```

2. Add coupling parameters to `MatingRule`:
   ```python
   class MatingRule(BaseModel):
       # ... existing fields ...
       coupling_ratio: Optional[float] = None   # gear ratio or screw pitch (mm/rev)
   ```

3. Update the Planner system prompt with coupling mate descriptions and example use cases: "Use GEAR mate with coupling_ratio = (teeth_B / teeth_A) for meshing gears. Use SCREW with coupling_ratio = lead_mm_per_revolution for lead screws."

4. In the Assembler, coupling mates are recorded as constraint metadata for simulation but do NOT affect `assembly.solve()` (which only does static positioning). Add a `kinematic_constraints` field to the assembly output JSON for downstream simulation tools.

5. For the evaluation suite (T1-07): add a "kinematic correctness" test for assemblies with coupling mates — verify that the specified gear ratio matches the geometry (tooth counts from the compiled gear geometry).

6. Plan training data: identify gearbox and lead-screw assemblies in the Fusion 360 Gallery dataset for Phase 3 training.

**Files:** `schemas.py`, `agents.py`, `assembler.py`, evaluation scripts  
**Dependencies:** T1-01 (constraint-based assembly), T2-02 (DoF ranges)  
**Effort:** 3–5 days  
**Success criteria:** Planner emits GEAR mate with ratio for a "2-stage spur gearbox" prompt. Coupling constraint recorded in assembly JSON output. Gear ratio validated against compiled tooth geometry.

---

### ✅ T2-04: G2 Continuity Validation for Domain C (Organic Parts)

**STATUS: IMPLEMENTED** — `backend/continuity_check.py` created with `check_g2_continuity()` (OCCT analytical via `BRepLProp_SLProps`; graceful fallback if pythonocc-core unavailable), `G2CheckResult` dataclass, and `check_g2_vlm_zebra()` stub awaiting rendering pipeline. Wired into `run_critic_loop()` in `agents.py`: Domain C parts that compile but fail G2 consume one retry attempt with structured continuity feedback injected into error context.

**What it is:** After successful Domain C compilation, run a Zebra Analysis render of the output surfaces and use a VLM to detect G1 creases (stripe discontinuities). If G1 creases are detected, feed back a targeted continuity-specific error message to the Critic for revision.

**Why it matters:** Domain C targets consumer products, medical devices, and industrial design — segments where Class A surface quality (minimum G2 continuity) is a hard requirement. The current Critic Loop only validates compilation correctness, not surface continuity. A Domain C part that compiles can still have visible G1 creases that make it unusable for its intended application.

**Implementation steps:**

1. Install pythonocc-core or use OCCT's built-in rendering for Zebra Analysis. Alternatively, implement a simplified curvature discontinuity check using `BRepLProp_SLProps`:
   ```python
   from OCC.Core.BRepLProp import BRepLProp_SLProps
   from OCC.Core.BRep import BRep_Tool
   from OCC.Core.TopExp import TopExp_Explorer
   from OCC.Core.TopAbs import TopAbs_EDGE
   
   def check_g2_continuity(shape: TopoDS_Shape) -> list[dict]:
       """Check G2 continuity at all shared edges between faces."""
       violations = []
       edge_explorer = TopExp_Explorer(shape, TopAbs_EDGE)
       while edge_explorer.More():
           edge = edge_explorer.Current()
           # For each edge, sample curvature from both adjacent faces
           # Flag if curvature vectors are non-parallel (G2 failure)
           # ... OCCT BRepLProp_SLProps sampling
           edge_explorer.Next()
       return violations
   ```

2. As a simpler alternative (for the first iteration): after Domain C compilation, render a Zebra Analysis image using a headless renderer (OCCT's `Display` or a Blender subprocess with HDRI environment). Upload the rendered image to Gemini Vision with:
   ```
   "This is a Zebra Analysis render (striped reflection) of a 3D surface. 
   Do any of the stripes show sharp kinks or discontinuities at surface boundaries?
   If yes, describe which boundary shows the kink. 
   Output JSON: {g2_pass: bool, violation_description: str}"
   ```

3. If `g2_pass: false`, inject the continuity feedback into the Critic's next-attempt prompt:
   ```
   "The surface at [boundary] shows a G1 crease (Zebra Analysis failure). 
   The current transition uses G0 or G1 continuity. 
   Replace the transition with cq.Edge.makeSpline() with curvature continuity 
   conditions at the endpoints (tangent vectors and curvature magnitudes must match 
   across the boundary). See CadQuery spline tangent documentation."
   ```

4. Add `g2_pass: bool` to telemetry records for Domain C parts.

5. Track Domain C G2 pass rate as a dedicated metric in the eval harness.

**Files:** new `continuity_check.py`, `critic.py`, `telemetry.py`, `strategies.py`  
**Dependencies:** T1-09 (domain-stratified telemetry)  
**Effort:** 3–5 days (OCCT analysis) or 1–2 days (VLM Zebra check shortcut)  
**Success criteria:** Domain C G2 pass rate tracked. At least one example where continuity feedback causes the Critic to switch from G1 to G2 surface construction.

---

### ✅ T2-05: Multi-Level Prompt Stratification for Machinist SFT Dataset

**STATUS: IMPLEMENTED** — `data_pipeline/prompt_stratification.py` created. `stratify_pair()` calls Gemini to produce abstract/beginner/intermediate/expert variants per training pair. `stratify_dataset()` runs threaded pipeline with resume support (tracks processed descriptions). `mine_telemetry_pairs()` extracts existing successful runs from telemetry JSONL as seed pairs. CLI: `python data_pipeline/prompt_stratification.py --input seed.jsonl --output stratified.jsonl --max-workers 4`.

**What it is:** For every (prompt, CadQuery code) training pair in the Machinist SFT dataset, generate four complexity variants of the prompt using Gemini: Abstract ("a housing"), Beginner ("a cylinder with a hole"), Intermediate (dimensions specified), and Expert (tolerances, material callouts, fit specifications). All four variants pair with the same CadQuery code — this quadruples dataset size at minimal cost.

**Why it matters:** Text2CAD's win-rate data shows tiered prompt training improves performance across all complexity levels. Without it, a fine-tuned model trained only on well-specified prompts fails on vague inputs (and vice versa). The ICP includes engineers who specify precisely and those who sketch loosely — the model must handle both.

**Implementation steps:**

1. Write a `prompt_stratification.py` script. For each training pair, call Gemini with:
   ```python
   STRATIFICATION_PROMPT = """Given a CadQuery script that generates a 3D part, 
   and an intermediate-level description of that part, generate FOUR descriptions 
   at different specificity levels:
   
   1. Abstract (1 sentence, no dimensions, just object type)
   2. Beginner (1-2 sentences, key geometric relationships, no dimensions)
   3. Intermediate (the original description with key dimensions)
   4. Expert (add material spec, tolerances, fit requirements, surface finish callouts)
   
   Part description: {description}
   CadQuery code: {code}
   
   Return JSON: {{"abstract": "...", "beginner": "...", "intermediate": "...", "expert": "..."}}"""
   ```

2. For each input pair, this generates 4 training pairs (the original + 3 variants). Multiply dataset size by 4.

3. Build a dataset construction pipeline:
   - Input: telemetry successes (prompt, code) + manually curated high-quality pairs
   - Process: stratification call per pair
   - Output: JSONL at `data/machinist_sft_stratified.jsonl` with fields: `{prompt, code, domain, complexity_level, source}`

4. Validate stratification quality: manually review 50 stratified pairs across all four levels. Check that Abstract variants are genuinely vague and Expert variants include real tolerance and material callouts.

5. Apply to all existing telemetry successes first (before investing in new data collection).

**Files:** new `data_pipeline/prompt_stratification.py`, data output at `data/`  
**Dependencies:** Requires at least 500 quality telemetry successes to be worthwhile  
**Effort:** 2–3 days (pipeline) + Gemini API cost per pair  
**Success criteria:** JSONL dataset with 4× original pair count, balanced across complexity levels. Manual review of 50 pairs passes quality check. Dataset ready for SFT training in T2-11.

---

### ✅ T2-06: DeepCAD → CadQuery Training Data Pipeline

**STATUS: IMPLEMENTED** — `data_pipeline/deepcad_to_cadquery.py` created. `deepcad_seq_to_cadquery()` converts DeepCAD sketch-extrude JSON sequences (LINE/ARC/CIRCLE/EXTRUDE ops) to CadQuery chains. `validate_cadquery_script()` filters via OCCT subprocess. `annotate_script()` generates abstract/technical/detailed descriptions via Gemini. `classify_domain()` tags each pair with A/B/C/D. `run_pipeline()` orchestrates end-to-end. CLI: `python deepcad_to_cadquery.py --deepcad-dir /data/deepcad --output data/deepcad_cq_pairs.jsonl`. Requires DeepCAD dataset download first.

**What it is:** Build a pipeline that takes the 180,000 parametric models in the DeepCAD dataset, reconstructs each as CadQuery Python code using a B-rep → sketch-extrude decomposition algorithm, then generates natural-language descriptions using Gemini from multi-view renders. Target: 100K+ high-quality (description, CadQuery) training pairs for Machinist SFT.

**Why it matters:** Telemetry mining alone produces training pairs slowly. DeepCAD provides 180K existing parametric models at zero human labeling cost. Combined with the Don't Mesh with Me B-rep→code pipeline insight, this is the fastest path to large-scale Machinist training data.

**Implementation steps:**

1. Download the DeepCAD dataset: `wget https://github.com/ChrisWu1997/DeepCAD` (or Hugging Face dataset link). DeepCAD provides models in its own JSON sketch-extrude sequence format.

2. Write a `deepcad_to_cadquery.py` converter. DeepCAD's format is already a sketch-extrude sequence — convert it directly to CadQuery method chains:
   ```python
   def deepcad_seq_to_cadquery(seq: dict) -> str:
       """
       DeepCAD sequences → CadQuery Python.
       Each operation: {type: "Line"|"Arc"|"Circle"|"Extrude", params: {...}}
       """
       lines = ["import cadquery as cq", "result = cq.Workplane('XY')"]
       for op in seq["operations"]:
           if op["type"] == "Circle":
               lines.append(f".circle({op['radius']:.2f})")
           elif op["type"] == "Extrude":
               lines.append(f".extrude({op['distance']:.2f})")
           # ... handle all operation types
       lines.append("show_object(result)")
       return "\n".join(lines)
   ```

3. Filter: execute each converted CadQuery script through OCCT and keep only the scripts that compile successfully (no errors). Target: 60–80% success rate.

4. For each successfully compiled script, render 6 views using the render pipeline from T1-07.

5. Generate text descriptions using Gemini:
   ```python
   ANNOTATION_PROMPT = """You are annotating a 3D CAD part for a training dataset.
   You are shown 6 rendered views of the part (front, back, left, right, top, isometric).
   Generate 3 natural-language descriptions at different specificity levels:
   - Abstract: 1 sentence describing the part type
   - Technical: 2-3 sentences with key geometric features and approximate dimensions  
   - Detailed: full specification including features, dimensions, and suggested material/application
   Return JSON: {{"abstract": "...", "technical": "...", "detailed": "..."}}"""
   ```

6. Filter by annotation quality: use Gemini's self-consistency score; discard pairs where the Abstract description is too vague to be useful ("a 3D shape").

7. Output: `data/deepcad_cq_pairs.jsonl` with fields `{description, code, domain, source: "deepcad"}`.

**Files:** new `data_pipeline/deepcad_to_cadquery.py`, `data_pipeline/annotate_renders.py`  
**Dependencies:** Rendering pipeline from T1-07; domain classifier to tag each pair with A/B/C  
**Effort:** 1–2 weeks  
**Success criteria:** 50,000+ (description, CadQuery) training pairs in JSONL format with compilation-verified CadQuery. Quality spot-check: 100 randomly sampled pairs reviewed manually, >80% rated accurate descriptions.

---

### ✅ T2-07: ABC Dataset → Synthetic Assembly Pipeline

**STATUS: IMPLEMENTED** — `data_pipeline/abc_assembly_pipeline.py` created. `classify_abc_part()` classifies STEP files into prismatic/cylindrical/sheet/organic using OCCT face type analysis. `build_or_load_index()` maintains SQLite index of classified parts. `COMPATIBLE_PAIRS` defines 5 compatible type/mate rules. `attempt_assembly()` tries cq.Constraint + assembly.solve() for each pair, checks bounding box overlap. `annotate_assembly()` generates descriptions via Gemini. `run_pipeline()` orchestrates. Outputs assembly manifest JSON + part paths per record. CLI: `python abc_assembly_pipeline.py --abc-dir /data/abc --output data/abc_assemblies.jsonl`.

**What it is:** Build a pipeline that clusters 1M ABC Dataset single-part CAD models by geometric type, then programmatically assembles matching part pairs (housing + shaft, bracket + fastener) using OCCT constraint solving, filters out invalid assemblies (non-solving, interpenetrating), and annotates with Gemini. Target: 500K synthetic 2-part assemblies.

**Why it matters:** The Fusion 360 Gallery provides 8,251 real assemblies — high quality but small. The ABC Dataset provides 1M single parts. Programmatically assembling them at scale fills the data gap for the Planner assembly training.

**Implementation steps:**

1. Download the ABC Dataset: `https://deep-geometry.github.io/abc-dataset/`. Available in STEP format.

2. Classify each ABC part into geometric types using OCCT feature recognition:
   ```python
   PART_TYPES = {
       "prismatic": lambda shape: is_primarily_flat_faces(shape),
       "cylindrical": lambda shape: has_dominant_cylinder(shape),
       "sheet": lambda shape: max_aspect_ratio(shape) > 10,
       "organic": lambda shape: has_freeform_surfaces(shape),
   }
   ```
   Build a `classify_abc_part(step_path) -> str` function. Store results in a SQLite index.

3. Define compatible part-pair rules for assembly (rules based on engineering intuition):
   ```python
   COMPATIBLE_PAIRS = [
       ("cylindrical", "prismatic", MateType.REVOLUTE),   # shaft in housing
       ("prismatic", "prismatic", MateType.FASTENED),     # block on block
       ("sheet", "prismatic", MateType.FASTENED),         # bracket on frame
       ("cylindrical", "cylindrical", MateType.CYLINDRICAL), # nested tubes
   ]
   ```

4. For each compatible pair rule, sample N random pairs from each type cluster. For each pair:
   a. Load both STEP files via OCCT.
   b. Select anchor faces automatically (for REVOLUTE: largest cylindrical face on part A + matching bore on part B).
   c. Apply `cq.Constraint` constraints and call `assembly.solve()`.
   d. Run PMC interpenetration check (T1-02).
   e. Keep only assemblies that solve without error and without interpenetration.

5. Annotate valid assemblies with Gemini using 6-view renders (same pipeline as T2-06).

6. Output: `data/abc_synthetic_assemblies.jsonl` with fields `{description, assembly_manifest_json, part_a_code, part_b_code, domain, source: "abc_synthetic"}`.

**Files:** new `data_pipeline/abc_assembly_pipeline.py`, `data_pipeline/abc_classifier.py`  
**Dependencies:** T1-01 (constraint assembly), T1-02 (interpenetration check), rendering pipeline from T1-07  
**Effort:** 2–4 weeks (pipeline + processing time)  
**Success criteria:** 100K+ valid 2-part assemblies with verified OCCT solve and descriptions. Assembly invalidity rate below 10% after filtering. Annotation quality spot-check passes.

---

### ✅ T2-08: Multi-Turn Conversational Refinement (`/refine` endpoint)

**STATUS: IMPLEMENTED** — `ManifestDiff` and `RefinementRequest` schemas added to `schemas.py`. `run_refiner()` and `apply_manifest_diff()` added to `agents.py`. `RefineRequest` + `/refine` POST endpoint added to `main.py`: runs RefinerAgent → applies diff → re-generates only changed parts → reassembles → returns new GLB. Refinement UI (text area + "Apply Refinement" button) added to Streamlit frontend. `last_manifest`/`last_scripts` session state tracks prior run for refinement context. Refinement triples logged to telemetry for future fine-tuning.

**What it is:** Add a `/refine` endpoint that takes an existing `AssemblyManifest` + compiled parts + natural-language refinement request, generates a differential update to the manifest (only changed parts/mates), re-runs only affected Machinists, and re-assembles. Pairs with a UI "Refine" input in Streamlit.

**Why it matters:** Zoo.dev's Zookeeper differentiates on conversational refinement. Mirum currently has no refinement loop. The minimum viable version (manifest diff + partial re-generation) is architecturally clean and directly competitive with Zookeeper's core interaction model.

**Implementation steps:**

1. In `schemas.py`, add a `RefinementRequest`:
   ```python
   class RefinementRequest(BaseModel):
       original_manifest: AssemblyManifest
       original_parts: dict[str, str]  # part_name → CadQuery script
       refinement_prompt: str
   ```

2. Write a `RefinerAgent` in `agents.py` that takes a refinement request and produces a `ManifestDiff`:
   ```python
   class ManifestDiff(BaseModel):
       modified_parts: list[str]   # part names to re-generate
       added_parts: list[Part]     # new parts
       removed_parts: list[str]    # parts to remove
       modified_mates: list[MatingRule]  # updated mates
   
   REFINER_PROMPT = """Given an existing assembly design and a refinement request, 
   output ONLY the changes needed — do not re-specify unchanged parts.
   If the user says 'make the housing thicker', only modify the housing part's description.
   Output a ManifestDiff JSON."""
   ```

3. In `main.py`, add a `/refine` endpoint:
   ```python
   @app.post("/refine")
   async def refine_assembly(request: RefinementRequest):
       diff = await refiner.refine(request)
       # Re-run Machinist only for modified_parts and added_parts
       new_parts = {**request.original_parts}
       for part_name in diff.modified_parts:
           new_parts[part_name] = await machinist.generate(
               updated_part_description, domain
           )
       # Re-assemble with updated manifest
       updated_manifest = apply_diff(request.original_manifest, diff)
       return await assemble(updated_manifest, new_parts)
   ```

4. In `frontend/app.py`, add a "Refine" text input below the 3D viewer:
   ```python
   refinement = st.text_input("What would you like to change?",
                               placeholder="make the flange thicker, add a chamfer to the top edge...")
   if refinement and st.button("Apply refinement"):
       # Call /refine endpoint with current manifest + scripts
   ```

5. Log refinement sessions to `telemetry.py`: each `(original_script, refinement_prompt, revised_script)` triple becomes a training example for future instruction-following fine-tuning.

**Files:** `schemas.py`, `agents.py`, `main.py`, `frontend/app.py`, `telemetry.py`  
**Dependencies:** T1-03 (manifest review — need manifest to be accessible in session state), T1-01 (constraint assembly)  
**Effort:** 1–2 weeks  
**Success criteria:** Submitting "make the shaft diameter 20% larger" re-generates only the shaft, not the housing. End-to-end refinement cycle < 30 seconds. Telemetry records refinement triples.

---

### ✅ T2-09: SOLIDWORKS-Native Parasolid Export via CAD Exchanger

**STATUS: IMPLEMENTED (scaffolded)** — `backend/exporters.py` created with `export_step()`, `export_parasolid()`, `export_stl()`, `export_dxf()`, `get_available_formats()`. `export_parasolid()` checks for CAD Exchanger SDK (commercial license required via `CADEXCHANGER_LICENSE_KEY` env var or `license.lic` file); falls back gracefully to STEP copy if unavailable. `/export/parasolid` POST endpoint added to `main.py`: runs script → produces STEP → converts to Parasolid (or falls back). `/generate` response now includes `export_formats` dict. **External blocker:** Requires CAD Exchanger commercial license (~$3K/year). Evaluation license available at cadexchanger.com.

**What it is:** Integrate CAD Exchanger to export Mirum's OCCT-compiled geometry as Parasolid `.x_t` files in addition to STEP and GLB. Parasolid is what SOLIDWORKS imports natively — without STEP conversion artifacts or topology loss.

**Why it matters:** Mirum's ICP is overwhelmingly SOLIDWORKS users. The difference between "import this STEP" and "import this Parasolid" is the difference between occasional geometry errors and reliable round-trips. This is a direct ICP friction reducer and enterprise value-add.

**Implementation steps:**

1. Evaluate CAD Exchanger licensing: the SDK requires a commercial license for Parasolid export. Check current pricing and obtain a development license. Evaluate the open-source XCAF alternative for STEP→Parasolid conversion.

2. Install CAD Exchanger SDK: `pip install cadexchanger` (or integrate via C++ bindings).

3. Write an `export_parasolid(step_path: str, output_path: str) -> bool` function in a new `exporters.py`:
   ```python
   import cadexchanger as cadex
   
   def export_parasolid(step_path: str, output_path: str) -> bool:
       model = cadex.ModelData_Model()
       reader = cadex.STEP_Reader()
       if not reader.Read(cadex.Base_UTF16String(step_path), model):
           return False
       writer = cadex.Parasolid_Writer()
       return writer.Write(model, cadex.Base_UTF16String(output_path))
   ```

4. Add Parasolid export as an option alongside STEP in the `/generate` response. Add a download button in Streamlit: "Download for SOLIDWORKS (.x_t)".

5. Test round-trip fidelity: export a 10-part test set to `.x_t`, import into SOLIDWORKS (or test via STEP round-trip as a proxy), verify no topology errors.

**Files:** new `exporters.py`, `main.py`, `frontend/app.py`, `docker-compose.yml` (add CAD Exchanger dependency)  
**Dependencies:** None  
**Effort:** 3–5 days  
**Success criteria:** `/generate` response includes `.x_t` download URL. File imports into SOLIDWORKS without error on 5 test parts. No topology loss compared to STEP import.

---

### ✅ T2-10: Program Sketching — Expand `cad_utils.py` with Parametric Templates

**STATUS: IMPLEMENTED** — Six new template functions added to `cad_utils.py`: `make_flanged_housing()`, `make_shaft()` (with keyway), `make_bracket()` (L-bracket with mounting holes), `make_bearing_housing()`, `make_spacer()`, `make_boss_with_bore()` (with counterbore). `AVAILABLE_TEMPLATES` list exported for strategy prompt injection. Domain A and B strategy prompts reference templates via the expanded feature vocabulary rule.

**What it is:** Add parametric template functions to `cad_utils.py` for the most common engineering shape classes: flanged housings, shafts with keyways, mounting brackets, bearing housings, spacers, and standard fastener clearance holes. The Machinist routes to these templates for matching shape families rather than generating free-form code.

**Why it matters:** Every shape added to `cad_utils.py` permanently raises the floor of Machinist quality for that class — it cannot generate a worse flanged housing than the template. The library is an investment with compounding returns, and the Critic Loop rate for these shapes drops to near-zero.

**Implementation steps:**

1. Design the function signatures for the highest-frequency engineering shapes:
   ```python
   def make_flanged_housing(
       OD: float, ID: float, height: float,
       flange_OD: float, flange_thickness: float,
       bolt_circle_diameter: float, n_bolt_holes: int,
       bolt_hole_diameter: float
   ) -> cq.Workplane:
       """Cylindrical housing with a mounting flange."""
   
   def make_shaft(
       diameter: float, length: float,
       keyway_width: float = 0, keyway_depth: float = 0,
       chamfer_end: float = 0.5
   ) -> cq.Workplane:
       """Shaft with optional keyway and end chamfers."""
   
   def make_bracket(
       base_width: float, base_length: float, base_thickness: float,
       wall_height: float, wall_thickness: float,
       n_mounting_holes: int, hole_diameter: float
   ) -> cq.Workplane:
       """L-bracket with mounting holes."""
   
   def make_bearing_housing(
       bearing_OD: float, bearing_width: float,
       housing_OD: float, housing_length: float,
       lip_thickness: float = 2.0
   ) -> cq.Workplane:
       """Bearing housing with press-fit bore."""
   ```

2. Implement each function in `cad_utils.py`, test each individually with `cq.exporters.export(shape, "test.step")`, and verify STEP validity.

3. Update Domain A and B strategy prompts in `strategies.py` to direct the Machinist toward these templates:
   ```
   "Available utility functions (prefer these over free-form generation):
   - make_flanged_housing(OD, ID, height, flange_OD, flange_thickness, bolt_circle_diameter, n_bolt_holes, bolt_hole_diameter)
   - make_shaft(diameter, length, keyway_width=0, keyway_depth=0, chamfer_end=0.5)
   - make_bracket(base_width, base_length, base_thickness, wall_height, wall_thickness, n_mounting_holes, hole_diameter)
   Use these functions when the part matches one of these shape classes, substituting appropriate dimensions from the description."
   ```

4. Add template detection to the retrieval system (T1-05): when retrieved few-shots show a shape matching a template, annotate the context with: "This shape type has a utility function available."

5. Extend the Clarifier (T1-04) to identify template-compatible requests and note the relevant template in its output.

**Files:** `cad_utils.py`, `strategies.py`  
**Dependencies:** T1-04 (Clarifier), T1-05 (retrieval augmentation)  
**Effort:** 3–5 days (implement and test 6–8 functions)  
**Success criteria:** Each new function produces valid STEP output. Machinist uses templates when prompted for "flanged housing" or "shaft with keyway". Failure rate for template-matched shapes drops to 0%.

---

### ✅ T2-11: Fine-Tuned Machinist v2 (Qwen2.5-7B on CadQuery Pairs)

**STATUS: IMPLEMENTED (scaffolded)** — `data_pipeline/finetune_machinist.py` created. `format_training_record()` converts JSONL pairs to chat-message triples with production strategy prompts as system messages. `load_and_format_dataset()` merges multiple JSONL files with deduplication. `train()` implements QLoRA SFT via HuggingFace `transformers` + `trl` + `peft`: 4-bit BitsAndBytes quantisation, LoRA rank 64 on q/k/v/o/gate/up projections, cosine LR schedule. `FinetunedMachinist` class provides production inference wrapper with graceful Gemini fallback for unavailable model or Domain D. `evaluate_invalidity_rate()` runs subprocess OCCT eval. CLI: `python finetune_machinist.py train --train-data ... --output-dir models/machinist-v2`. **External blocker:** Requires GPU (2× A100 80GB minimum) and T2-05/T2-06 data.

**What it is:** Fine-tune a Qwen2.5-7B model on the curated text-to-CadQuery dataset built in T2-05 and T2-06, targeting <1% invalid part rate. Replace Gemini Machinist calls for Domain A, B, and C with the fine-tuned model; keep Gemini for Domain D (aerospace complexity exceeds small model capability).

**Why it matters:** ProCAD demonstrates that 1.6K curated CadQuery samples fine-tuned on a small model outperform frontier zero-shot models. Fine-tuning reduces API cost by 90%+ (local inference vs. Gemini API) and enables domain specialization that zero-shot cannot achieve.

**Implementation steps:**

1. Assemble the training dataset: combine outputs from T2-05 (stratified prompts from telemetry), T2-06 (DeepCAD pairs), and manually curated high-quality pairs. Target: 5,000–20,000 pairs minimum (ProCAD's 1.6K is the floor; more is better).

2. Format for instruction tuning:
   ```jsonl
   {"messages": [
     {"role": "system", "content": "{domain_strategy_prompt}"},
     {"role": "user", "content": "{part_description}"},
     {"role": "assistant", "content": "{cadquery_python_code}"}
   ]}
   ```

3. Fine-tune using Hugging Face `transformers` + `trl` (SFT Trainer):
   ```python
   from trl import SFTTrainer
   from transformers import AutoModelForCausalLM, AutoTokenizer
   
   model = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-7B-Instruct")
   trainer = SFTTrainer(
       model=model,
       train_dataset=dataset,
       max_seq_length=4096,
       dataset_text_field="text",
   )
   trainer.train()
   ```

4. Evaluate the fine-tuned model on a held-out test set of 200 prompts: measure invalidity rate, Critic Loop retry count distribution, LVM Score (T1-07).

5. Deploy via vLLM or Ollama inside the existing Docker stack. Update `agents.py` to route Domain A/B/C Machinist calls to the local model endpoint instead of Gemini.

6. A/B test: run 100 prompts through both Gemini and the fine-tuned model; compare invalidity rate, LVM Score, and latency.

**Files:** new `training/fine_tune_machinist.py`, `agents.py`, `docker-compose.yml`  
**Dependencies:** T2-05 (stratified dataset), T2-06 (DeepCAD pairs) — needs sufficient training data  
**Effort:** 1–3 weeks (data prep + training + eval + deployment)  
**Success criteria:** Fine-tuned Machinist achieves <1% invalidity rate on test set. LVM Score ≥ Gemini baseline. Inference latency ≤ 5 seconds per part. API cost per generation reduced by >80%.

---

### ✅ T2-12: Requirements Engineering Agent (REA) Pre-Planner

**STATUS: IMPLEMENTED** — `DesignRequirements` schema added to `schemas.py` (primary_function, key_dimensions, material_class, environment, connecting_interfaces, production_volume, inferred_domain, confidence). `run_rea()` agent added to `agents.py`: runs concurrently with Planner, low-latency Gemini call with structured JSON schema. `run_planner()` updated to accept `requirements` param and prepend structured spec block. `/requirements` endpoint added to `main.py` for standalone REA call. Pipeline (`_pipeline()`) and `/plan` endpoint now run REA first and pass requirements to Planner. REA token usage logged to telemetry.

**What it is:** Add a structured requirements extraction step before the Planner. The REA parses the user's prompt into a requirements JSON: primary function, dimensional constraints, material class, environmental context, connecting parts, and production volume. The Planner receives this structured document alongside the raw prompt.

**Why it matters:** The current Planner simultaneously extracts requirements AND decomposes into parts in a single LLM call. Separating these tasks (REA then Planner) reduces the Planner's cognitive load and provides a reference specification that the evaluation loop can validate against.

**Implementation steps:**

1. Define the requirements schema in `schemas.py`:
   ```python
   class DesignRequirements(BaseModel):
       primary_function: str
       key_dimensions: Optional[dict[str, float]] = None  # {"OD_mm": 50, "height_mm": 30}
       material_class: Optional[str] = None  # "aluminum", "steel", "plastic", etc.
       environment: Optional[str] = None    # "outdoor", "corrosive", "high-temp", etc.
       connecting_interfaces: list[str] = []  # mating features mentioned
       production_volume: Optional[str] = None  # "prototype", "low volume", "production"
       inferred_domain: Optional[str] = None  # A, B, C, or D
   ```

2. Write a `RequirementsAgent` in `agents.py`:
   ```python
   REA_PROMPT = """Extract structured design requirements from this CAD design brief.
   Be conservative — only include information explicitly stated or clearly implied.
   Do not invent specifications not mentioned in the prompt."""
   ```

3. Add a `/requirements` endpoint (or integrate into `/plan`):
   ```python
   @app.post("/plan")
   async def plan(request: GenerateRequest):
       requirements = await rea.extract(request.prompt)
       manifest = await planner.plan(request.prompt, requirements)
       return PlanResponse(requirements=requirements, manifest=manifest)
   ```

4. Pass `DesignRequirements` to the Planner's context. Update the Planner prompt: "You have been provided structured requirements extracted from the user's prompt. Use these as the authoritative specification; use the raw prompt only for details not captured in the requirements."

5. Log `requirements` to telemetry for each generation. This enables future training on (requirements, manifest) pairs rather than (raw_prompt, manifest) pairs — a higher-quality training signal for the Planner.

6. In the Streamlit UI (T1-03 manifest review), show the extracted requirements above the manifest: "Extracted requirements: [primary_function] [key_dimensions]".

**Files:** `schemas.py`, `agents.py`, `main.py`, `frontend/app.py`  
**Dependencies:** T1-03 (manifest review), T1-04 (Clarifier) — REA and Clarifier should run in parallel  
**Effort:** 2–3 days  
**Success criteria:** Every generation logs structured requirements JSON. Planner produces better manifests for under-specified prompts (measured by LVM Score comparison). Manual review of 20 requirements extractions shows >85% accuracy.

---

### ✅ T2-13: Aerospace Beachhead — Demo Assemblies and SBIR Application

**STATUS: IMPLEMENTED** — `output/demos/aerospace/demo_prompts.md` created with exact prompts, expected part decompositions, mate types, and validation checklists for all 5 demo assemblies (wing structural, landing gear, engine nacelle, avionics bay bracket, hydraulic manifold). `output/sbir-phase1-draft.md` created with full 8-section Phase I application: technical abstract (250 words), problem statement, technical objectives, detailed approach (pipeline architecture, OML Master Modeling, constraint model table), innovation section, commercial application (pricing model, SOLIDWORKS integration path), 6-month milestone table, and SBIR topic identification guidance.

**What it is:** Build 5 demonstration assemblies targeting aerospace/defense use cases: wing structural assembly (ribs + spars + skin panels), landing gear mechanism, engine nacelle, avionics bay bracket assembly, hydraulic manifold. Document them as reference demonstrations. Prepare a DoD SBIR Phase I application.

**Why it matters:** Aerospace/defense is the clearest commercial beachhead for Mirum because Domain D (aerospace) is already deeply built out (NACA airfoil functions, wing/rib/spar strategies). SBIR Phase I provides $250K with a relatively low application bar for novel technical approaches in DoD-relevant areas.

**Implementation steps:**

1. Build the 5 demonstration assemblies (prompt → Mirum output → human polish if needed):
   - **Wing structural:** 3 ribs + 2 spars + top skin panel. Ribs joined to spars at FASTENED mates; skin panel bonded to rib top flanges.
   - **Landing gear:** Oleo strut (CYLINDRICAL) + truck assembly (REVOLUTE) + wheel (REVOLUTE). Simplified 3-part mechanism.
   - **Engine nacelle:** Outer shell (Domain C) + inner duct (Domain C) + forward frame + aft frame. 4-part assembly.
   - **Avionics bay bracket:** 3 mounting brackets (Domain A) + equipment tray (Domain A). Fastened mates.
   - **Hydraulic manifold:** Manifold body (Domain B, multiple intersecting bores) + end caps × 2 + fitting adapters × 4. 7-part assembly.

2. For each assembly: document the prompt that generated it, the generated code (with any edits), the STEP file, and a GLB render. Save to `output/demos/aerospace/`.

3. Identify SOLIDWORKS import fidelity for each: export to `.x_t` (T2-09) and verify round-trip accuracy.

4. Research SBIR solicitations: visit `www.sbir.gov/solicitations`. Search DoD (Air Force, Navy, DARPA) for solicitations mentioning CAD automation, AI-assisted design, digital engineering, or Model-Based Engineering (MBE). Current FY2026 solicitations are relevant.

5. Draft a SBIR Phase I application at `output/sbir-phase1-draft.md`:
   - Technical Objective: "Develop an assembly-first text-to-CAD generation system for aerospace structures, enabling Model-Based Engineering workflows from natural language design specifications."
   - Innovation: First system to generate multi-part aerospace assemblies with typed kinematic constraints from natural language.
   - Technical Approach: agentic LLM + OCCT constraint solver + aerospace-specific generation strategies.
   - Commercial Application: Engineering design automation for defense contractors (Boeing, Lockheed, Raytheon, BAE).
   - Phase I Deliverable: Working prototype demonstrated on 5 aerospace assembly types.

**Files:** `output/demos/aerospace/`, `output/sbir-phase1-draft.md`  
**Dependencies:** T1-01 (constraint assembly), T2-09 (Parasolid export)  
**Effort:** 1–2 weeks  
**Success criteria:** 5 demo assemblies render correctly and import into SOLIDWORKS. SBIR application draft reviewed and submitted to an appropriate solicitation.

---

### ✅ T2-14: Gemini Model String Centralization

**GAP FOUND:** P0-01 audit confirmed all 5 files (`agents.py`, `main.py`, `evaluation.py`, `retrieval.py`, `strategies.py`) hardcode Gemini model strings. `backend/config.py` now defines `GEMINI_MODEL_HEAVY = "gemini-3-flash-preview"` and `GEMINI_MODEL_LITE = "gemini-3.1-flash-lite-preview"`, but none of the existing files import from it. A model rename requires 5 manual edits instead of 1.

**What it is:** Update all model-string sites in the codebase to import from `config.py`. This is the simplest correctness fix in the codebase — one hour of mechanical refactoring with zero logic change.

**Implementation steps:**

1. `grep -rn '"gemini-' backend/` to find every hardcoded model string with exact line numbers.

2. In each file, add at top: `from config import GEMINI_MODEL_HEAVY, GEMINI_MODEL_LITE` and replace every hardcoded string with the appropriate constant. The mapping is: `"gemini-3-flash-preview"` → `GEMINI_MODEL_HEAVY`, `"gemini-3.1-flash-lite-preview"` → `GEMINI_MODEL_LITE`.

3. In `main.py` line 68 (the global `genai.configure` call), ensure the configure call itself still runs — don't remove it, just keep it; the key comes from the env var, not the model string.

4. Run `python -m pytest backend/tests/ -q` to confirm nothing broken.

5. Verify: `grep -rn '"gemini-' backend/` returns zero hits.

**Files:** `backend/agents.py`, `backend/main.py`, `backend/evaluation.py`, `backend/retrieval.py`, `backend/strategies.py`, `backend/config.py`  
**Dependencies:** None  
**Effort:** 0.5 days  
**Success criteria:** Zero hardcoded Gemini model strings in the codebase. Changing `GEMINI_MODEL_HEAVY` in `config.py` updates the model used by all agents.

---

### ✅ T2-15: Airfoil Pipeline Phase 2 — End-to-End Validation

**GAP FOUND:** `_run_aero_stage()` is already implemented in `main.py` (lines 164–232) and `aero_report` is wired into the `/generate` response payload. However: (a) the function has never been tested against a live XFOIL binary end-to-end, (b) the production Dockerfile has no `xfoil` package, (c) CI runs skip all Group B tests because `MIRUM_XFOIL_BIN` is unset.

**What it is:** Validate the full aero pipeline against a live XFOIL binary; add XFOIL to the production Docker image; wire `mock_xfoil` into CI so Group B tests always run.

**Implementation steps:**

1. **Windows validation (manual):** On the Windows dev machine with `T2CIII\bin\xfoil.exe` installed, run the `/generate` endpoint with a Domain D prompt that specifies a NACA airfoil (e.g., `"Generate a NACA 2412 wing section, chord 300mm, Re=2e6, alpha 0–15°"`). Verify that the response JSON contains a populated `aero_report` field with non-null `Cl`, `Cd`, `stall_alpha`. Log the raw XFOIL stdout for debugging.

2. **PolarReport schema validation:** Confirm that `_run_aero_stage`'s output round-trips through `PolarReport.model_validate()` without loss. Check that `aero_report` in the SSE payload serialises cleanly to JSON (no numpy arrays, no non-serialisable floats like `nan` — replace `nan` with `null`).

3. **Dockerfile update:** In `Dockerfile` (or `docker-compose.yml`), add:
   ```dockerfile
   RUN apt-get update && apt-get install -y xfoil && apt-get clean && rm -rf /var/lib/apt/lists/*
   ```
   Verify with `docker exec <container> xfoil <<< "QUIT"`.

4. **CI mock integration:** In CI (GitHub Actions or equivalent), set `MIRUM_XFOIL_BIN` to the path of `analysis/tests/mock_xfoil` (the shebang executable created in Phase 1). This unblocks all 7 Group B golden-value tests. Add to `.github/workflows/ci.yml`:
   ```yaml
   env:
     MIRUM_XFOIL_BIN: analysis/tests/mock_xfoil
   ```

5. **NaN guard:** In `_run_aero_stage`, wrap the `PolarReport` construction with a nan-to-null sanitizer:
   ```python
   import math
   def _sanitize_floats(obj):
       if isinstance(obj, float) and math.isnan(obj): return None
       if isinstance(obj, dict): return {k: _sanitize_floats(v) for k,v in obj.items()}
       if isinstance(obj, list): return [_sanitize_floats(v) for v in obj]
       return obj
   ```

6. **Integration test:** Add `backend/tests/test_aero_stage.py` — mocks XFOIL binary, runs `_run_aero_stage` with a mock STEP file + NACA spec, asserts `PolarReport` has expected keys and non-null `stall_alpha`.

**Files:** `backend/main.py`, `Dockerfile`, `.github/workflows/ci.yml`, `backend/tests/test_aero_stage.py`  
**Dependencies:** T2-13 (aerospace demos — use demo STEP files for validation)  
**Effort:** 2–3 days  
**Success criteria:** `/generate` with Domain D NACA prompt returns populated `aero_report`. Group B tests run in CI (0 skips). `docker build` produces image with working `xfoil`. NaN values serialise as `null`.

---

### ✅ T2-16: Airfoil Pipeline Phase 3 — Frontend Polar Display [2026-05-14]

**GAP FOUND:** The `aero_report` field is populated in the `/generate` SSE response payload but the Streamlit frontend (`frontend/app.py`) does not display it. Aerodynamic results are silently dropped on the UI side.

**What it is:** Add a collapsible "Aerodynamic Report" panel to the Streamlit sidebar / results section that plots Cl, Cd, and L/D vs. α and annotates the stall point. Display only for Domain D outputs where `aero_report` is non-null.

**Implementation steps:**

1. In `frontend/app.py`, after the GLB viewer section, add:
   ```python
   if result.get("aero_report"):
       aero = result["aero_report"]
       with st.expander("✈️ Aerodynamic Report", expanded=True):
           import plotly.graph_objects as go
           alpha = aero["alpha"]
           fig = go.Figure()
           fig.add_trace(go.Scatter(x=alpha, y=aero["Cl"], name="Cl", line=dict(color="royalblue")))
           fig.add_trace(go.Scatter(x=alpha, y=aero["Cd"], name="Cd", line=dict(color="tomato"), yaxis="y2"))
           fig.add_trace(go.Scatter(x=alpha, y=aero["L_D"], name="L/D", line=dict(color="seagreen", dash="dot"), yaxis="y3"))
           if aero.get("stall_alpha") is not None:
               fig.add_vline(x=aero["stall_alpha"], line_dash="dash", line_color="orange",
                             annotation_text=f"Stall α={aero['stall_alpha']}°")
           fig.update_layout(title=f"NACA {aero['naca_code']} Polar  Re={aero['reynolds']:.2e}",
                             xaxis_title="α (deg)", yaxis_title="Cl")
           st.plotly_chart(fig, use_container_width=True)
           col1, col2, col3 = st.columns(3)
           col1.metric("Cl max", f"{aero.get('Cl_max', 'n/a'):.3f}" if aero.get('Cl_max') else "n/a")
           col2.metric("Stall α", f"{aero.get('stall_alpha', 'n/a')}°" if aero.get('stall_alpha') else "n/a")
           col3.metric("Converged pts", aero.get("converged_count", "n/a"))
   ```

2. Ensure `plotly` is in `requirements.txt` / `pyproject.toml`. It likely already is for the GLB viewer.

3. Add a download button for the raw polar CSV:
   ```python
   import pandas as pd, io
   df = pd.DataFrame({"alpha": aero["alpha"], "Cl": aero["Cl"], "Cd": aero["Cd"], "L_D": aero["L_D"]})
   st.download_button("Download polar CSV", df.to_csv(index=False), "polar.csv", "text/csv")
   ```

4. Add polar path link if `aero["polar_path"]` is non-null — lets the user download the raw XFOIL polar file.

**Files:** `frontend/app.py`, `requirements.txt`  
**Dependencies:** T2-15 (Phase 2 validation — need working `aero_report` in response)  
**Effort:** 1–2 days  
**Success criteria:** Domain D generation displays Cl/Cd/L/D polar chart with stall annotation. Chart only appears when `aero_report` is non-null (no error for Domain A/B/C outputs). Polar CSV downloadable.

---

### ✅ T2-17: Domain D Eval Harness Prompts [2026-05-14]

**GAP FOUND:** The eval harness regression suite (`backend/eval_harness/`) has no prompts with `regression_for: ["D"]`. Domain D (aerospace) has the richest implementation (OML Master Modeling, airfoil functions, XFOIL pipeline) but is untested by the automated regression gate. Any change to `strategies.py` Domain D prompts, `cad_utils.make_naca_wire()`, or `analysis/airfoil.py` could silently regress without detection.

**What it is:** Add at least 8 Domain D prompts to `backend/eval_harness/prompts.json` (or the CSV source), tagged `regression_for: ["D"]`, covering the core Domain D capabilities.

**Implementation steps:**

1. Add the following prompts (minimum set) to the eval harness corpus:

   | id | description | regression_for |
   |----|-------------|---------------|
   | D-001 | NACA 0012 wing section, chord 200mm, span 500mm, no sweep | ["D"] |
   | D-002 | NACA 2412 cambered wing, chord 300mm, AoA sweep 0–15° | ["D"] |
   | D-003 | Wing + 3 internal ribs, NACA 0015, chord 400mm | ["D"] |
   | D-004 | Wing + rib + spar cross-section, FASTENED mate | ["D"] |
   | D-005 | Symmetric NACA 0009 control surface, 150mm chord | ["D"] |
   | D-006 | Multi-part wing assembly: skin + 2 ribs + spar | ["D"] |
   | D-007 | Wing with explicit Re=1e6, Ncrit=9, alpha 0–18° aerodynamic sweep | ["D"] |
   | D-008 | Fuselage cross-section (NACA 0018 thickness profile, 2m length) | ["D"] |

2. For each prompt, define the pass criteria in the harness schema: `expected_domain: "D"`, `min_converged_alpha` (for prompts with aerodynamic sweep), `required_naca_code`.

3. Run `python backend/tests/test_prompts_schema.py` to verify new prompt ids follow the schema contract (`id` prefix matches `domain`, `regression_for` is `list[str]` from {A,B,C,D,E}).

4. Add `D` to the worktree-merge-gate.md `--regression-for` map (Worktree D or a new Worktree G for airfoil work).

**Files:** `backend/eval_harness/prompts.json` (or CSV source), `backend/tests/test_prompts_schema.py`, `.claude/rules/worktree-merge-gate.md`  
**Dependencies:** T2-15 (Phase 2 validation — need working aero pipeline before prompts can be evaluated end-to-end)  
**Effort:** 1–2 days  
**Success criteria:** 8 Domain D prompts pass schema tests. `python -m eval_harness.runner --regression-for D` runs successfully and reports pass/fail for all 8 prompts. Schema tests pass (no duplicate ids, correct prefix format).

---

## Tier 3 — Research Bets

### T3-01: Semantic Mate Bootstrapping Pipeline via OntoBREP ✅ IMPLEMENTED

**What it is:** Build a Semantic Mate classifier that takes two B-rep parts and their joint geometry (from Fusion 360 Gallery annotations) and classifies the joint into the OntoBREP type hierarchy (BallBearingMate, HingeMate, BoltedFlangeConnection, PressureFitMate, etc.). Use this classifier to auto-annotate the Fusion 360 Gallery with Semantic Mate labels — creating high-quality training data for assembly planning with full kinematic and manufacturing intent.

**Why it matters:** JoinABLe predicts *where* parts connect; Semantic Mate predicts *what kind* of connection it is. The Semantic Mate vocabulary carries full design intent (kinematic + manufacturing) in a single symbolic type — the highest-density assembly representation. Training the Planner on Semantic Mate labels rather than raw mate types would produce assemblies with correct functional semantics, not just geometric correctness.

**Implementation steps:**

1. Obtain the OntoBREP ontology: download from the fortiss/Semantic Mates GitHub (or paper supplementary materials). Parse the OWL file using `owlready2`:
   ```python
   from owlready2 import get_ontology
   onto = get_ontology("onto_brep.owl").load()
   mate_types = list(onto.search(type=onto.SemanticMate))
   ```
   Build a type hierarchy tree with feature discriminators for each leaf type.

2. Feature engineering for the classifier: for each joint in Fusion 360 Gallery (JoinABLe annotations include B-rep geometry + joint origin):
   - Extract geometric features of the two mating faces: face type (plane/cylinder/cone/sphere/torus), face area ratio, normal vector alignment, distance between face centroids.
   - Extract topology features: edge count, vertex count, shared edge existence.
   - Encode as a fixed-length feature vector.

3. Train a GNN-based classifier (following JoinABLe's B-rep GNN architecture) with the Semantic Mate type as the label. Use the Fusion 360 Gallery's joint annotations as the training signal (JoinABLe predicts pairwise joints; add Semantic Mate type as a second prediction head).

4. Validate: does the classifier agree with human-annotated Semantic Mate types on 200 manually labeled joints? Target: >70% accuracy (the problem is harder than JoinABLe's binary joint detection).

5. If validation passes: apply the classifier to all 32,148 Fusion 360 Gallery joint annotations, producing Semantic Mate labels for each. This becomes the training set for Planner fine-tuning with Semantic Mate output.

6. Update the Planner schema to optionally output `semantic_mate_type` alongside `mate_type`. Fine-tune the Planner on (assembly_description, assembly_manifest_with_semantic_mates) pairs.

**Files:** new `research/semantic_mate_classifier.py`, training pipeline, Planner schema update  
**Dependencies:** T1-01 (constraint assembly), T2-07 (ABC assembly data), Fusion 360 Gallery dataset access  
**Effort:** 2–4 months (GNN training, validation, annotation pipeline)  
**Success criteria:** Classifier achieves >70% accuracy on manually labeled validation set. Fusion 360 Gallery auto-annotated with Semantic Mate types. Planner trained on semantic mate labels achieves higher LVM Score on kinematic assemblies than mate-type-only Planner.

---

### T3-02: JoinABLe GNN as Post-Machinist Geometric Cross-Check ✅ IMPLEMENTED

**What it is:** After all Machinists complete, run a JoinABLe-style GNN over the compiled B-rep face/edge graphs to independently predict which face pairs are likely joints. Compare these bottom-up geometric predictions against the Planner's top-down text-derived mates. Divergences trigger a Planner verification query.

**Why it matters:** The current pipeline has no bottom-up geometric grounding: the Planner specifies mates from text, but if the generated part geometries don't actually support the specified mate (e.g., the specified bore doesn't exist at the predicted face), the error isn't caught until the assembler fails. A geometric cross-check catches mismatches between planned mates and actual geometry.

**Implementation steps:**

1. Implement a lightweight B-rep face graph encoder for compiled OCCT shapes:
   ```python
   def extract_face_graph(shape: TopoDS_Shape) -> nx.Graph:
       """Convert OCCT B-rep to a NetworkX graph with face/edge features."""
       G = nx.Graph()
       face_explorer = TopExp_Explorer(shape, TopAbs_FACE)
       while face_explorer.More():
           face = face_explorer.Current()
           # Node features: surface type, area, normal, centroid
           G.add_node(face_hash(face), **face_features(face))
           face_explorer.Next()
       # Edge features: shared edge type, curvature
       return G
   ```

2. Train a GNN joint predictor (can reuse JoinABLe's architecture from the paper, which is open-sourced in the Fusion 360 Gallery repository) on Fusion 360 Gallery part pairs with known joint annotations. Target: replicate JoinABLe's 79.53% pairwise accuracy.

3. After Machinists complete, run the GNN on all part pairs. For each pair, the GNN returns: predicted face pair + confidence score.

4. Compare GNN predictions against Planner's `MatingRule` specifications. For each mate:
   - If GNN and Planner agree (same face pair, compatible constraint type): log `mate_confidence: high`.
   - If GNN predicts a different face pair: log `mate_confidence: low`, trigger a Planner verification call: "The geometry suggests the best connection between Part A and Part B is face X↔Y, but you specified face A↔B. Please verify or revise."
   - If GNN predicts no likely joint between two parts the Planner mates: log `mate_confidence: very_low`.

5. Use `mate_confidence` scores to prioritize which assemblies need human review in the evaluation pipeline.

**Files:** new `research/joinable_gnn.py`, `assembler.py`, `telemetry.py`  
**Dependencies:** T1-01 (constraint assembly — need compiled parts), T3-01 (Semantic Mate — optional but complementary)  
**Effort:** 2–3 months  
**Success criteria:** GNN achieves >75% face pair prediction accuracy on Fusion 360 Gallery test set. At least 10 examples where GNN correctly catches a Planner mate specification error on real Mirum outputs.

---

### T3-03: SketchGraphs 2D Constraint Pre-Training for Mate Prediction ✅ IMPLEMENTED

**What it is:** Pre-train a small transformer on the SketchGraphs 2D constraint prediction task (15M 2D CAD sketches → geometric constraint classification). Use this pre-trained "constraint reasoner" as the initialization for 3D mate prediction (T3-01, T3-02). The hypothesis: 2D constraint prediction is structurally similar to 3D mate prediction, and 15M 2D examples provide better initialization than random weights for the smaller 3D dataset.

**Why it matters:** The Fusion 360 Gallery provides 32,148 3D joint annotations — small for training a GNN from scratch. SketchGraphs provides 15M 2D annotations. If the structural similarity holds, pre-training on SketchGraphs provides dramatically better initialization for 3D constraint prediction, improving accuracy with the available 3D data.

**Implementation steps:**

1. Download the SketchGraphs dataset: `https://github.com/PrincetonLIPS/SketchGraphs`. The dataset provides constraint graphs over 2D geometric entities (lines, arcs, circles) with constraint types: coincident, parallel, perpendicular, tangent, equal, fixed, horizontal, vertical.

2. Design the pre-training task: given a 2D sketch with N geometric entities, predict which entity pairs are constrained and what type. Formulate as a binary edge classification task on a fully connected entity graph.

3. Train a message-passing GNN (GIN or MPNN architecture) on SketchGraphs. Node features: entity type (line/arc/circle), length/radius, endpoint coordinates. Edge labels: constraint type or "unconstrained."

4. Transfer to 3D: initialize the 3D mate prediction GNN (T3-02) with the SketchGraphs pre-trained weights (using the first few layers which capture geometric relationship patterns). Fine-tune on Fusion 360 Gallery joint annotations.

5. Ablation study: compare GNN accuracy with and without SketchGraphs pre-training on the Fusion 360 Gallery test set. If improvement is < 3%, the transfer learning hypothesis is false — don't invest further.

**Files:** new `research/sketchgraphs_pretrain.py`, shared with T3-02's GNN  
**Dependencies:** T3-02 (JoinABLe GNN — needs the 3D GNN architecture to pre-train for)  
**Effort:** 2–3 months  
**Success criteria:** SketchGraphs pre-trained GNN achieves ≥ 5% higher accuracy on Fusion 360 Gallery test set compared to GNN trained from scratch. Ablation study documented.

---

### T3-04: RLAIF Fine-Tuning for Machinist (Full RL Pipeline) ✅ IMPLEMENTED

**What it is:** After SFT fine-tuning (T2-11), apply Reinforcement Learning from AI Feedback (RLAIF) to the Machinist model. Use a hybrid reward signal: execution reward (binary — does the CadQuery script compile and produce a valid solid?) + AI preference reward (Gemini or GPT-4V rates geometric quality). This enables the Machinist to improve beyond the ceiling of the SFT training data.

**Why it matters:** SFT trains the model to imitate training examples. RLAIF trains it to maximize quality as judged by execution success and geometric preference — which can exceed the quality ceiling of available training data. t2cIII's `telemetry.py` already produces the (code, success_status) pairs needed for execution reward with no new infrastructure.

**Implementation steps:**

1. Set up the reward model:
   - **Execution reward (free):** `reward = 1.0 if success_status else -1.0`. Source: existing `telemetry.py` records.
   - **AI preference reward:** For each generated script, render the compiled geometry (if valid) and ask Gemini: "On a scale of 1–5, how well does this 3D model match the description: [prompt]?" Normalize to [-1, 1]. Cost: ~$0.01 per rendered evaluation.
   - **Hybrid reward:** `total_reward = 0.7 * execution_reward + 0.3 * preference_reward`.

2. Set up the PPO training loop using `trl`'s `PPOTrainer`:
   ```python
   from trl import PPOTrainer, PPOConfig
   
   ppo_config = PPOConfig(
       model_name="path/to/sft_machinist",
       learning_rate=1.4e-5,
       batch_size=256,
       mini_batch_size=64,
   )
   trainer = PPOTrainer(config=ppo_config, model=model, ref_model=ref_model,
                        tokenizer=tokenizer, dataset=prompt_dataset)
   ```

3. Online data collection: at each training step, generate N scripts per prompt using the current policy, compile each via OCCT (the Critic Loop), compute rewards, update policy.

4. Implement **macro-action credit assignment** (from MA-RLHF research): instead of token-level reward attribution, segment CadQuery scripts into method-chain units (`.circle(r)`, `.extrude(h)`, `.constrain(...)`) using AST parsing. Assign the reward at the macro-action level during PPO.
   ```python
   def segment_into_macro_actions(code: str) -> list[str]:
       """Split CadQuery method chain into individual operations."""
       tree = ast.parse(code)
       # Walk method calls in the chain and extract each .method(args) segment
       return [ast.unparse(node) for node in extract_method_chain_nodes(tree)]
   ```

5. Monitor KL divergence from the SFT reference model (standard PPO practice) to prevent reward hacking.

6. Evaluate: after RLAIF training, compare invalidity rate and LVM Score against SFT-only baseline on the held-out test set.

**Files:** new `training/rlaif_machinist.py`, `telemetry.py`, `compiler.py`  
**Dependencies:** T2-11 (SFT fine-tuned Machinist — RLAIF starts from SFT), T1-07 (LVM Score for preference reward)  
**Effort:** 2–3 months  
**Success criteria:** RLAIF Machinist achieves lower invalidity rate than SFT-only Machinist on test set. LVM Score improves by ≥ 0.3 points. KL divergence from reference model remains stable (< 5 nats).

---

### T3-05: AST-Aware Grammar-Constrained Decoding ✅ IMPLEMENTED

**What it is:** Implement grammar-constrained decoding for the fine-tuned Machinist model using CadQuery's method-chain grammar as a context-free grammar. Only tokens that are valid continuations of the current CadQuery method chain are permitted at each decoding step. This eliminates structural CadQuery errors (invalid method calls, wrong return types, missing `.close()`) at generation time.

**Why it matters:** Structural errors (calling a method that doesn't exist on the current Workplane object) are caught by the Critic Loop, but they consume retry budget. Preventing them at generation time would reduce the Critic Loop retry rate. This is only worth implementing if structural errors remain a significant fraction of failures after T2-11 (SFT fine-tuning) — the SFT may already largely eliminate structural errors.

**Implementation steps:**

1. First: verify necessity. After T2-11 deployment, run T1-08 (failure classification) on 1,000 generations. If structural errors (wrong method call, missing operation) represent < 5% of failures, skip this task. If they represent > 20%, proceed.

2. Define the CadQuery method-chain grammar as a context-free grammar:
   ```
   CadQuery_Script ::= Import Assignment* ExpressionStatement
   Workplane ::= "cq.Workplane(" Axis ")"
   ChainedCall ::= Workplane | ChainedCall "." Method "(" Args ")"
   Method ::= "circle" | "rect" | "box" | "cylinder" | "extrude" | "revolve" |
               "fillet" | "chamfer" | "shell" | "cut" | "union" | "loft" | ...
   ```
   Use CadQuery's source code to extract the full method signature list per Workplane state.

3. Implement constrained decoding using Outlines (`pip install outlines`):
   ```python
   import outlines
   
   cadquery_grammar = """
   ... (full grammar definition) ...
   """
   model = outlines.models.transformers("path/to/sft_machinist")
   generator = outlines.generate.cfg(model, cadquery_grammar)
   script = generator(prompt)
   ```

4. Benchmark: measure generation speed (constrained decoding is 2–10× slower than unconstrained). If latency > 30 seconds per part, the UX cost outweighs the quality benefit.

5. If latency is acceptable: deploy as an option for Domain C (organic, where structural errors from complex spline chains are more frequent) while keeping unconstrained generation for Domain A/B.

**Files:** new `inference/constrained_decoding.py`, `agents.py`  
**Dependencies:** T2-11 (fine-tuned Machinist — must have the model to apply constrained decoding to), T1-08 (failure classification — needed to verify necessity)  
**Effort:** 1–2 months  
**Success criteria:** Constrained generation produces 0% structural CadQuery errors. Generation latency < 20 seconds per part. Overall failure rate (including semantic errors) decreases vs. unconstrained.

---

### T3-06: Domain E — Sheet Metal and Thin-Wall Classification ✅ IMPLEMENTED

**What it is:** Add a Domain E classification for sheet metal and thin-wall parts, with domain-specific design rules (minimum bend radius, flange lengths, K-factor), a new strategy system prompt, and a `make_sheet_metal_bracket()` family of `cad_utils.py` functions. The Domain Classifier learns to identify sheet metal prompts and route to the specialized Machinist strategy.

**Why it matters:** Sheet metal is a major manufacturing category (enclosures, brackets, panels, chassis) that the current Domain A/B routing handles poorly because sheet metal design rules (bend radius, flat pattern, K-factor) are fundamentally different from machined part design rules. Adding a dedicated domain improves output quality for an important part of the ICP's workflow.

**Implementation steps:**

1. Write the Domain E strategy system prompt in `strategies.py`:
   ```python
   DOMAIN_E_PROMPT = """You are generating a sheet metal part using CadQuery.
   
   Sheet metal design rules (follow these strictly):
   - Minimum bend radius: 1× material thickness for soft materials, 2× for hard
   - Minimum flange length: 2× material thickness + bend radius
   - Hole-to-edge distance: minimum 2× material thickness
   - K-factor for steel: 0.33 (inside setback calculation)
   - Standard gauges: 0.8mm (20ga), 1.0mm (18ga), 1.2mm (16ga), 1.5mm (14ga)
   
   In CadQuery, generate sheet metal as thin extruded profiles. For bent features,
   use .shell() or stepwise extrusions representing the flat-pattern-folded geometry.
   Note: CadQuery does not natively support flat-pattern unfolding — generate the
   folded final shape."""
   ```

2. Update `classifier.py` to add Domain E to the classification schema. Add examples:
   - Domain E: "a sheet metal bracket", "a steel enclosure panel", "a bent plate with flanges", "a PCB mounting tray"
   - Add 5–10 labeled examples to the few-shot classifier prompt.

3. In `cad_utils.py`, add sheet metal utility functions:
   ```python
   def make_bent_bracket(
       material_thickness: float,
       base_length: float,
       base_width: float,
       flange_height: float,
       bend_radius: float,
       n_base_holes: int,
       hole_diameter: float
   ) -> cq.Workplane:
       """Standard L-bracket from sheet metal with bend relief."""
   
   def make_enclosure_panel(
       width: float, height: float,
       material_thickness: float,
       flange_depth: float,
       cutout_positions: list[tuple[float, float, float, float]]  # (x, y, w, h)
   ) -> cq.Workplane:
       """Sheet metal panel with optional rectangular cutouts."""
   ```

4. Collect 50–100 sheet metal training examples for the Machinist SFT dataset (T2-05): use the Domain E strategy prompt + Gemini to generate (description, code) pairs, validate each with OCCT.

5. Update the evaluation suite (T1-07, T1-09) to include Domain E in stratified reporting.

**Files:** `classifier.py`, `strategies.py`, `cad_utils.py`, training pipeline  
**Dependencies:** T2-01 (features vocabulary), T2-10 (cad_utils expansion), T2-11 (SFT training — domain E pairs needed in dataset)  
**Effort:** 3–6 weeks  
**Success criteria:** Classifier correctly routes "sheet metal bracket" to Domain E. Domain E generates thin-walled profiles following design rules. Bend radius and flange length constraints validated against prompt specifications.

---

### T3-07: Gaussian Curvature Analysis for Domain C Quality ✅ IMPLEMENTED

**What it is:** After Domain C compilation, compute the Gaussian curvature distribution over the generated surface using OCCT's `BRepLProp_SLProps` API. Flag regions with unexpectedly negative curvature (saddle shapes on surfaces described as convex) or zero curvature on surfaces described as continuously curved.

**Why it matters:** Gaussian curvature classifies surface regions as bowl-shaped (positive), saddle-shaped (negative), or flat (zero). For ergonomic parts, saddle regions are usually design errors — they create uncomfortable grip surfaces and unexpected reflections. This differentiates Mirum's organic generation quality beyond what any competitor currently evaluates.

**Implementation steps:**

1. Implement `compute_gaussian_curvature(shape: TopoDS_Shape, n_samples=50) -> dict`:
   ```python
   from OCC.Core.BRepLProp import BRepLProp_SLProps
   from OCC.Core.BRep import BRep_Tool
   from OCC.Core.TopExp import TopExp_Explorer
   from OCC.Core.TopAbs import TopAbs_FACE
   
   def compute_gaussian_curvature(shape, n_samples=50):
       results = {"min": float('inf'), "max": float('-inf'),
                  "negative_regions": [], "zero_regions": []}
       face_exp = TopExp_Explorer(shape, TopAbs_FACE)
       while face_exp.More():
           face = face_exp.Current()
           surface, u1, u2, v1, v2 = BRep_Tool.Surface(face), *BRep_Tool.Range(face)
           for i in range(n_samples):
               u = u1 + (u2 - u1) * i / n_samples
               for j in range(n_samples):
                   v = v1 + (v2 - v1) * j / n_samples
                   props = BRepLProp_SLProps(surface, u, v, 2, 1e-6)
                   if props.IsNormalDefined():
                       k1, k2 = props.MinCurvature(), props.MaxCurvature()
                       gaussian = k1 * k2
                       # flag unexpectedly negative or flat regions
           face_exp.Next()
       return results
   ```

2. In the Critic Loop, for Domain C parts: after successful compilation, run Gaussian curvature analysis. If negative-curvature regions are found on surfaces described as convex (cross-reference against the part description using Gemini), inject diagnostic feedback: "The surface at face [N] shows a saddle shape (negative Gaussian curvature) in a region described as convex. Replace the lofting strategy with a simpler extrusion-and-shell approach for this region."

3. Add `gaussian_curvature_analysis` to telemetry for Domain C parts.

4. Add Gaussian curvature pass rate to the Domain C section of the evaluation harness.

**Files:** new `geometry_analysis.py`, `critic.py`, `telemetry.py`  
**Dependencies:** T2-04 (G2 continuity — both are Domain C quality checks and should be co-located)  
**Effort:** 2–4 weeks  
**Success criteria:** Gaussian curvature computed for all Domain C parts. At least 5 examples where curvature feedback causes the Critic to produce a geometrically cleaner surface. Curvature pass rate tracked.

---

### T3-08: Tolerance and PMI Validation for Manufacture-Ready Output ✅ IMPLEMENTED

**What it is:** Validate that generated geometry includes appropriate Product and Manufacturing Information (PMI) annotations — tolerances, surface finish callouts, GD&T — when the prompt specifies manufacturing specifications. Store PMI as OCCT attributes on B-rep faces and include in STEP export.

**Why it matters:** Expert-level prompts specify tolerances ("H7 fit for a 608 bearing", "Ra 0.8 surface finish"). The generated CadQuery may produce correctly-sized geometry but without tolerance annotations — making the output geometrically correct but not manufacture-ready. PMI support would be a significant step toward production-grade CAD output for the enterprise ICP.

**Implementation steps:**

1. Extend `schemas.py` with PMI annotations:
   ```python
   class PMIAnnotation(BaseModel):
       face_selector: str     # CadQuery selector for the annotated face
       tolerance_type: str    # "linear", "angular", "surface_finish", "gdt"
       value: str             # "H7", "±0.05", "Ra 0.8", "⊙ ⌀0.1 A B"
   
   class Part(BaseModel):
       # ... existing fields ...
       pmi_annotations: list[PMIAnnotation] = []
   ```

2. Update the Machinist system prompt (Expert-level, from T2-05): "If the prompt specifies tolerances, surface finish, or GD&T, include PMI annotations in the output JSON. For each annotated feature, specify the face selector and annotation value."

3. In the Assembler/post-processor, attach PMI attributes to OCCT B-rep faces using `TDocStd_Document` and XCAF attributes (ISO 10303-242 PMI representation):
   ```python
   from OCC.Core.TDocStd import TDocStd_Document
   from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool
   # ... XCAF PMI attribute attachment
   ```

4. Export STEP files with PMI included using `STEPCAFControl_Writer` instead of `STEPControl_Writer`.

5. Add a PMI completeness check to evaluation: for prompts with explicit tolerance callouts, verify that the STEP file contains corresponding PMI annotations. Report PMI completeness rate.

**Files:** `schemas.py`, `agents.py`, `assembler.py` / `postprocessing.py`, STEP exporter  
**Dependencies:** T2-05 (Expert-level prompts in SFT dataset — needed for training), T2-09 (CAD Exchanger — Parasolid PMI support is better)  
**Effort:** 2–3 months  
**Success criteria:** Expert-level prompts with tolerance callouts produce STEP files with PMI annotations. PMI opens correctly in SOLIDWORKS inspection tool. PMI completeness rate tracked in evaluation.

---

### ✅ T3-09: VLM Zebra Rendering Pipeline (Unblocks Two Production Stubs) [2026-05-14]

**GAP FOUND:** Two functions in the codebase are `STUB / no-op` pending a rendering pipeline:
- `backend/continuity_check.py` line 247: `vlm_g2_check()` — returns `passed=True` unconditionally
- `backend/geometry_analysis.py` line 357: `vlm_curvature_check()` — returns placeholder result

Both stubs share the same blocker: there is no code path that takes a STEP file → zebra-stripe rendered image → Gemini Vision API. This task builds that shared rendering utility so both stubs can be completed.

**What it is:** Build a `render_zebra(step_path) -> image_path` pipeline using either (a) a headless OpenCASCADE/VTK renderer, (b) a lightweight Three.js offscreen renderer via Node.js, or (c) screenshot of the existing GLB viewer with zebra shaders enabled. Then complete both VLM stubs using Gemini Vision.

**Why it matters:** G2 continuity and gaussian curvature checks are currently no-ops — every part trivially "passes." Domain C (organic parts) depends entirely on these checks for surface quality. The existing Critic Loop integration calls `curvature_check_for_critic()` and `vlm_g2_check()` but their results are meaningless until the rendering pipeline exists.

**Implementation steps:**

1. **Renderer selection (Day 1 — evaluate options):**
   - Option A: `pythonocc-core` with `OCC.Core.V3d` off-screen rendering + `OCC.Core.Graphic3d` zebra material. Requires X11 in Docker (or VirtualFramebuffer). Complexity: high.
   - Option B: Export STEP → GLB (existing pipeline), then use `pyrender` + custom zebra fragment shader. Complexity: medium. Best choice if `pyrender` is already installed.
   - Option C: Puppeteer / Playwright headless Chrome with Three.js zebra shader, save screenshot. Complexity: medium but adds Node.js dependency.
   - **Recommended:** Option B — reuse the existing STEP→GLB pipeline + `pyrender` + zebra GLSL shader.

2. **Implement `render_zebra(step_path, output_png_path, *, n_stripes=20)`:**
   ```python
   import pyrender, trimesh, numpy as np
   from OCC.Core.STEPControl import STEPControl_Reader
   
   def render_zebra(step_path: str, output_png_path: str, *, n_stripes: int = 20) -> str:
       """Render STEP file with zebra-stripe reflection map. Returns path to PNG."""
       # Load via trimesh (STEP→mesh via OCC export)
       mesh = trimesh.load(step_path)
       # Apply zebra material: fragment shader alternates black/white based on
       # dot product of reflected normal with environment Y axis
       scene = pyrender.Scene(ambient_light=[0.1, 0.1, 0.1])
       mat = pyrender.MetallicRoughnessMaterial(metallicFactor=1.0, roughnessFactor=0.0)
       mesh_node = pyrender.Mesh.from_trimesh(mesh, material=mat)
       scene.add(mesh_node)
       camera = pyrender.PerspectiveCamera(yfov=np.pi / 3.0)
       scene.add(camera, pose=_look_at_pose(mesh))
       r = pyrender.OffscreenRenderer(800, 600)
       color, _ = r.render(scene)
       import imageio; imageio.imwrite(output_png_path, color)
       return output_png_path
   ```
   Note: a true zebra shader requires a custom GLSL fragment shader injected via pyrender's `CustomShaderCache`. See pyrender docs for fragment shader override. The n_stripes parameter controls stripe frequency.

3. **Complete `vlm_g2_check()` in `continuity_check.py`:**
   - Replace the stub body with:
     1. `render_zebra(step_path, tmp_png_path)`
     2. Load image as base64
     3. Send to Gemini Vision with the system prompt already documented in the stub (lines 250–256)
     4. Parse JSON response: `{"g2_pass": bool, "violation_description": str}`
     5. Return `G2CheckResult(passed=result["g2_pass"], method="vlm_zebra", feedback=result["violation_description"])`

4. **Complete `vlm_curvature_check()` in `geometry_analysis.py`:**
   - Replace stub body with:
     1. `render_zebra(step_path, tmp_png_path)` (reuse same rendering call)
     2. Send to Gemini Vision with the curvature analysis prompt already documented in the stub (lines 357–369)
     3. Parse structured output: `{"severity": 0–3, "regions": [...], "feedback": str}`
     4. Return `GaussianCurvatureResult(severity=severity, regions=regions, feedback=feedback, method="vlm_zebra")`

5. **Docker:** Add `pyrender` (headless-compatible) to requirements. Verify `pyrender.OffscreenRenderer` works in Docker without a display:
   ```dockerfile
   RUN pip install pyrender imageio imageio-ffmpeg
   ENV PYOPENGL_PLATFORM=osmesa
   ```

6. **Test:** Add `backend/tests/test_vlm_stubs.py` — with mocked Gemini Vision responses, verify that both `vlm_g2_check()` and `vlm_curvature_check()` no longer return stubs and instead parse mocked JSON responses.

**Files:** `backend/continuity_check.py`, `backend/geometry_analysis.py`, new `backend/rendering.py`, `requirements.txt`, `Dockerfile`  
**Dependencies:** T1-07 (GLB viewer — existing STEP→GLB pipeline can be reused for mesh loading), T3-07 (Gaussian curvature — defines the data structure being populated)  
**Effort:** 2–4 months (renderer evaluation + GLSL zebra shader + Vision API integration + Docker headless validation)  
**Success criteria:** `render_zebra()` produces visible zebra stripes on a NACA 0012 wing STEP file. `vlm_g2_check()` returns a non-trivial result (not always `passed=True`) on a surface with a known G2 discontinuity. `vlm_curvature_check()` returns severity > 0 on a saddle surface. Both checks active in the Critic Loop.

---

## Tier 4 — Subagent Architecture

> **Goal:** Break the monolithic `agents.py` (1,422 lines, 8 entangled functions, shared global state) into a proper subagent system. Each agent runs as an independent object with its own context window, communicates via structured `SubagentMessage` envelopes, can be upgraded independently via the model registry, and can be spawned in parallel where tasks are independent.
>
> **Current pain points:** Planner shares context with Machinist which shares with Critic — impossible to upgrade one model without touching all three. Machinist parts are generated sequentially even though they are independent. Telemetry spans are per-critic-attempt, not per-agent. A model rename requires coordinated edits across 5 files.
>
> **Token Efficiency Protocol** applies throughout. Status symbols: ⬜ pending · 🔄 in progress · ✅ done · ⚠️ blocked.

---

### ✅ T4-01: SubagentBase — Protocol, Message Schema, Context

```xml
<task id="T4-01" status="⬜" effort="3–5d" tier="4">
  <summary>Define SubagentBase ABC + SubagentMessage TypedDict + SubagentContext dataclass</summary>
  <why>Shared interface every subagent signs. Without it extractions diverge. Zero logic change — pure schema.</why>
  <files>
    <new>backend/subagents/__init__.py</new>
    <new>backend/subagents/base.py</new>
    <new>backend/subagents/messages.py</new>
  </files>
  <steps>
    <step n="1">SubagentMessage TypedDict:
      trace_id: str          # UUID4 — propagated across full pipeline
      parent_id: str | None  # spawning agent's trace_id
      agent_type: str        # "planner"|"machinist"|"critic"|"assembler"|"rea"|"clarifier"|"aero"
      payload: dict          # agent-specific structured output
      metadata: dict         # {model, temperature, tokens_in, tokens_out, latency_ms, timestamp}
    </step>
    <step n="2">SubagentContext dataclass:
      trace_id: str
      prompt: str
      domain: str
      manifest: AssemblyManifest | None = None
      flight_env: FlightEnvelope | None = None
      history: list[SubagentMessage] = field(default_factory=list)
      model_registry: ModelRegistry = field(default_factory=ModelRegistry.default)
    </step>
    <step n="3">SubagentBase ABC:
      @abstractmethod async def run(self, ctx: SubagentContext) -> SubagentMessage
      @property def agent_type(self) -> str   # must match SubagentMessage.agent_type literal
      @property def model(self) -> str         # reads from ctx.model_registry
    </step>
    <step n="4">Unit test: mock subagent passes ABC contract + mypy strict</step>
    <step n="5">SubagentMessage.to_json() / from_json() — clean round-trip, no numpy/nan</step>
  </steps>
  <success>ABC defined. Mock subagent passes mypy. Message round-trips JSON cleanly. 0 logic changes to existing agents.py.</success>
  <deps>none</deps>
</task>
```

---

### ✅ T4-02: Orchestrator / Router

```xml
<task id="T4-02" status="⬜" effort="1–2w" tier="4">
  <summary>Central Orchestrator dispatching to subagents via state machine; replaces inline main.py pipeline</summary>
  <why>main.py /generate currently has 80+ lines of imperative pipeline logic. Orchestrator separates routing from agent logic and enables parallel dispatch of independent stages.</why>
  <files>
    <new>backend/subagents/orchestrator.py</new>
    <modify>backend/main.py  ← /generate body → Orchestrator(ctx).run()</modify>
  </files>
  <steps>
    <step n="1">PipelineState enum:
      INIT → REA → CLARIFY → PLAN → MACHINE → CRITICIZE → ASSEMBLE → AERO → DONE | FAILED
    </step>
    <step n="2">Orchestrator.run(prompt, opts) → AsyncGenerator[SSEEvent]:
      1. Root span: trace_id=uuid4(), emit INIT SSE
      2. REASubagent → DesignRequirements
      3. If under-specified → ClarifierSubagent → clarification questions
      4. PlannerSubagent → AssemblyManifest
      5. asyncio.gather(*[MachinistSubagent().run(ctx_part) for part in manifest.parts])
      6. CriticSubagent per part (serial within part, parallel across parts)
      7. AssemblerSubagent → STEP + GLB
      8. If domain=="D" and airfoil_spec → AeroSubagent (parallel with assembler)
      9. Emit DONE SSE; return result dict
    </step>
    <step n="3">Replace main.py /generate body: Orchestrator(ctx).run()</step>
    <step n="4">Backward compat: /run endpoint keeps direct run_machinist() call (single-part quick mode unchanged)</step>
    <step n="5">Integration test: full /generate pipeline routed through Orchestrator → SSE stream identical to current</step>
  </steps>
  <success>SSE event stream byte-identical to current on 5 test prompts. No regression. Parallel Machinist speedup measurable (≥1.5× on 4-part assembly vs. sequential).</success>
  <deps>T4-01</deps>
</task>
```

---

### ✅ T4-03: Planner Subagent

```xml
<task id="T4-03" status="✅" effort="2–3d" tier="4">
  <summary>Extract run_planner → PlannerSubagent; isolated context window; model from registry</summary>
  <files>
    <new>backend/subagents/planner.py</new>
    <modify>backend/agents.py  ← run_planner() → shim calling PlannerSubagent</modify>
  </files>
  <steps>
    <step n="1">Move run_planner body → PlannerSubagent.run(ctx): no logic change</step>
    <step n="2">Model: ctx.model_registry.get("planner").model_id (not hardcoded)</step>
    <step n="3">Input: ctx.prompt + ctx.domain + REA payload from ctx.history</step>
    <step n="4">Output: SubagentMessage{payload={manifest: AssemblyManifest.model_dump()}}</step>
    <step n="5">Context window: system_prompt + user_prompt only — no history bleed from clarifier/REA</step>
    <step n="6">Shim: run_planner(*args) → asyncio.run(PlannerSubagent().run(ctx)) for backward compat</step>
  </steps>
  <success>PlannerSubagent.run() produces structurally identical manifests to run_planner() on 10 held-out prompts (same keys, plausible values — LLM output will vary).</success>
  <deps>T4-01</deps>
</task>
```

---

### ✅ T4-04: Machinist Subagent (Parallel-Safe)

```xml
<task id="T4-04" status="⬜" effort="3–5d" tier="4">
  <summary>Extract run_machinist + batch → MachinistSubagent; each instance stateless, parallel-safe</summary>
  <files>
    <new>backend/subagents/machinist.py</new>
    <modify>backend/agents.py  ← run_machinist() / run_machinist_batch() → shims</modify>
  </files>
  <steps>
    <step n="1">MachinistSubagent.run(ctx): handles single part — own tmp dir, own trace_id, own telemetry sink</step>
    <step n="2">Critic loop embedded: MachinistSubagent calls CriticSubagent internally until accept or max_retries</step>
    <step n="3">Parallel spawn: Orchestrator uses asyncio.gather(*[MachinistSubagent().run(ctx_p) for ctx_p in part_ctxs])</step>
    <step n="4">Resource guard: asyncio.Semaphore(MAX_MACHINIST_CONCURRENCY) — default 4, from config.py</step>
    <step n="5">Per-attempt telemetry emitted with parent_id=orchestrator trace_id</step>
    <step n="6">Shim: run_machinist_batch() → gather over MachinistSubagent instances</step>
  </steps>
  <success>4-part assembly: 4 concurrent MachinistSubagents; wall-clock ≤1.5× single-part at 4× concurrency (Gemini API is the bottleneck, not Python). Resource guard prevents runaway parallelism.</success>
  <deps>T4-01, T4-05</deps>
</task>
```

---

### ✅ T4-05: Critic Subagent

```xml
<task id="T4-05" status="✅" effort="2–3d" tier="4">
  <summary>Extract run_critic_loop + run_assembly_critic_loop → CriticSubagent + AssemblyCriticSubagent</summary>
  <files>
    <new>backend/subagents/critic.py</new>
    <modify>backend/agents.py  ← shims</modify>
  </files>
  <steps>
    <step n="1">CriticSubagent.run(ctx): inputs={code, part_id, domain, max_retries} — preserves full Critic Loop (compile→traceback→retry)</step>
    <step n="2">G2/curvature checks remain advisory: CriticSubagent.run() never raises on geometry advisories</step>
    <step n="3">AssemblyCriticSubagent.run(ctx): inputs={step_paths, manifest} → runs interpenetration + DOF checks</step>
    <step n="4">Both emit per-attempt SubagentMessages (retry N logged as child of parent trace_id)</step>
    <step n="5">CriticSubagent wired into MachinistSubagent (T4-04) — not called directly by Orchestrator</step>
  </steps>
  <success>CriticSubagent produces identical pass/fail outcomes to run_critic_loop() on 20 held-out part generation tasks. AssemblyCriticSubagent flags known-bad assembly (interpenetrating parts).</success>
  <deps>T4-01</deps>
</task>
```

---

### ✅ T4-06: Assembler Subagent

```xml
<task id="T4-06" status="✅" effort="2–3d" tier="4">
  <summary>Extract run_assembler + run_assembler_constraint + run_single_part_export → AssemblerSubagent</summary>
  <files>
    <new>backend/subagents/assembler.py</new>
    <modify>backend/agents.py  ← shims</modify>
  </files>
  <steps>
    <step n="1">AssemblerSubagent.run(ctx): selects constraint vs. translation path from manifest.mating_rules</step>
    <step n="2">Single-part fast path: SinglePartExportSubagent (trivial wrapper — enables hot-swap of export logic)</step>
    <step n="3">Output: SubagentMessage{payload={step_path, glb_path, assembly_report}}</step>
    <step n="4">Interpenetration check (T1-02) runs inside AssemblerSubagent post-solve — before output message emitted</step>
    <step n="5">No LLM call in assembler: model registry entry = None (no-op)</step>
  </steps>
  <success>AssemblerSubagent handles all 3 assembly modes. No regression on existing assembly integration tests.</success>
  <deps>T4-01, T4-05</deps>
</task>
```

---

### ✅ T4-07: Clarifier + REA Subagents

```xml
<task id="T4-07" status="✅" effort="2–3d" tier="4">
  <summary>Extract run_rea → REASubagent; run_clarifier → ClarifierSubagent; both use lite model</summary>
  <files>
    <new>backend/subagents/rea.py</new>
    <new>backend/subagents/clarifier.py</new>
    <modify>backend/agents.py  ← shims</modify>
  </files>
  <steps>
    <step n="1">REASubagent.run(ctx): prompt → DesignRequirements SubagentMessage; lite model default</step>
    <step n="2">ClarifierSubagent.run(ctx): prompt + requirements → list[str] SubagentMessage; lite model default</step>
    <step n="3">ClarifierSubagent: empty list → Orchestrator skips clarification gate (no blocking)</step>
    <step n="4">Both add trace_id to existing telemetry.py calls</step>
    <step n="5">Shims: run_rea() → REASubagent; run_clarifier() → ClarifierSubagent</step>
  </steps>
  <success>REASubagent + ClarifierSubagent output matches current agents.py on 10 test prompts. Lite model confirmed from ModelRegistry (not heavy model).</success>
  <deps>T4-01, T4-10</deps>
</task>
```

---

### ✅ T4-08: Airfoil Analyst Subagent

```xml
<task id="T4-08" status="✅" effort="2–3d" tier="4">
  <summary>Wrap analysis/airfoil.py → AeroSubagent; integrate at Orchestrator Phase 3 slot; parallel with assembler</summary>
  <files>
    <new>backend/subagents/aero.py</new>
    <modify>backend/main.py  ← remove _run_aero_stage(); route through AeroSubagent</modify>
  </files>
  <steps>
    <step n="1">AeroSubagent.run(ctx): inputs=step_files + airfoil_spec + flight_env → PolarReport SubagentMessage</step>
    <step n="2">Move _run_aero_stage() body verbatim into AeroSubagent.run() — add message wrapper only</step>
    <step n="3">Skip silently: airfoil_spec is None OR domain != "D" → return null-payload SubagentMessage</step>
    <step n="4">No LLM call — model registry entry = None</step>
    <step n="5">Parallel with assembler: AeroSubagent only needs the wing part STEP (available before full assembly). Orchestrator spawns asyncio.gather(assembler_task, aero_task) when domain=="D"</step>
    <step n="6">CI: MIRUM_XFOIL_BIN=analysis/tests/mock_xfoil — no code change needed, already wired in config.py</step>
  </steps>
  <success>AeroSubagent.run() produces PolarReport matching _run_aero_stage() on 3 NACA test cases. Parallel execution with AssemblerSubagent confirmed via asyncio.gather timing log.</success>
  <deps>T4-01, T4-02, T2-15</deps>
</task>
```

---

### ✅ T4-09: Subagent-Level Telemetry + Trace IDs

```xml
<task id="T4-09" status="✅" effort="2–3d" tier="4">
  <summary>Per-subagent telemetry: trace_id propagation, per-agent latency + token counts, JSONL sink</summary>
  <files>
    <modify>backend/telemetry.py  ← add write_subagent_span()</modify>
    <new>backend/subagents/telemetry_mixin.py</new>
  </files>
  <steps>
    <step n="1">TelemetryMixin (mixed into SubagentBase):
      start_span(name) → records start_time + trace_id
      end_span(tokens_in, tokens_out, status) → writes SubagentSpan JSONL record
    </step>
    <step n="2">SubagentSpan JSONL record:
      {trace_id, parent_id, agent_type, model, latency_ms, tokens_in, tokens_out, status, timestamp}
    </step>
    <step n="3">Orchestrator: root span (trace_id=UUID4, parent_id=null); each child subagent inherits trace_id + sets parent_id=orchestrator trace_id</step>
    <step n="4">telemetry.py: add write_subagent_span() → appends to /tmp/mirum/telemetry/subagent_spans.jsonl (separate from critic_loop.jsonl for query clarity)</step>
    <step n="5">Optional: Streamlit sidebar shows per-agent latency breakdown (low-pri — add only if time allows)</step>
  </steps>
  <success>Full pipeline run produces SubagentSpan records for all 7+ agent types. Trace tree reconstructable from JSONL via trace_id + parent_id linkage. Per-agent P50/P95 latency queryable with jq one-liner.</success>
  <deps>T4-01, T4-02</deps>
</task>
```

---

### ✅ T4-10: Model Registry + Hot-Swap

```xml
<task id="T4-10" status="✅" effort="2–3d" tier="4">
  <summary>ModelRegistry singleton: per-agent model config; hot-swap without restart via POST /admin/model-config</summary>
  <files>
    <new>backend/subagents/model_registry.py</new>
    <modify>backend/main.py  ← add POST /admin/model-config endpoint</modify>
    <modify>backend/config.py  ← add per-agent model defaults table</modify>
  </files>
  <steps>
    <step n="1">ModelRegistry singleton: dict[agent_type → ModelConfig]
      ModelConfig: {model_id: str, temperature: float, max_tokens: int, lite_fallback: str | None}
    </step>
    <step n="2">Default registry from config.py:
      planner:    GEMINI_MODEL_HEAVY, temp=0.3
      machinist:  GEMINI_MODEL_HEAVY, temp=0.2
      critic:     GEMINI_MODEL_HEAVY, temp=0.1
      rea:        GEMINI_MODEL_LITE,  temp=0.2
      clarifier:  GEMINI_MODEL_LITE,  temp=0.4
      assembler:  n/a (no LLM)
      aero:       n/a (no LLM)
    </step>
    <step n="3">SubagentBase.model property: ModelRegistry.instance().get(self.agent_type).model_id</step>
    <step n="4">POST /admin/model-config {agent_type, model_id, temperature} → in-place registry update (no restart)</step>
    <step n="5">Auth: /admin/* gated by ADMIN_API_KEY env var (simple bearer token, no OAuth for v1)</step>
    <step n="6">Hot-swap test: swap Planner to lite model via POST → run 3 generates → swap back → no restart at any point</step>
    <step n="7">Swap logged: telemetry event {type: "model_swap", agent_type, old_model, new_model, timestamp}</step>
  </steps>
  <success>Planner swapped heavy→lite via POST /admin/model-config. Next /generate uses lite model. Swap reverted. Zero process restarts. Swap events visible in telemetry JSONL.</success>
  <deps>T4-01, T4-09</deps>
</task>
```

---

## Dependency Graph

```
T1-01 (Constraint Assembly)
  ├─ T1-02 (PMC Interpenetration)
  ├─ T1-03 (Manifest Review UI)
  ├─ T2-02 (Limit Mates)
  ├─ T2-03 (Mechanical Mates)
  ├─ T2-07 (ABC Synthetic Assemblies)
  ├─ T2-08 (Refinement /refine)
  ├─ T2-13 (Aerospace Demos)
  └─ T3-01 (Semantic Mate Bootstrapping)
      └─ T3-02 (JoinABLe GNN)
          └─ T3-03 (SketchGraphs Pre-training)

T1-07 (LVM Score)
  ├─ T1-09 (Domain-Stratified Eval)
  ├─ T2-06 (DeepCAD Pipeline)
  └─ T3-04 (RLAIF)

T1-08 (F1 Error Attribution)
  └─ T1-09 (Domain-Stratified Eval)

T2-05 (Stratified Dataset)
  └─ T2-11 (SFT Fine-tuning)
      └─ T3-04 (RLAIF)
          └─ T3-05 (AST Constrained Decoding — verify necessity post-SFT first)

T2-06 (DeepCAD Pipeline) ──┐
T2-07 (ABC Assemblies) ─────┤── T2-11 (SFT Fine-tuning)
T2-05 (Stratified Dataset) ─┘

T2-04 (G2 Continuity)
  └─ T3-07 (Gaussian Curvature)
      └─ T3-09 (VLM Zebra Rendering — completes both VLM stubs)

T2-10 (cad_utils Expansion)
  └─ T3-06 (Domain E Sheet Metal)

T2-09 (Parasolid Export)
  └─ T3-08 (PMI Validation)

T2-13 (Aerospace Demos)
  └─ T2-15 (Airfoil Phase 2 Validation)
      ├─ T2-16 (Airfoil Phase 3 Frontend)
      └─ T2-17 (Domain D Eval Harness Prompts)

T2-14 (Gemini Model Centralization)  ← no deps; enables T4-10

T4-01 (SubagentBase protocol)
  ├─ T4-02 (Orchestrator)
  │     ├─ T4-03 (Planner subagent)
  │     ├─ T4-04 (Machinist subagent — parallel)
  │     │     └─ T4-05 (Critic subagent — called inside Machinist)
  │     ├─ T4-06 (Assembler subagent)
  │     │     └─ T4-08 (Aero subagent — parallel with assembler for Domain D)
  │     └─ T4-07 (Clarifier + REA subagents)
  ├─ T4-09 (Subagent telemetry + trace IDs)
  └─ T4-10 (Model registry + hot-swap)
      └─ T4-07 (Clarifier + REA — need registry for lite-model routing)
```

---

## Quick-Start Sequence (Recommended Order)

If starting fresh, execute in this order to get the maximum early impact:

1. **T1-01** — Constraint assembly (foundational; everything else builds on it)
2. **T1-03** — Manifest review UI (stops expensive mistakes; 1 day)
3. **T1-04** — Clarification gate (reduces bad Planner runs; 1 day)
4. **T1-02** — PMC interpenetration check (immediate assembly quality signal; 1 day)
5. **T1-08** — F1 error attribution (tells you where to focus next; 0.5 days)
6. **T1-05** — Retrieval augmentation (quality improvement before fine-tuning; 2–3 days)
7. **T1-06** — Parametric variable extraction (UX win; 2–3 days)
8. **T1-07** — LVM Score (evaluation infrastructure for everything after; 2–3 days)
9. **T1-09** — Domain-stratified evaluation (now you can see where the problems are)
10. **T1-11** — ICP interviews (run concurrently with engineering; must complete before v1 launch)
11. **T2-10** — Expand cad_utils.py (raise the floor before training)
12. **T2-05** — Stratified dataset pipeline (data for SFT)
13. **T2-06** — DeepCAD pipeline (data for SFT)
14. **T2-11** — SFT fine-tuned Machinist (the quality step change)
15. **T2-08** — /refine endpoint (product differentiator)
16. **T2-09** — Parasolid export (enterprise frictionless)
17. **T2-13** — Aerospace demos + SBIR (commercial milestone)
18. **T3-04** — RLAIF (continuous quality improvement post-SFT)
19. **T3-01** — Semantic mate bootstrapping (assembly quality step change)

**Gap-fill additions (run concurrently with above):**

20. **T2-14** — Gemini model centralization (0.5 days, zero logic risk — do immediately)
21. **T2-15** — Airfoil Phase 2 end-to-end validation (requires XFOIL on dev machine)
22. **T2-16** — Airfoil Phase 3 frontend polar display (1–2 days after T2-15)
23. **T2-17** — Domain D eval harness prompts (1–2 days, schema-driven)
24. **T3-09** — VLM Zebra rendering (unblocks two production no-op stubs; start renderer evaluation early)

**Subagent architecture (Tier 4 — begin after Tier 1 complete):**

25. **T4-01** — SubagentBase protocol (foundational; zero risk; do in parallel with T2-14)
26. **T4-10** — Model registry (enables hot-swap; do early to inform all subagent extractions)
27. **T4-07** — REA + Clarifier subagents (simplest extractions, no side effects)
28. **T4-03** — Planner subagent
29. **T4-05** — Critic subagent
30. **T4-04** — Machinist subagent + parallel execution (highest impact latency win)
31. **T4-06** — Assembler subagent
32. **T4-08** — Aero subagent (after T2-15 validates the aero pipeline)
33. **T4-02** — Orchestrator (integrate all subagents; replaces main.py pipeline)
34. **T4-09** — Subagent telemetry (wire after each subagent is extracted)

---

*Generated from: `wiki/synthesis/mirum-capability-gaps.md` + `wiki/synthesis/mirum-technical-architecture.md`*  
*Updated: 2026-05-14 — Added T2-14/15/16/17 (gap audit), T3-09 (VLM Zebra), Tier 4 (Subagent Architecture T4-01–T4-10)*
