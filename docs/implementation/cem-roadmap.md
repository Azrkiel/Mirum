# Mirum CEM plan: from performance requirements to verified geometry (Noyron-style)

Date: 2026-11-03. Author: planning session (Claude). Audience: the builder agent. Read the whole file before starting.

## 1. Goal

A user states **performance requirements**, for example: "motorless glider, launched from 10 m, 1200 s in the air". Mirum should then return one of two things:

- (a) a design that physics predicts will meet the requirements, with geometry, manufacturing files, a predicted-performance report with uncertainty, and a list of assumptions; or
- (b) a quantified **infeasible** verdict that names the binding physics and the cheapest changes that would make the requirements feasible.

This is the LEAP 71 Noyron pattern. Public descriptions (leap71.com/noyron, Metal-AM interview with Lin Kayser) say three things about it:

- It is an **algorithm, not a neural net**. Engineering logic and physics are encoded as deterministic code, and the LLM only helps around the edges.
- **Physics is evaluated while each feature is built.** For example, regen channel cross-section and wall thickness come from local heat flux and pressure, and are not drawn first and then checked.
- **The output is geometry, manufacturing files, and a predicted-performance summary.** Each part carries the model version and an input hash, and the model is **calibrated against physical tests** (they hot-fire roughly every 4 weeks). There is no public evidence of CFD or FEA running in their loop.

**User decisions (2026-11-03), which override defaults elsewhere in this file:**

- The example means **metres of distance**.
- The mode is **maximize**, not "meet a target". 1200 m is a reference stretch goal: always report the gap between it and the best design found.
- The user wants to **push what's possible by discovering new shapes**, not just tune one fixed layout. This is section 5.7.
- **Both manufacturing processes**: foam/balsa sheet and FDM printing, plus a hybrid of the two (section 5.4).
- **Still air is the default.** Other environments are user toggles (section 5.1).

Mirum already follows half of this: "LLM fills a typed spec, then a no-LLM builder renders it, then a fail-closed verifier checks it". It is missing the front half (requirements, feasibility, sizing) and the closing half (measuring the built geometry, re-predicting performance, calibrating).

## 2. Physics reality check for the example (must become a test)

**Still air from launch height h:**

- time aloft is `t = h / v_sink`
- range is `R = h * (L/D)`
- minimum sink is `v_sink = sqrt(2 (W/S) / rho) / (CL^1.5 / CD)`

**The two readings of the example:**

- **1200 s from 10 m** needs a mean sink of **0.0083 m/s**.
  - At an ultralight wing loading of 3 g/dm² (W/S of about 2.9 N/m²), that requires CL^1.5/CD of about **260**. Real low-Re wings reach about 10 to 20.
  - The best sample design in the repo (`glider-designer` results: L/D 13.3, sink 0.34 m/s) gives about **29 s**.
- **1200 m of range from 10 m** needs **L/D of about 120**. Open-class full-size sailplanes reach about 70, and small models reach about 10 to 25.

So the system must say **infeasible**, then give levers with numbers:

| Lever | Requirement |
|---|---|
| Launch energy | `h_eff = h0 + eta * V0^2 / (2g)`. A discus or catapult launch at 25 m/s adds about 20 to 30 m, which is still only about 100 to 150 s. |
| Rising air | A mean uplift of `w >= v_sink - h/t`, so thermal or ridge lift of about 0.15 to 0.35 m/s. |
| Stored energy | Rubber or electric power, which breaks "motorless". |
| Relaxed target | The maximum still-air duration achievable from 10 m, with the spec given. |

The input wording "1200 m of flight duration" mixes units. The front end must ask whether the user means seconds or metres. It must not guess. (For this example the user confirmed metres.)

**What maximize-distance means here.** Ground distance is `R = h_eff * (L/D)` in still air. So, for a fixed 10 m launch, maximizing distance means:

- maximizing trimmed L/D;
- plus any launch energy the chosen launch method adds;
- plus wind, if the user toggles it on.

