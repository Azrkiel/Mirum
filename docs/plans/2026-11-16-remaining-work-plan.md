# Mirum remaining-work plan (single agent, sequential)

Date: 2026-11-16. Written for **one** agent that executes the steps below in order.
It replaces the wave protocol and launch-prompt template in
[co2-dragster master § 8](co2-dragster/00-master-plan.md#8-agent-protocol-every-agent).
Everything else in the master (§ 3 scope, § 5 invariants, § 6 contracts, § 9 acceptance) and
in the subplans stays in force and is linked, not copied.

Two goals drove this rewrite: stop spending hours on test runs (§ 4), and add
physics-based verification of the glider CEM in `backend/cem/` (§ 5).

## 1. Status snapshot (checked against git and GitHub on 2026-11-16)

`origin/main` = `b308034` (2026-11-12). Open PRs: #31, #18, #16.

| Item | State | Evidence |
| --- | --- | --- |
| Dragster D0–D6, F1 fix | merged | PRs #20, #22–#27, #29; master § 7 |
| Wave-3 handoff | merged | PR #30 (`d4189b6`) |
| Open-access deploy config | **merged** | PR #19 → `b308034`. `deploy/fly/frontend.fly.toml` on `main` now has `MIRUM_ACCESS_GATE = "0"` |
| D7 service/API/UI v2 | **draft PR #31**, not "not started" | Branch `feat/co2-dragster-d7-service-ui`, head `5ba5686`, 2 commits, 15 files, base `d4189b6`. CI 4/4 green on the head. PR body reports 1271 dragster tests passed and a security review with 0 findings in changed code. Two exit criteria are open: frontend image import check, browser screenshots |
| D8 backend, D8 UI, D9 | not started | No branch, no files (`backend/dragsters/intent.py` absent) |
| CEM glider | screening workflow on `main` | `backend/cem/glider/`, 13 CEM/glider test files; "not a completed G0–G8 system" ([cem-handoff](../implementation/cem-handoff.md)) |
| CEM-V physics verification | group 1 (V1–V8) added with this plan | `backend/tests/test_cem_physics_verification.py`: 48 tests, all pass on `main`'s CEM code (§ 5.5) |

Changes since the old master and handoff were written:

1. **PR #19 merged.** D9's "reconcile PR #19 first" step is gone. Deploying `main` now keeps
   the site open. The remaining deploy question is reversed: keep it open, or re-enable the
   gate (`MIRUM_ACCESS_TOKEN` secret plus `MIRUM_ACCESS_GATE = "1"`, per `deploy/fly/README.md`).
   Master § 2 "Deploy" row and the § 10 risk row about PR #19 are stale.
2. **D7 exists as draft PR #31** (opened 2026-11-11). Master § 7 still says "not started".
   PR #31 predates `b308034`; GitHub reports it mergeable.
3. PR #31's review found three **baseline** issues outside D7's files: HIGH PMI path
   containment at `backend/main.py:2075`, HIGH unsanitized PMI errors near `:2079–2101`,
   MEDIUM unbounded `PMIRequest` strings. They are not fixed anywhere in this plan (§ 9, item 7).

## 2. Rules that do not change

- **Dragster:** [master § 5](co2-dragster/00-master-plan.md#5-invariants-never-break) invariants 1–11
  and [§ 6](co2-dragster/00-master-plan.md#6-shared-contracts-frozen-by-d1d2-others-build-against-them)
  contracts. In short: v1 stays byte-stable; no LLM in geometry; `compiler.py`,
  `script_policy.py` and the sandbox allowlists are never edited; fail closed; verification
  never imports the builder; never raise `ProcessRunner` limits (30 s / 1536 MiB); never loosen
  a tolerance or widen a bound; no thrust, race-time, strength or performance claims.
- **Dragster physics:** the only physical quantity is body mass = density × measured volume
  (D6, merged). It is already checked independently of the builder (`validation_v2` measures
  the reimported STEP; the reference volume comes from the profile law). Add nothing else.
- **CEM:** [cem-roadmap § 4](../implementation/cem-roadmap.md#4-target-architecture-one-cem-interface-many-domains)
  rules 1–4: the LLM never computes physics; every stage fails closed; same inputs give
  byte-identical output; predictions always carry an uncertainty band and assumptions. Plus
  [cem-handoff § Invariants](../implementation/cem-handoff.md#invariants-and-practical-traps):
  the station floors stay at Re 30,000 and confidence 0.8; no analytic fallback may count as
  independent solver evidence; `manufacturing_ready` stays false.
- **Merge gates:** [worktree-merge-gate.md](../../.claude/rules/worktree-merge-gate.md).
  Gate (a) schema tests on every PR. Gate (c) `mirum-security-reviewer` on any change to
  `backend/main.py` endpoints, `backend/schemas.py`, `backend/cad_utils.py` or key handling.
  Gate (b) is discussed in § 4.4.
- **Git:** commit only on the workstream branch; never `--no-verify`; never commit `.env`.

## 3. Execution order and stop points

One agent, one branch at a time, one PR per step. **STOP** means: post the question, then
continue with the next step that does not depend on the answer. Never wait idle.

| # | Step | Branch | Depends on | Stop points |
| --- | --- | --- | --- | --- |
| 1 | **CEM-V** physics verification (§ 5) | `test/cem-physics-verification` | none | STOP-M |
| 2 | **D7** finish draft PR #31 (§ 6.1) | `feat/co2-dragster-d7-service-ui` (existing) | none | STOP-M |
| 3 | **D8 backend** (§ 6.2) | `feat/co2-dragster-d8-text` from `origin/main` | D1 (merged) | STOP-G: live sample not run |
| 4 | **D8 UI** (§ 6.3), same PR as step 3 | same | D7 **merged** | STOP-M |
| 5 | **D9** release gate (§ 6.4) | `chore/co2-dragster-d9-release` | D7, D8 merged | STOP-G, STOP-P, STOP-D |
| 6 | **CEM next work** (§ 7) | one branch per slice | step 1 merged | STOP-M per slice |

Stop points that need the owner:

- **STOP-M, merge authority.** Unanswered since the handoff (§ 11 Q2/Q3). Default: the agent
  never merges and never edits master § 7 status cells; it reports "ready to merge" with
  evidence. If the owner grants merge authority, merge one PR at a time and wait for `main` CI.
- **STOP-G, live Gemini.** `python -m dragsters.eval.run_live` (15 LITE calls) and any live
  eval-harness run spend the owner's key and quota. Never run without a yes in the session.
- **STOP-P, showroom reference photo.** D9's visual review compares against it; it is not in
  the repo. Without it, record the screenshots and mark the verdict "awaiting reference".
- **STOP-D, deploy.** `fly deploy` and the live smoke only after an explicit yes. Before
  asking, state: the site is open, and `/dragsters/interpret` lets any visitor spend the
  Gemini key at 10 requests/minute per IP.

Sub-agents: only `mirum-security-reviewer` (D7 if endpoint code changes again, D8 always),
and at most **one** helper when the work is truly independent of the main thread (for
example, CEM-V native checks while D8 tests are being written). No fan-out, no per-task
reviewer agents, no waves.

If step 2 is blocked on the owner's laptop (RAM gates, see § 4.3), do step 3 meanwhile.
Step 4 never starts before D7 is on `main`; if it is not, report "D8 UI waiting on D7".

## 4. Test budget

### 4.1 Why earlier runs took hours

No single suite takes hours. Recorded timings:

| Run | Tests | Wall clock | Source |
| --- | --- | --- | --- |
| Pure-Python slice (`test_cem_requirements`, `test_dragster_spec_v2`, `test_dragster_profiles`) | 891 | 1.4 s | measured 2026-11-16, bare Python, no CAD |
| D7 focused files, verify image | 43 | 143 s | PR #31 |
| Full dragster suite, verify image | 1271 | 478 s | PR #31 |
| CEM + glider + AVL/XFOIL wrappers, native venv | 271 (+7 skipped) | 124 s | [cem-shape-discovery](../implementation/cem-shape-discovery.md) |
| CI native gate, D0 baseline | 1580 | 697 s in pytest | [D0 evidence](co2-dragster/evidence/D0-baseline.md) |
| CI native gate on PR #31, including image build | — | about 19 min | check-run times |

The hours came from repetition: full suites re-run after small edits, several agents each
running their own, image rebuilds, and waiting on laptop RAM. The tiers below remove that.

### 4.2 Tiers

| Tier | What | When | Budget | Commands |
| --- | --- | --- | --- | --- |
| **T0** | Pure-Python unit and physics tests of the files being edited | every edit | seconds | `python -m pytest -q -p no:cacheprovider <files>` from the repo root. CEM-V group 1, `test_dragster_spec*`, `test_dragster_profiles`, `test_dragster_intent`, `test_dragster_extraction`, frontend pure tests |
| **T1** | Targeted native files for the change | after a coherent change, at most a few times per step | 2–3 min (143 s evidence) | fast container path below, listing only the touched test files |
| **T2** | Full suite of the workstream: dragster suite, or CEM+glider suite | **once** per workstream, before the PR leaves draft | dragster about 8 min; CEM about 2 min | fast container path with `backend/tests/test_dragster_*.py frontend/test_dragster_*.py`, or `backend/tests/test_cem* backend/tests/test_glider* frontend/test_cem_ui.py` |
| **T3** | Gate (a) + full native gate, once per PR | once per PR head | gate (a) < 1 s; native gate about 19 min in CI | `python backend/tests/test_prompts_schema.py`, `test_runner_filters.py`, `test_md_report.py`; then push and let CI job "Native CAD and offline API regression gate" run. Do not also run the full gate locally |
| **T4** | Live provider, deploy, live smoke | release only, each with approval | minutes; estimate | `python -m dragsters.eval.run_live`; `fly deploy …` ([D9 § 7–8](co2-dragster/D9-release.md)) |

Fast container path (T1/T2), from the handoff § 3; no image rebuild, code is bind-mounted:

```bash
W=$(pwd -W); MSYS_NO_PATHCONV=1 docker run -d --name <step>-suite --memory 3g --network none \
  --tmpfs /tmp:rw,exec,size=1g,mode=1777 -v "$W/backend:/workspace/backend:ro" \
  -v "$W/frontend:/workspace/frontend:ro" -v "$W/docs:/workspace/docs:ro" mirum-verify:main-w2 \
  sh -c 'python -m pytest -q -p no:cacheprovider -rfEs <test files> > /tmp/s.log 2>&1; grep -E "^(FAILED|ERROR)|passed|failed" /tmp/s.log'
docker wait <step>-suite; docker logs <step>-suite; docker rm <step>-suite
```

(`pwd -W` and `MSYS_NO_PATHCONV` are Git-Bash forms; on Linux use `$(pwd)`. CEM tests may also
need `analysis/` mounted the same way; not verified.)

### 4.3 Rules

1. **Lowest tier that can fail.** A change is not promoted to the next tier until the tier
   below is green. T2 is not a debugging tool; reproduce a T2 failure with the single file at T1.
2. **Never re-run a green T2 or T3 unless code changed.** Docs-only commits re-run gate (a) only.
   CI on the PR is the T3 record; do not repeat it locally.
3. **One long run at a time, detached** (`docker run -d` + `docker wait`), then keep working at
   T0. Do not poll in a loop. Check `docker ps` is empty before starting.
4. **Rebuild the image only if** `requirements*.lock`, `requirements*.txt`, `Dockerfile.verify`
   or `frontend/components/glider_explorer/package*.json` changed. Otherwise reuse
   `mirum-verify:main-w2`. A rebuild runs in the foreground with the CA workaround from
   `.claude/handoffs/2026-11-09-2203.md` § 3. None of steps 1–5 should need one.
5. **Timing tests only on AC power** (`(Get-CimInstance Win32_Battery).BatteryStatus` = 2).
   A timing failure on battery is not a finding. Report a median over 12 s; never raise limits.
6. **RAM gates** (owner's laptop): > 4 GB free before a 3 GB container or a build; ≥ 5 GB free
   and nothing else running for the D7 browser check (backend 3 g + frontend 1 g).
7. **A local failure is real.** F1 is fixed; expect 0 failures and 0 skips. Skipped or mocked
   CAD tests do not count as evidence.
8. **Budget per step:** if test wall-clock for a step passes 45 minutes in total, stop and
   report what is being repeated and why. This number is a planning estimate.

### 4.4 Gate (b) and environment caveats

- **Gate (b)** (`python -m eval_harness.runner --regression-for <letter>`) is defined for
  worktrees A–F. No letter is assigned to the dragster or CEM work, and the live run needs a
  Gemini key (STOP-G). The handoff required only gates (a) and (c) for D7/D8. Default here:
  run `--dry-run` only where a step touches the generic pipeline (D8 edits `_pipeline` and the
  `rule_co2_dragster` keyword list), and record "gate (b) live: not run, no slice assigned".
  The owner decides whether a live slice is wanted (§ 9, item 3).
- **Where the agent runs.** The handoff commands assume the owner's Windows laptop with
  Docker and the `mirum-verify:main-w2` image. A cloud session has no Docker daemon (checked
  in this session: `docker ps` cannot connect). There, T0 runs locally and T1–T3 run through
  CI on the pushed branch, unless a venv from `requirements-linux.lock` +
  `requirements-glider.lock` installs (untested; the image uses Python 3.10). The D7 browser
  check and frontend image build need the laptop (§ 9, item 2).

## 5. Workstream CEM-V: physics-based verification of the glider CEM

Touches only `backend/tests/` and `docs/implementation/`. No endpoints, no production code,
so gate (c) does not apply. It can run before or alongside D7.

### 5.1 What the CEM computes (from the code)

| Module | Computes |
| --- | --- |
| `physics.atmosphere` | Density from ISA hydrostatic pressure at altitude with a **supplied** temperature (ideal gas, R = 287.05); viscosity from Sutherland's law |
| `physics.launch_state` | Effective height `h + η V²/(2g)`, η from calibration (drop: 0) |
| `physics.trajectory` | Duration `h/(sink − uplift)`, distance `t·(√(V²−sink²) + wind)`; `sustained_lift_limited` with no finite bound when uplift ≥ sink |
| `feasibility.feasibility` | Optimistic bound from the airfoil table, `required_lift_to_drag = target/h`, `required_sink = h/target`, four levers |
| `structure.bending_moment` | Half-wing bending moment under ideal elliptical total lift `n W` |
| `processes.spar_size`, `structure.converged_spar` | Smallest catalogue rod with `32M/(πd³) ≤ allowable/SF`; fixed point including the rod's own mass and the 48-gon section ratio |
| `uncertainty.uncertainty` | 200 seeded log-normal samples at fixed trim CL → P10/P50/P90 |
| `model.evaluate_candidate`, `closure.verify` (native) | Trim, stability, station Reynolds/confidence, as-built mass re-prediction |

### 5.2 Group 1: pure Python (T0)

The oracle is computed inside the test from the closed form or a published reference value.
It never calls the function under test or a helper that shares its formula. Tolerances are
test tolerances proposed here; the committed tests use the tolerances listed in § 5.5,
which win where they differ.

| ID | Law / limit | Code under test | Independent oracle | Tolerance | Exists today |
| --- | --- | --- | --- | --- | --- |
| CEM-V1 | ISA + ideal gas; Sutherland | `physics.py:atmosphere` | Standard-atmosphere table: 0 m, 15 °C → ρ 1.2250 kg/m³, μ 1.789e-5 Pa·s; 1000 m, 8.5 °C → ρ ≈ 1.1117 | ρ rel 5e-4; μ rel 1e-3 | new (only a qualitative "thinner air" test) |
| CEM-V2 | Glide energy identities | `physics.py:trajectory` | `t·(sink−uplift) = h`; still air `distance/h = √(V²−w²)/w = L/D`; wind adds `u·t`; uplift ≥ sink → `sustained_lift_limited`, duration and distance `None` | rel 1e-12 | partial (`test_zoom_and_wind_and_sustained_lift_have_finite_semantics`) |
| CEM-V3 | Launch energy conservation | `physics.py:launch_state` | `Δh = η V²/(2g)` with g = 9.81; drop adds exactly 0 | rel 1e-12; drop exact | partial (same test) |
| CEM-V4 | Elliptic-lift beam statics | `structure.py:bending_moment` | Tip `M = 0`; root `M = n W b/(3π)`; `−dM/dy` at root `= n W/2` (shear = half the lift); `d²M/dy² = q(y) = 2nW/(πL)·√(1−(y/L)²)`, `L = b/2`; `M` non-increasing; plus Simpson quadrature of `∫ q(s)(s−y) ds` | root rel 1e-12; quadrature rel 1e-6; finite-difference shear rel 1e-6, curvature rel 1e-4 away from the tip | partial (root, tip, monotone in `test_cem_structure`) |
| CEM-V5 | Sizing consistency | `processes.py:spar_size`, `structure.py:converged_spar` | `spar_size` root moment = `bending_moment(…, 0)`; stress = `32M/(πd³)` recomputed; fixed point: `sizing_mass = non_spar + rod mass`, rod mass = `π d²/4 · b · ρ`; the next smaller catalogue rod, with its own mass, exceeds `allowable/SF` | rel 1e-12; strict inequality | partial (`test_catalogue_sizing_closes_its_own_mass…`); "next smaller fails" is new |
| CEM-V6 | Regular 48-gon second moment | ratio inside `structure.py:converged_spar` | Polygon second moment by the shoelace integral over 48 vertices on a unit circle, divided by `π/4` | rel 1e-12 | related mesh test exists; this integration is new |
| CEM-V7 | Roadmap § 2 limits; lever closure | `feasibility.py:feasibility` | 1200 m from 10 m → required L/D = 120; 1200 s from 10 m → required sink = 10/1200 m/s. Feed each lever back: height lever → bound equals target; uplift lever → `trajectory` duration equals target; wing-loading lever → `√(2(W/S)/ρ)/efficiency = h/t`; relaxed target → not infeasible | rel 1e-9 | partial (L/D 120 golden test) |
| CEM-V8 | Degenerate uncertainty | `uncertainty.py:uncertainty` | Calibration copy with every `log_sigma = 0` and a synthetic `result` dict: P10 = P50 = P90 = nominal `trajectory` value; each still-air sample has distance/height = `CL/CD` | rel 1e-12 | new (a seeded-band test exists) |

### 5.3 Group 2: needs the native AeroSandbox/NeuralFoil environment (T1/T2)

`cem.glider.model` does not import in bare Python (no `aerosandbox`). These run in the verify
image or CI.

| ID | Law / limit | Code under test | Independent oracle | Tolerance | Exists today |
| --- | --- | --- | --- | --- | --- |
| CEM-N1 | Trim equilibrium | `gliders/model/analysis.py:evaluate`, `fixed_geometry_trim` | From reported outputs: `½ρV²S·CL` vs `W cos γ`, `γ = atan(CD/CL)`; `Cm_trim ≈ 0`. The code solves `L = W`, so the expected gap is `1 − cos γ` (about 0.3 % at L/D 13) | lift gap ≤ `1 − cos γ` + 1e-6; `|Cm| ≤ 0.01` (the code's own admission floor) | new as a test; the `|Cm|` floor is enforced in code |
| CEM-N2 | Induced drag | `analysis.py:_aero` (`D_induced`) | `CDi ≥ CL²/(π AR)`; implied `e = CL²/(π AR CDi) ≤ 1` for the wing alone | upper bound 1.02; lower value recorded, not asserted | new |
| CEM-N3 | Station Reynolds | `model.py:evaluate_candidate` (`validity_envelope`) | `ρ V c / μ` recomputed per station from section chords and CEM-V1 air properties; minimum matches the reported one | rel 1e-9 | partial (station coverage in `test_cem_comparison`); recomputing from **delivered** geometry waits for § 7 slice 1 |
| CEM-N4 | Static margin sign | `analysis.py:evaluate` | `SM > 0 ⇔ Cmα < 0`; `(x_np − x_cg)/mac ≈ −Cmα/CLα` from a finite difference in α | sign exact; value rel 0.05 (estimate, fix on first run) | new as a test; sign is enforced in admission |
| CEM-N5 | Mass closure | `closure.py:verify`, `MeasuredGlider.mass_summary` | Σ(measured volume × density) + point masses = reported mass; injected 20 % error fails | rel 1e-9; existing 5 % closure gate | exists (`test_cem_pipeline`, `test_cem_foam_core`, `test_cem_segmented`) |
| CEM-N6 | Thin-airfoil and elliptic-wing limits | NeuralFoil section polar; `_aero` | Symmetric section lift slope near `2π` per rad at high Re; untwisted high-AR wing `CLα ≈ 2π·AR/(AR+2)`; elliptic limit `e → 1` | sanity band ±15 % (estimate, fix on first run and then freeze) | new |
| CEM-N7 | Independent solvers | `rechecks.py` (XFOIL stations, AVL derivatives) | Real XFOIL and AVL binaries | existing agreement screens | orchestration exists with test doubles (`test_cem_comparison`). Real-binary agreement is **not** established: Debian XFOIL ended with SIGFPE, AVL rejected the spiral screen, and the verify image points `MIRUM_XFOIL_BIN` at a mock. Stays `incomplete`; belongs to § 7 slice 2 |
| CEM-N8 | Determinism | search and export | Same seed → same result and identical STL bytes | exact | exists (`test_glider_search`, `test_cem_foam_core`) |

### 5.4 Steps and acceptance

1. Done with this plan: `backend/tests/test_cem_physics_verification.py` (CEM-V1…V8, § 5.5). T0 on every edit.
2. Add group 2 as `backend/tests/test_cem_physics_native.py` with
   `pytest.importorskip('aerosandbox')` (same pattern as `test_cem_pipeline.py`); a skip is
   acceptable only in bare Python, never in the verify image or CI.
3. Write `docs/implementation/cem-physics-verification.md`: the two tables, results, limits.
4. T2 (CEM + glider suite) once, gate (a), push, T3 in CI. Open the PR.

Acceptance:

- A failing physics check **fails closed**: it blocks the PR. No `xfail`, no skip in native runs.
- No tolerance, floor or constant in the code under test is loosened to make a check pass.
- A real discrepancy becomes either a bug fix (separate commit, with the failing test first) or
  an explicit limitation written into the doc and reviewed by the owner. CEM-N1's
  `L = W` versus `W cos γ` gap is the first known candidate for a written limitation.
- CEM-V does not add any performance or fabrication claim. `manufacturing_ready` stays false.

### 5.5 Results (2026-11-16)

Group 1 ran in bare Python 3.13 (numpy 2.5, pydantic 2.13, pytest 9.2, trimesh 5.2, scipy),
not in the pinned verify image: `python -m pytest -q backend/tests/test_cem_physics_verification.py`
→ **48 passed in 0.6 s**. To show the checks have teeth, each of 12 deliberate physics bugs was
injected into the code under test one at a time (then reverted); **every one made at least one
check fail**: launch `/(2g)` → `/g`; gas constant 287.05 → 300; wind drift halved; Sutherland
constant 110.4 → 120; elliptic moment integral 2/3 → 1/2; `spar_size` root moment `/(3π)` → `/(4π)`;
48-gon ratio computed for 40 sides; uncertainty speed ∝ W instead of √W; wing-loading lever
missing `/2`; uplift lever off by 10 %; height lever halved.

| ID | Result | Tolerance used | Notes |
| --- | --- | --- | --- |
| CEM-V1 | pass (7) | ρ rel 5e-4 vs ISA table at 0/1000/2000 m; μ rel 2e-3 | Pressure is ISA pressure-altitude, temperature is user-supplied: correct by design, noted as a modelling choice |
| CEM-V2 | pass (16) | pytest default rel 1e-6 | Includes headwind > airspeed → `no_forward_progress`, and fail-closed on V ≤ sink, negative height, NaN |
| CEM-V3 | pass (4) | rel 1e-6 | `G = 9.81` vs standard 9.80665 (0.04 %), within 5e-4 |
| CEM-V4 | pass (6) | direct quadrature rel 1e-4; shear and M'' by finite difference rel 1e-3 | Checked for every launch load factor |
| CEM-V5 | pass (6) | rel 1e-6; strict for "next smaller rod fails" | Converged rod also passes `verify_spar` on a measured 48-section mesh |
| CEM-V6 | pass (1) | rel 1e-12 | The 48-gon ratio `n sinθ(2+cosθ)/(6π)` equals the shoelace integral |
| CEM-V7 | pass (6) | rel 1e-6 | Lever closure for distance and duration, feasible and infeasible targets |
| CEM-V8 | pass (2) | exact collapse; spread check `0.20 < ln(P90/P10) < 0.32` | Mass-only sigma: range band collapses (L/D is weight-independent) and duration spread matches √W scaling; stable for seeds 1–39 |
| CEM-N1…N8 | not run | — | Need AeroSandbox/NeuralFoil (verify image or CI); this session has no Docker. CEM-N7 expected `incomplete` |

No discrepancy was found in the analytic layer, so no limitation or bug fix is recorded yet.
The `L = W` versus `W cos γ` trim gap (CEM-N1) remains the first candidate once group 2 runs.

## 6. Dragster D7 → D8 → D9

Detail lives in the subplans. Steps below are the single-agent order with tiers.

### 6.1 D7: finish draft PR #31 ([subplan](co2-dragster/D7-service-ui-v2.md))

Do not redo the implementation. Remaining work:

1. Merge `origin/main` (`b308034`) into the branch. Only `deploy/fly/README.md` and
   `frontend.fly.toml` changed on `main`; expect no conflict. T1 on the three v2 test files.
2. **Frontend image import check** (subplan frontend task 7): build `frontend/Dockerfile`
   with the scratch-Dockerfile CA trick (handoff § 14), then
   `docker run --rm mirum-frontend-d7 python -c "import dragster_ui"`. Never commit the scratch file.
3. **Browser check** (subplan § Browser check, handoff § 14): backend + frontend, empty `.env`,
   RAM gate ≥ 5 GB. Generate each preset, toggle wheel envelopes, load the v1 example, cancel an
   attempt, make an invalid edit. Screenshots to `docs/plans/co2-dragster/evidence/d7-*.png`.
4. T2 is **not** repeated unless step 1 changed code under test (PR #31 already records
   1271 passed). T3 = CI on the new head.
5. Security review: the report in PR #31 covers `679e4b6`; the later commit `5ba5686` only
   edits the two frontend Dockerfiles. Re-run `mirum-security-reviewer` only if endpoint code changes.

Exit: every box in the D7 subplan ticked with evidence; PR out of draft; STOP-M.

### 6.2 D8 backend ([subplan](co2-dragster/D8-text-routing.md) tasks 1–4, and task 6 files only)

1. `intent.py` + `test_dragster_intent.py` (≥ 30-row table). T0 only; stdlib.
2. `extraction.py` + `test_dragster_extraction.py` with a mocked provider. T0.
3. `POST /dragsters/interpret` and the ≤ 10-line early route in `_pipeline`; narrow the
   `rule_co2_dragster` keyword list. `test_dragster_interpret_api.py`. T1 for this file plus
   `test_classifier.py` and the existing pipeline tests.
4. `eval/prompts.json` + `eval/run_live.py`, **not run** (STOP-G). PR says "not run: awaiting
   owner approval".
5. T2 dragster suite once; gate (a); `--dry-run` note for gate (b) (§ 4.4); push; T3 in CI.
6. `mirum-security-reviewer` report covering the key path and the 503 path, attached to the PR.

Exit: D8 subplan exit criteria except UI and live sample. If D7 merged meanwhile, merge
`origin/main`, resolve the `backend/main.py` conflict (handoff § 10.1), re-run T1 for the
endpoint tests, and re-run the security review if endpoint code moved.

### 6.3 D8 UI (subplan task 5; only after D7 is on `main`)

1. Merge `origin/main` into the D8 branch.
2. `route` event handling in `frontend/app.py`; "Describe or refine" panel in
   `frontend/dragster_ui.py`; `frontend/test_dragster_text_ui.py` (AppTest, mocked backend). T0/T1.
3. T2 once more only for the frontend dragster files plus the interpret API file; T3 in CI.

Exit: remaining D8 boxes ticked; STOP-M.

### 6.4 D9 release gate ([subplan](co2-dragster/D9-release.md), adjusted)

1. `test_dragster_release_matrix.py` (subplan task 1). Develop one style at T1, then the whole
   matrix once at T2 on AC; record per-case seconds and peak RSS.
2. Docs (`v2-guide.md`, `release-v2-evidence.md`, legacy marks, README/architecture paragraph).
   Field tables are generated from `spec_v2` + `dragster_fields.py`. Also correct the stale
   master § 2 Deploy row and § 10 PR #19 risk row.
3. T3: CI on the branch; record the native-gate count against D0's 1580.
4. STOP-G: live text sample, once, if approved. Otherwise record "not run".
5. Visual review: side/top/iso screenshots of the three presets. STOP-P for the reference photo.
6. STOP-D: deploy from `main`. The PR #19 reconcile step no longer applies; ask "open or gated".
   Commands as in subplan task 7 (`--remote-only --depot=false`).
7. T4 live smoke (subplan task 8), only after step 6.

Exit: [master § 9](co2-dragster/00-master-plan.md#9-global-acceptance-checked-by-d9), with
deploy and live smoke either done or recorded as "awaiting owner".

## 7. CEM next work after CEM-V

From [cem-handoff § Recommended next work](../implementation/cem-handoff.md#recommended-next-work-with-acceptance-criteria),
updated by the later continuation docs. One PR-sized slice at a time; each new physical
quantity gets a CEM-V row before it is used.

| # | Slice | One-line acceptance |
| --- | --- | --- |
| 1 | **G2/G3 physical construction, one family.** The segmented polyhedral foam wing exists as a screen; remaining per [cem-segmented-wing § Next continuation](../implementation/cem-segmented-wing.md): foam-to-spar/sleeve bond interface and wing saddle with real material volume; physical tail/pod and payload/ballast cavities; aerodynamic sections recovered from delivered geometry | Injected 20 % mass error fails; open, extra or unaccounted parts fail; missing load-path evidence fails; final mass within the declared tolerance; `accepted_for_manufacture` stays false for every other family |
| 2 | **Independent solver checks and full uncertainty** | Missing solver, non-convergence or disagreement gives failed/unverified evidence, never a pass; an injected low-Re drag hole is caught before ranking; bands repeat with the seed. Needs a working real XFOIL (CEM-N7) |
| 3 | **G4 shape discovery** (new families, shared spanwise controls) | Deterministic archive; ≥ 3 qualified families; no unsupported family silently substituted; gains reported relative to uncertainty |
| 4 | **G5 manufacturing builders** (PicoGK parts spec, sheet/balsa templates) | Deterministic artifacts in one pinned image; measured mass within 5 %; 3 GiB worker cap; rejected builds produce no downloads |
| 5 | **G6/G7/G8 integration** (archive UI, live text extraction, flight data, thermal/ridge models) | End-to-end text → archive → build; ambiguous units return `needs_clarification`; old calibration IDs still reproduce |

Slices 2 and 5 include live-provider or real-flight evidence and carry their own STOP-G.

## 8. Reporting

After each step, one message: branch, head SHA, PR number, commands per tier with counts and
wall-clock, each exit criterion pass/fail with evidence, deviations, open questions.

## 9. Owner decisions needed

1. **Merge authority.** May the agent merge its own PRs after CI and (where required) the
   security review, and set master § 7 cells? Default: no.
2. **Where the agent runs.** Laptop with Docker (needed for the D7 frontend image and browser
   check) or a cloud session (T1–T3 through CI only)?
3. **Gate (b).** Is a live eval-harness slice wanted for D8's `_pipeline`/keyword change, and
   which slice? It spends Gemini quota.
4. **Live Gemini text sample** (15 LITE calls) for D8/D9: approve or skip.
5. **Showroom reference photo** for the D9 visual review.
6. **Deploy:** approve, and choose open access (current `main`) or gated.
7. **Baseline PMI findings** reported in PR #31 (two HIGH, one MEDIUM in `backend/main.py`):
   open a separate security workstream, or accept for now?
8. **PR #16** ("Cem integration verification", 494 files, conflicts with `main`) and draft
   **PR #18**: close, or keep? This plan does not touch them.
9. **CEM order.** Confirm § 7 slice 1 (physical construction) comes before more shape
   discovery. The CEM handoff notes shape discovery was chosen first once before.