L/D at this scale is limited mainly by:

- **Reynolds number:** small chord and low speed give poor airfoil performance;
- **aspect ratio versus structural mass:** longer, thinner wings need a heavier spar;
- **the span limit.**

These three trade-offs are where shape discovery can find real gains. Realistic still-air results from 10 m are about 100 to 250 m, against 1200 m needed. The report must state this honestly. With a tailwind `u`, ground distance becomes `R = t * (V cos(gamma) + u)` and the best trim speed shifts (speed-to-fly), so the trim speed must be optimized for the chosen environment, not fixed at the L/D-max speed.

## 3. What already exists (verified from git, 2026-11-03)

| Piece | Where | State |
|---|---|---|
| Glider sizing and search | `origin/main` `backend/gliders/` (PR #9 merged) | AeroSandbox AeroBuildup with NeuralFoil, VLM stability, trim, ballast, lateral modes. Admission fails closed. Seeded random-plus-local search. **The objective is hard-coded to distance** (`backend/gliders/spec.py:67-70`). There is no launch-energy model, and mass comes from fixed areal densities (`model/config.py:34-37`). |
| Duration objective | `origin/glider-designer` `glider_designer/config.py` | `objective: "duration"` maximizes CL^1.5/CD. It exists only in that standalone folder and is not in the backend. |
| Glider outputs | `backend/gliders/model/export.py` | STL (mm), 1:1 SVG templates, three-view PNG, `design.json`, assumptions. No STEP and no PicoGK. |
| Explorer UI and API | `backend/gliders/api.py`, `frontend/components/glider_explorer/` | Jobs pattern, one solver slot, rate limits. Use it as the template for long jobs. |
| PicoGK pattern | `engineering/regen-chamber` and `origin/xvant/regen-generator-3` (c6e9416) | `RegenChamberSpec` (frozen, strict, envelope-checked), C# builder in `picogk-builder/`, `regen_cli.py` gate with exit codes, bounded mesh verifier. **Geometry only, no sizing.** The xvant branch is the newer verifier generation. |
| Engineering gate | `origin/main` `backend/engineering/service.py`, `verify.py` | Cantilever: a fixed box template plus a beam FEA check, with the user supplying the dimensions. Good model for a fail-closed report shape. |
| 3-D wing aero | `origin/main` `backend/wing_aero.py` | Lifting line, **unwired** (`wing_aero_report` is always None). |

Known model limits to carry forward as stated uncertainty:

- NeuralFoil and XFOIL are weak below Re of about 30k, especially with laminar bubbles. *Correction (2026-11-05):* the literature puts the unreliable regime higher. Lissaman (1983) gives a critical Re of about 70,000, and XFOIL disagrees with DNS and wind-tunnel data well above 30k. The CEM now uses a hard floor of Re 50,000 and a degraded band to 70,000; see [cem-low-re-method-decision.md](cem-low-re-method-decision.md).
- Stability comes from a VLM screen, with Ixz neglected.
- There is no structural model.

## 4. Target architecture: one CEM interface, many domains

Add `backend/cem/` with a domain-agnostic pipeline. Each domain (glider first, rocket chamber later) implements six stages:

```
Requirements (typed)  ->  Feasibility (analytic bounds)  ->  Sizing (optimizer over physics)
      ^                                                            |
      |                                                            v
 Calibration  <-  Report (perf + uncertainty + ID)  <-  Verify (as-built closure)  <-  Geometry (feature-by-feature)
```

```python
class CEM(Protocol):
    name: str; version: str                      # version goes into every output ID
    Requirements: type[BaseModel]                # strict, extra=forbid, SI units in field names
    def feasibility(self, req) -> FeasibilityReport   # pure analytic, < 1 s, no geometry
    def size(self, req, budget) -> SizedDesign        # deterministic given (req, seed, version)
    def build(self, design) -> GeometryBundle         # Python and/or PicoGK builder
    def verify(self, design, bundle) -> VerificationReport  # re-predict from MEASURED geometry
    def report(self, ...) -> PerformanceReport
```

**Rules (non-negotiable, consistent with existing Mirum invariants):**

1. **The LLM never computes physics or writes C#.** It only turns text into `Requirements` and explains reports.
2. **Every stage fails closed.** If evidence is missing or non-finite, the result is a rejection, never a pass.
3. **Determinism.** The same `(requirements, seed, cem_version, calibration_version)` gives byte-identical `design.json`. Output ID = `sha256(canonical_json(req) + cem_version + calibration_version)[:12]`, written into every artifact. This is the analogue of Noyron's embossed ID, and can be embossed on printed parts later.
4. **Predicted performance always comes with an uncertainty band and an assumptions list**, never as a single bare number.

## 5. Glider CEM: stage details

### 5.1 Requirements: `GliderMissionRequirements`

```
objective: Literal["distance_m", "duration_s"] = "distance_m"   # always maximized
reference_target: float | None                       # e.g. 1200 (m); used only to report the gap, never to stop the search
launch: { height_m (0-100), method: "drop"|"hand_toss"|"discus"|"catapult"|"hi_start",
          launch_speed_m_s: float | None }           # None means a per-method default with a stated assumption
environment: {                                       # default kind = "still_air"; every other kind is an explicit user toggle
  kind: "still_air" | "wind" | "uniform_uplift" | "thermal" | "ridge",
  wind_m_s: float = 0,            # +tailwind / -headwind along the flight path (kind="wind")
  uplift_m_s: float = 0,          # kind="uniform_uplift"
  thermal: {core_uplift_m_s, radius_m, distance_m} | None,   # simple column model, phase G8
  ridge: {slope_deg, wind_m_s} | None,                        # phase G8
  altitude_m, temperature_c }
constraints: { max_span_m, max_length_m, max_mass_kg, payload: {mass_kg, diameter_m, length_m} | None }
process: "sheet_foam" | "balsa" | "fdm_lwpla" | "fdm_pla" | "hybrid" | "auto"   # "auto" = search all of them and report the best per process
discovery: { families: list[Family] | "all", budget_evals: int, seed: int }    # section 5.7
```

Reuse the bounds style of `backend/gliders/spec.py` (`StrictModel`).

### 5.2 Feasibility (new, pure analytic): the most important piece

- Compute `h_eff`.
  - Launch zoom efficiency `eta` per method is a calibratable coefficient, defaulting to 0.6 with "assumption" marked.
  - Hand toss defaults to about 8 m/s, discus about 25 m/s, catapult about 30 m/s. Every default is labelled as an assumption.
- Compute the **lower bound of wing loading** for the process. This is minimum structural areal density times minimum area, plus payload and hardware. This is where a 60 g payload makes long duration hard.
- Take the **upper bound of CL^1.5/CD and L/D** from a Re-indexed table built offline from NeuralFoil and XFOIL over a fixed airfoil set (UIUC low-Re: SD7037, AG-series, S1223, flat or cambered plate). Cache the table as a versioned JSON with its source recorded.
- Output:
  - a verdict: `feasible | marginal | infeasible`
  - the best achievable value
  - the binding constraint
  - **levers**, each with the quantified requirement: needed `h_eff`, needed uplift `w`, needed wing loading, or the relaxed target
- Bounds are optimistic on purpose. Feasibility may only say "infeasible" when even the optimistic bound fails. Otherwise it passes to sizing.
- **In maximize mode, an infeasible `reference_target` never stops the run.** The report says "1200 m needs L/D ≈ 120 from 10 m; the optimistic bound at this span/process is X; the best design found is Y". It then lists the levers, and the search continues.

### 5.3 Sizing (extend the existing search, don't rewrite it)

- Distance is already the backend objective. Add an `objective` field anyway (duration as the secondary option, ported from `glider_designer/config.py`) and thread it through `configuration()`.
- Add the launch-energy model.
- Add the environment toggles:
  - **Wind:** trim speed becomes a design variable optimized for ground distance (speed-to-fly).
  - **Uplift:** `t = h_eff / max(v_sink - w, eps)`. If `w >= v_sink`, report "sustained (lift-limited)" instead of infinity. Distance then depends on the size of the lift region, so for uplift and thermal the distance is reported as a range, not a single number.
  - **Still air** stays the default.
- **Replace the fixed areal densities with a process mass model:**
  - The spar is sized for bending at launch load factor `n_launch`. Root moment comes from elliptic lift. A spar tube or strip is chosen from a material table (carbon rod, spruce, balsa) to give stress at most `sigma_allow / SF`. Mass follows from that.
  - Skin and ribs come from per-process areal density.
  - This makes wing loading respond to span and launch method, which is what makes the duration optimum real.
- The search objective is to maximize the duration or distance margin over the target, subject to the existing `admissible()` constraints. Keep the seeded random-plus-local search. Optionally add AeroSandbox `Opti` gradient refinement around the best candidate (Phase G4, optional).

### 5.4 Geometry: feature-by-feature, physics evaluated at the feature

**Both processes are built, user decision 2026-11-03.**

- `process="auto"` runs discovery once per process and reports the best design for each, side by side.
- `hybrid` means a printed fuselage pod and joints with foam or balsa flying surfaces. It is often the best real-world option.

**Each process limits the shape space through manufacturability checks inside `admissible()`, not after it:**

| Process | Shape limits |
|---|---|
| Sheet foam | Cambered-plate or simple sectioned airfoils only. Limited twist. Dihedral only at discrete joints. Thickness from stock sheet sizes. |
| Balsa built-up | Real airfoils. Rib spacing limits. |
| FDM | Any airfoil and smooth twist or dihedral. Minimum wall, bed size, overhang. Mass penalty from the printed shell. |

Two builders, picked by `process`:

- **`sheet_foam` / `balsa`:** the existing STL and SVG export, plus new physics-driven features:
  - spar slot sized by the 5.3 spar section at each span station;
  - rib spacing set by the skin's buckling and sag allowance;
  - ballast pocket volume sized from the computed ballast mass and density;
  - payload bay sized from the payload dimensions plus clearance.
- **`fdm_*`:** a **new PicoGK builder**, following the `RegenChamberSpec` pattern exactly:
  - Python emits a strict `GliderPartsGeometrySpec` JSON, made of airfoil section polylines per station, planform, spar path and diameter per station, wall thickness per process, ballast and payload cavities, and voxel size. It has an envelope check like `supported_envelope` in `regen_spec.py`.
  - The C# builder (`picogk-builder/` sibling project, or the same project with a second command) does the following:
    - lofts the outer mould line from the sections (mesh, then voxels);
    - shells it at the process min wall;
    - subtracts the spar channel;
    - adds internal ribs or a LatticeLibrary infill whose density is graded by the local bending moment, which is the Noyron-style "physics at the feature" step;
    - adds the ballast pocket and payload bay;
    - splits the result into printable segments no larger than the printer bed (a process parameter).
  - The C# side validates nothing beyond what it needs to avoid crashing. **The Python gate is authoritative**, the same rule as `regen_cli.py`.
  - Reuse the bounded verifier approach from `xvant/regen-generator-3`: weld, manifold, components, volume, budget accounting, and runs under a memory cap.
- Do not try to get STEP out of PicoGK. It has no B-rep export. STL or 3MF plus SVG is the manufacturing output.

### 5.5 Verify: as-built closure (new; Mirum's equivalent of a hot-fire)

1. Measure the built geometry from the actual meshes: volume times material density, then mass, CG and inertia tensor (divergence theorem on the closed mesh). Add hardware, ballast and payload point masses.
2. **Re-run trim, stability and performance** with the measured mass properties, not the sizing estimates.
3. Pass only if every one of these holds:
   - the measured mass is within tolerance of the sized mass (start at 5%);
   - the re-predicted performance still meets the target, at the P10 of the uncertainty band if the target was a hard requirement;
   - static margin and modes stay within the `admissible()` limits;
   - the structural check at `n_launch` passes on the as-built spar section;
   - the manufacturability checks pass (min wall, bed size, overhang if FDM).
4. **Uncertainty:** run a seeded Monte Carlo (N of about 200) over `CD0` multiplier, `CLmax` multiplier, mass build variance and launch `eta`. Each takes its prior from the calibration file. Report P10, P50 and P90 for duration and distance.

### 5.6 Report and calibration

- `performance_report.json`: requirements, verdict, predicted P10/P50/P90, binding constraints, margins, assumptions, model limits (low-Re caveat, no gusts), output ID, CEM version and calibration version.
- `calibration/glider.json` holds versioned coefficients: `cd0_factor`, `cl_max_factor`, `oswald_factor`, `eta_launch[method]`, `mass_factor[process]`, each with its prior.
- `POST /cem/glider/flight-test` takes `{output_id, measured: launch_height, launch_method, duration_s, distance_m?}`. It refits the coefficients by least squares, or Bayesian update of a log-normal multiplier, and writes a **new calibration version**. Old outputs stay reproducible because their ID pins the version.

### 5.7 Shape discovery: pushing past one fixed layout (user priority)

**Honest framing.**

- Noyron's publicly described novelty comes from **encoded design logic plus fast generation**. It can build many variants in minutes, and its geometry follows physics, so unusual forms such as aerospikes and conformal channels come out naturally.
- LEAP 71 has not publicly described using blind ML search.
- Mirum should get novelty the same way, with a **wide, physics-checked design space and a search that explores it**:
  - every candidate is still sized by physics and passed through `admissible()`;
  - nothing is accepted on the optimizer's word alone.

**Design representation.** Today the space is one conventional layout with a few parameters. Widen it in three layers.

1. **Configuration families**, each a small builder plus family-aware admission:
   - conventional, T-tail and V-tail;
   - tailless swept wing with washout (Horten-style bell lift distribution);
   - tailless plank with a reflex airfoil;
   - canard;
   - tandem wing;
   - box or joined wing;
   - blended body;
   - optionally, nonplanar tips such as winglets or C-wing.
2. **Continuous spanwise shape**, with the same knobs in every family:
   - B-spline distributions of chord, twist, dihedral and sweep, with 5 to 8 control points each;
   - per-station airfoils from **CST (Kulfan) coefficients**, constrained for thickness so they stay buildable;
   - tail or second-surface sizes and positions;
   - trim speed.
3. **Process**: from 5.4, which also constrains layers 1 and 2.

**Analysis coverage per family:**

- The VLM in AeroSandbox handles nonplanar, canard, tandem and box layouts.
- Each family's admission must check its own failure modes, for example:
  - tailless needs Cm0 > 0 and an adequate static margin from reflex or washout;
  - tandem and canard need the forward surface to stall first;
  - box wing needs the joint load path.
- A family whose analysis cannot be trusted must not be offered.

**Search: two levels, both seeded and deterministic.**

- **Outer: quality-diversity (MAP-Elites)** across all families. It keeps an archive grid indexed by behaviour descriptors, for example family × aspect ratio × wing loading × sweep, or tail volume. Each cell keeps its best-distance design.
  - The archive holds many different good shapes, not one optimum. This is the "discover new shapes" engine.
  - It also shows the user which regions of design space are worth having.
- **Inner: gradient refinement** of each elite with AeroSandbox `Opti`. CasADi gives automatic differentiation, and NeuralFoil is differentiable. Use CMA-ES where gradients fail, for example on discrete family switches.
- The budget is set by `budget_evals`, run as a job (the jobs pattern in `backend/gliders/api.py`). Expect hours, not seconds, for a full archive. Run it under a memory cap.

**Guards against exploitation: the main risk of open search.** An optimizer will find shapes that look great only because the model is wrong there.

1. **Validity envelope.** Reject or penalize evaluations outside the model's trusted range:
   - NeuralFoil `analysis_confidence` below a threshold;
   - Re below the calibrated floor (now Re 50,000 at the lowest speed, with a degraded band to 70,000, per [the low-Re decision](cem-low-re-method-decision.md));
   - CL near CLmax;
   - sweep or nonplanarity beyond VLM assumptions.
2. **Independent re-check of the top designs.** Before reporting, re-run each top-K elite on higher-fidelity paths:
   - XFOIL polars at its actual Re for each station;
   - AVL for trim, stability and modes;
   - optionally OpenVSP VSPAERO panel.
   If L/D or stability disagrees beyond a set tolerance, the design is flagged `unverified_novelty` and not ranked as a win.
3. **Report novelty explicitly.** Report each design's distance in descriptor space from the conventional baseline and its predicted gain, with the uncertainty band. A "new shape" that is only 2% better and inside model error is labelled as such.
4. **Flight test closes the loop (G7).** Novel elites are the first ones to build and fly. Their results recalibrate the model, which then reshapes the archive. This is Noyron's hot-fire loop applied to gliders.

**UI.**

- An archive map: a grid coloured by best distance, with family filters.
- Clicking a cell opens that elite in the existing explorer.
- A "build this one" action runs G3 closure and the builder for the chosen process.

## 6. LLM front end (thin)

- Add a Gemini structured-output call that maps text to `GliderMissionRequirements`. It must avoid `exclusiveMinimum`-style schema features (the PR #8 bug: `gt=` breaks the Gemini schema, so use `ge=` or validate after parsing).
- **Ambiguity is a hard stop, not a guess.** Examples: a unit mismatch like "1200 m of duration", a missing launch method, or no answer on whether the target is a hard requirement or something to maximize. In these cases the API returns `needs_clarification` with the specific questions.
- The LLM explains the feasibility and performance reports in plain language, quoting the report's numbers. It never makes up its own numbers.

## 7. Phases for the builder (each one is a PR-sized slice with acceptance tests)

Branch from `origin/main` (2e6a4ee). Do not touch the `deploy/*` branches. Each phase is fail-closed, deterministic and tested.

| Phase | Scope | Acceptance |
|---|---|---|
| **G0 CEM skeleton** | `backend/cem/` protocol, report types, output-ID hashing, canonical JSON | Identical inputs give identical ID; any field change changes the ID; tests pass. |
| **G1 Feasibility** | `GliderMissionRequirements`, launch-energy model, offline Re-indexed airfoil bound table (script plus versioned JSON), feasibility function | Golden tests: **10 m / maximize distance with `reference_target` 1200 m** gives a gap report: 1200 m flagged infeasible (needs L/D ≈ 120), the optimistic bound is reported, and `continue_search=true`. 10 m / 1200 s gives `infeasible` (sink). Runs in under 1 s. |
| **G2 Processes + environments** | Process mass models for sheet_foam, balsa, fdm_*, hybrid, with spar sizing at `n_launch`. Per-process manufacturability in `admissible()`. `process="auto"`. Environment toggles: still air (default), wind with speed-to-fly trim, uniform uplift. Launch energy in performance. | The existing glider and search tests stay green. `auto` returns one best design per process. A tailwind raises distance and lowers the optimal trim speed versus still air on the same design. Spar stress at most allowable. |
| **G3 As-built closure (non-PicoGK)** | Mass properties from the existing STL export, re-trim, Monte Carlo band, `performance_report.json` | An injected 20% mass error must fail closure. Bands are reproducible with the seed. |
| **G4 Shape discovery** | Configuration families (start with conventional, V-tail, tailless swept, flying plank, tandem; then canard, box), spanwise B-spline + CST parameterization, family-aware admission, MAP-Elites archive, `Opti` refinement, validity envelope, XFOIL+AVL re-check, `unverified_novelty` flag | The archive fills at least 3 families with admissible elites. Determinism: same seed gives the same archive. Exploitation guard test: an injected model bug (e.g. a CD hole at low Re) is caught by the re-check, not ranked. The best elite's distance is at least the current conventional baseline, or the report explains why not. |
| **G5 PicoGK glider builder** | `GliderPartsGeometrySpec` (any family), C# builder (loft, shell, spar channel, graded infill, cavities, bed split), Python gate, bounded verifier, Docker image. Plus the sheet/balsa SVG builder extended for the new families. | Byte-identical STL across runs on one image (pin digests per image). Manifold, one component per part. Measured mass within 5% of sized. Runs under the 3 GiB cap. |
| **G6 API + LLM front end + archive UI** | `POST /cem/glider/{feasibility,discover,design}`, jobs pattern, Gemini spec extraction, `needs_clarification`, archive map in the explorer | Text input → archive → chosen elite → build bundle, end to end. Ambiguous units return `needs_clarification`. |
| **G7 Calibration** | Flight-test endpoint, calibration versioning, refit, archive re-score | A synthetic test log with known `cd0_factor` is recovered within 5%. Old IDs still reproduce. |
| **G8 Advanced environments** | Thermal column and ridge lift models (opt-in toggles) | Distance is reported as a range, with the assumptions listed. |
| **R1 (later) Rocket CEM front half** | `ChamberRequirements {thrust_N, Pc_bar, propellants, mixture_ratio, Pe_or_eps, burn_time_s}`, then NASA CEA (or `rocketcea`; verify licence and Windows/Linux install) for `c*, Isp, gamma, Tc`, then `mdot = F/(Isp*g0)`, `At = F/(Pc*Cf)`, `eps` from `Pe`, chamber volume from `L*`, Bartz heat flux, then channel count, size and wall from coolant dT and hoop stress, then emit the existing **`RegenChamberSpec`** | Hand-checked against a published small engine; feeds the existing builder and verifier unchanged. This is the literal "thrust/Isp in, engine out" path. |

Suggested order: G0 → G1 → G2 → G3 → G4 → G5 → G6 → G7 → G8.

- G1 alone answers the user's example honestly.
- G4 (discovery) needs G2 and G3 first, so that every elite is sized and closure-checked by physics the user can trust.
- G5 is the biggest geometry job.

## 8. User decisions (confirmed 2026-11-03)

1. **Units:** metres (distance). The clarification gate stays for future ambiguous inputs.
2. **Mode:** maximize, with 1200 m as the reference stretch goal for the gap report. Push what's possible through shape discovery (5.7).
3. **Process:** both. Sheet/balsa and FDM, plus hybrid, with `auto` comparing them.
4. **Environment:** still air by default. Wind, uplift, thermal and ridge are user toggles.

Open, for the builder to raise with the user if it matters:

- the compute budget for a full discovery run (hours on the laptop versus a bigger remote machine);
- whether nonplanar/box families are worth their analysis risk before the flight-test loop exists.

## 9. Pitfalls already known in this repo

- The Gemini schema rejects `exclusiveMinimum`. Use `ge` or post-validate (PR #8).
- On Windows, new worktrees check lockfiles out CRLF, which makes the Docker pip layer miss its cache, and Avast then breaks TLS. Overlay onto an existing verify image (see the Mirum memory and README).
- Run every expensive mesh step under a memory cap (`xl-evidence/mirum-regen-sweep/capped.py`). An uncapped verifier froze the laptop on 2026-11-02.
- PicoGK hashes differ between Windows and Linux (about 1.3% of facets, under 6e-5 mm). Pin digests per build image.
- PicoGK memory timer crash: stop `Library.m_oTimerMemCheck` via reflection, as `picogk-builder/Program.cs` does.
- Do not loosen any existing check to make a new phase pass.
