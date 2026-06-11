# Plan: Aerospace Design Closure Loop

**Date:** 2026-06-11
**Status:** reviewed + revised (xvant-plan-review findings applied; report appended at bottom)

## Strategic decision first: handoff vs pivot

**Verdict: neither continue the Worktree-A handoff nor pivot to something new. Close the loop that already exists.**

Rationale:

1. **Worktree A (backend-throughput) does not move aerospace capability.** Its own
   handoff (`output/worktree-A-handoff.md`) proved A-01..A-07, A-09..A-11 don't even
   affect the eval-harness pass rate — they're latency/UX plumbing. A-08 (prompt
   cache) is a cost optimization. None make output more usable, novel, or safe.
   Finish A later as infra hygiene; it is not the capability path.
2. **A full pivot would discard a nearly-complete stack.** The repo already has:
   XFOIL polars (`analysis/airfoil.py`), 3-D lifting-line + AVL (`analysis/lifting_line.py`,
   `analysis/avl.py`), structural margins with MIL-HDBK-5 allowables
   (`analysis/structures.py`), geometry certificates (`verification/geometry.py`),
   deterministic wing builders (`backend/cad_aero.py`), a Refiner agent
   (`agents.py:run_refiner`), and Domain D/F/G eval prompts.
3. **The gap is closure, not capability.** Today the pipeline generates geometry,
   analyzes it, and *reports* — `_run_aero_stage` / `_run_struct_stage` results ride
   along in the `complete` payload (`main.py:875`) and are never compared against
   what the user asked for. `StructuralReport.bending_passed` can be `False` and the
   pipeline still says "done". `DesignRequirements` (`schemas.py:604`) has **no
   performance-target fields at all**. "Usable, safe" aerospace output requires the
   design to be *checked against requirements*; "novel, creative" requires *iterating
   toward them*. That connective tissue is this plan.

```text
                        TODAY (open loop)
  prompt → REA → Planner → [Machinist+Critic]×N → Assembler → GLB
                                                       │
                                XFOIL polar ───────────┤  (advisory,
                                struct margins ────────┤   never read
                                geometry cert ─────────┘   by anyone)

                        THIS PLAN (closed loop)
  prompt → REA (+targets) → Planner → [Machinist+Critic]×N → Assembler
                                                       │
              ┌────────────────────────────────────────┤
              │   XFOIL polar + struct margins         │
              ▼                                        │
        ┌──────────┐  pass   ┌──────────────────────┐  │
        │ VERDICT  │────────▶│ complete: verdict ✅  │◀─┘
        │ targets  │         │ + reports + GLB      │
        │ vs actual│         └──────────────────────┘
        └────┬─────┘
             │ fail (and iterations left)
             ▼
        ┌──────────────────────────────┐
        │ build refinement prompt from │
        │ failed criteria (determinis- │
        │ tic lever table)             │
        └────┬─────────────────────────┘
             ▼
        run_refiner → re-export → re-analyze ──▶ back to VERDICT
             │
             │ fail (iterations exhausted)
             ▼
        complete: verdict ❌ + best-attempt GLB + failed-criteria list
        (NEVER withhold the GLB; closure failure is reported, not fatal)

  Error states:
   - XFOIL/struct stage errors → criterion marked "unevaluated", verdict
     overall = "incomplete", no refine loop on missing data
   - run_refiner LLM error → stop loop, report last verdict
   - re-export fails sandbox → stop loop, report last good GLB + verdict
```

## Goal

When a Domain-D prompt states performance requirements ("Cl_max ≥ 1.5",
"margin of safety ≥ 0.5", "L/D at least 20"), the pipeline extracts them,
verifies the generated wing against XFOIL + structural analysis, auto-refines
up to 2 times when a criterion fails, and delivers a machine-readable
pass/fail verdict alongside the GLB.

## Architecture

A new pure-function verdict module (`backend/verdict.py`) compares
`PolarReport` + `StructuralReport` against a new `DesignTargets` schema the
REA extracts. `main.py` gains a verdict stage after the existing
aero/struct stages and a bounded refine loop that reuses `run_refiner`.
Analysis stages stay advisory-never-raise; the verdict adds judgment, the
loop adds iteration, and the GLB is always delivered.

## Tech stack

Python 3.11, FastAPI/Pydantic (existing), XFOIL via `analysis/` (existing),
stdlib-runnable tests matching `backend/tests/test_jobs.py` convention.
No new dependencies.

## File map

| File | Status | Responsibility |
|------|--------|----------------|
| `backend/verdict.py` | new | Pure functions: targets + reports → `DesignVerdict`; aero lever table; deterministic wing-box re-sizing |
| `backend/closure.py` | new | Closure-loop orchestration with injected callbacks — shared by `main.py`, the `/jobs` path, and tests (NOT inline in the SSE generator) |
| `backend/tests/test_verdict.py` | new | Stdlib-runnable unit tests for verdict + re-sizing logic (no LLM, no XFOIL) |
| `backend/tests/test_design_closure.py` | new | `closure.py` loop behavior with stubbed analysis + stubbed refiner |
| `backend/schemas.py` | modified | `DesignTargets`, `CriterionResult`, `DesignVerdict` models; `design_targets` field on `DesignRequirements` |
| `backend/agents.py` | modified | REA structured-output schema gains `design_targets` extraction |
| `backend/main.py` | modified | Verdict stage + bounded refine loop in `/generate` SSE pipeline |
| `backend/eval_harness/prompts.json` | modified | 4 new Domain-D prompts with explicit targets (regression net for closure) |
| `backend/tests/test_propulsion.py` | modified (Phase 0) | Delete ImportError-dead rocket-helper tests |
| `backend/failure_analysis.py` | modified (Phase 0) | Prune stale `make_helical_cooling_channels` hint markers |

## Phase 0 — repair the safety net (prereq, ~30 min)

The propulsion handoff (`~/.claude/plans/propulsion-domain-f-handoff-2026-05-26.md` §6)
left the test suite lying. Closure work needs a trustworthy net first.

- [ ] **0.1 Fix `backend/tests/test_propulsion.py` ImportErrors.**
  Run `python backend/tests/test_propulsion.py` (or pytest) — confirm the failures are
  the documented `from cad_utils import make_rao_bell_nozzle / make_conical_nozzle /
  make_injector_plate / make_helical_cooling_channels` (all deleted in the aerospace
  pivot). Delete those test functions; keep `make_combustion_chamber` tests. Re-run → green.
- [ ] **0.2 Prune stale hints in `backend/failure_analysis.py`.**
  Remove `make_helical_cooling_channels` / `make_rao_bell_nozzle` from
  `_PROPULSION_CODE_MARKERS` and the `cooling_channels` entry in `OP_TO_RULE_HINT`
  (they reference deleted helpers; the hint can never fire usefully). Run existing
  failure-analysis tests.
- [ ] **0.3 Re-baseline the full corpus** (needs `GEMINI_API_KEY_HEAVY`, cadenv
  container, host-CA-bundle mount per memory `reference_mirum_docker_ssl`):
  `python -m eval_harness.runner --report-md output/qa-reports/2026-06-11-full.md`.
  Diff vs the 2026-05-22 59/64 baseline. This is the "before" snapshot the closure
  work will be measured against. Park (don't fix) any B/C failures.

## Phase 1 — targets in, verdict out (pure logic, fully testable offline)

- [ ] **1.1 Write failing test for `DesignTargets` schema** —
  `backend/tests/test_verdict.py`:

  ```python
  """Stdlib-runnable: python backend/tests/test_verdict.py"""
  import sys, pathlib
  sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

  def test_design_targets_schema():
      from schemas import DesignTargets
      t = DesignTargets(cl_max_min=1.5, ms_min=0.5)
      assert t.cl_max_min == 1.5
      assert t.l_d_min is None          # unset criteria are None
      assert t.ms_min == 0.5

  def test_design_targets_rejects_negative_cl():
      from schemas import DesignTargets
      try:
          DesignTargets(cl_max_min=-1.0)
          assert False, "should reject"
      except Exception:
          pass
  ```

  Run: `python backend/tests/test_verdict.py` → `ImportError: cannot import name 'DesignTargets'`. ✔ fails.

- [ ] **1.2 Add `DesignTargets` to `backend/schemas.py`** (after `WingBoxSpec`, ~line 393):

  ```python
  class DesignTargets(BaseModel):
      """Domain D: quantitative pass/fail criteria extracted from the prompt.

      Every field is optional; only criteria the user actually stated are
      populated. None = not a requirement = not checked.
      """

      cl_max_min:      Optional[float] = Field(default=None, gt=0.0, lt=5.0,
          description="Minimum acceptable Cl_max from the XFOIL polar.")
      l_d_min:         Optional[float] = Field(default=None, gt=0.0, lt=200.0,
          description="Minimum acceptable peak L/D from the XFOIL polar.")
      stall_alpha_min: Optional[float] = Field(default=None, gt=0.0, lt=30.0,
          description="Minimum acceptable stall angle of attack, degrees.")
      ms_min:          Optional[float] = Field(default=None, ge=0.0, lt=10.0,
          description="Minimum structural margin of safety (bending AND shear).")
      v_ne_min_ms:     Optional[float] = Field(default=None, gt=0.0, lt=1000.0,
          description="Minimum never-exceed speed, m/s.")
  ```

  And on `DesignRequirements` (after `wing_box_spec`):

  ```python
  design_targets: Optional[DesignTargets] = Field(
      default=None,
      description=(
          "Domain D only: quantitative performance criteria the design must "
          "meet (min Cl_max, min L/D, min margin of safety, min stall alpha, "
          "min V_ne). Populate ONLY criteria the prompt states explicitly."
      ),
  )
  ```

  Run test → pass. Commit: `feat(schemas): DesignTargets criteria model`.

- [ ] **1.3 Write failing tests for verdict logic** — append to `test_verdict.py`:

  ```python
  POLAR = {"Cl_max": 1.62, "stall_alpha": 14.5,
           "L_D": [10.0, 18.0, 22.5, 19.0], "converged_count": 18}
  STRUCT = {"bending_margin_of_safety": 0.41, "shear_margin_of_safety": 1.2,
            "bending_passed": True, "shear_passed": True, "v_ne_ms": 120.0}

  def test_verdict_all_pass():
      from schemas import DesignTargets
      from verdict import evaluate_design
      v = evaluate_design(DesignTargets(cl_max_min=1.5, ms_min=0.2), POLAR, STRUCT)
      assert v.overall == "pass"
      assert {c.name for c in v.criteria} == {"cl_max_min", "ms_min"}
      assert all(c.passed for c in v.criteria)

  def test_verdict_fail_lists_failed_criterion():
      from schemas import DesignTargets
      from verdict import evaluate_design
      v = evaluate_design(DesignTargets(ms_min=0.5), POLAR, STRUCT)
      assert v.overall == "fail"
      failed = [c for c in v.criteria if not c.passed]
      assert failed[0].name == "ms_min"
      assert failed[0].actual == 0.41      # min(bending, shear)
      assert failed[0].target == 0.5

  def test_verdict_missing_report_is_incomplete_not_fail():
      from schemas import DesignTargets
      from verdict import evaluate_design
      v = evaluate_design(DesignTargets(cl_max_min=1.5), None, STRUCT)
      assert v.overall == "incomplete"
      assert v.criteria[0].passed is None   # unevaluated, not failed

  def test_verdict_no_targets_is_not_applicable():
      from verdict import evaluate_design
      v = evaluate_design(None, POLAR, STRUCT)
      assert v.overall == "not_applicable"
      assert v.criteria == []
  ```

  Run → `ModuleNotFoundError: verdict`. ✔ fails.

- [ ] **1.4 Add `CriterionResult` + `DesignVerdict` to `schemas.py`** (after `DesignTargets`):

  ```python
  class CriterionResult(BaseModel):
      name:   str                       # e.g. "cl_max_min"
      target: float
      actual: Optional[float] = None    # None when source report missing
      passed: Optional[bool] = None     # None = unevaluated
      source: str                       # "polar" | "struct"

  class DesignVerdict(BaseModel):
      """Pass/fail judgment of analysis reports vs DesignTargets.

      overall: "pass" | "fail" | "incomplete" | "not_applicable"
      incomplete = at least one criterion couldn't be evaluated and none failed.
      """
      overall:  str
      criteria: list[CriterionResult] = Field(default_factory=list)
      iterations_used: int = 0
  ```

- [ ] **1.5 Implement `backend/verdict.py`** (minimal, makes 1.3 pass):

  ```python
  """Pure design-verdict logic: DesignTargets + analysis reports → DesignVerdict.

  No LLM calls, no subprocess, no I/O. Reports arrive as plain dicts
  (model_dump() output of PolarReport / StructuralReport) so this module
  stays import-light and stdlib-testable.
  """
  from __future__ import annotations
  from schemas import CriterionResult, DesignTargets, DesignVerdict

  # (criterion field, source report, extractor) — extractor returns None if
  # the value is absent from the report (e.g. XFOIL never converged).
  _CRITERIA = [
      ("cl_max_min",      "polar",  lambda p: p.get("Cl_max")),
      # L_D arrives through _sanitize_floats: unconverged points are None, not
      # NaN — bare max() would TypeError. Filter first.
      ("l_d_min",         "polar",  lambda p: max(
          (x for x in p.get("L_D") or [] if x is not None), default=None)),
      ("stall_alpha_min", "polar",  lambda p: p.get("stall_alpha")),
      ("ms_min",          "struct", lambda s: min(
          s["bending_margin_of_safety"], s["shear_margin_of_safety"])
          if "bending_margin_of_safety" in s else None),
      ("v_ne_min_ms",     "struct", lambda s: s.get("v_ne_ms")),
  ]

  def evaluate_design(
      targets: DesignTargets | None,
      polar: dict | None,
      struct: dict | None,
  ) -> DesignVerdict:
      if targets is None:
          return DesignVerdict(overall="not_applicable")
      reports = {"polar": polar, "struct": struct}
      criteria: list[CriterionResult] = []
      for name, source, extract in _CRITERIA:
          target = getattr(targets, name)
          if target is None:
              continue
          report = reports[source]
          actual = extract(report) if report is not None else None
          passed = (actual >= target) if actual is not None else None
          criteria.append(CriterionResult(
              name=name, target=target, actual=actual,
              passed=passed, source=source))
      if not criteria:
          return DesignVerdict(overall="not_applicable")
      if any(c.passed is False for c in criteria):
          overall = "fail"
      elif any(c.passed is None for c in criteria):
          overall = "incomplete"
      else:
          overall = "pass"
      return DesignVerdict(overall=overall, criteria=criteria)
  ```

  Run `python backend/tests/test_verdict.py` → all pass. Commit:
  `feat(verdict): evaluate_design pure verdict module`.

- [ ] **1.6 REA extracts targets — failing test first** (append to `test_verdict.py`;
  this tests the *schema dict*, not a live LLM call):

  ```python
  def test_rea_schema_includes_design_targets():
      import agents
      schema = agents._REA_RESPONSE_SCHEMA  # the structured-output dict
      props = schema["properties"]
      assert "design_targets" in props
      dt = props["design_targets"]["properties"]
      assert set(dt) == {"cl_max_min", "l_d_min", "stall_alpha_min",
                         "ms_min", "v_ne_min_ms"}
  ```

  (If the REA schema dict has a different module-level name, locate it via
  `grep -n "aircraft_spec" backend/agents.py` — it's the same structured-output
  dict that already declares `aircraft_spec`/`wing_box_spec`, around `agents.py:320-410` —
  and adjust the test to that name. The assertion content stays identical.)

- [ ] **1.7 Add `design_targets` to the REA structured-output schema in `agents.py`**
  alongside the existing `aircraft_spec` block (~line 328 pattern):

  ```python
  "design_targets": {
      "type": "object",
      "description": (
          "Quantitative pass/fail criteria ONLY when the prompt states them "
          "numerically (e.g. 'Cl_max of at least 1.5', 'margin of safety "
          "0.5 or better', 'L/D above 20'). NEVER invent thresholds."
      ),
      "properties": {
          "cl_max_min":      {"type": "number"},
          "l_d_min":         {"type": "number"},
          "stall_alpha_min": {"type": "number"},
          "ms_min":          {"type": "number"},
          "v_ne_min_ms":     {"type": "number"},
      },
  },
  ```

  Plus one sentence in the REA system prompt's Domain-D paragraph (mirroring the
  existing `cl_max ONLY if stated` phrasing at `agents.py:408`):
  `"If the prompt states minimum performance requirements, populate design_targets — never guess."`
  Run test → pass. Commit: `feat(rea): extract design_targets`.

## Phase 2 — verdict visible in the pipeline (advisory, no loop yet)

- [ ] **2.1 Failing test** — `backend/tests/test_design_closure.py`, testing the
  helper (not the SSE generator) so it runs offline:

  ```python
  """Stdlib-runnable: python backend/tests/test_design_closure.py"""
  import sys, pathlib, types
  sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

  def test_verdict_stage_returns_verdict_dict():
      from schemas import DesignTargets, DesignRequirements
      import main
      req = DesignRequirements(
          primary_function="test wing", inferred_domain="D",
          design_targets=DesignTargets(cl_max_min=1.5))
      polar = {"Cl_max": 1.7, "L_D": [15.0], "stall_alpha": 13.0,
               "converged_count": 10}
      out, msg = main._run_verdict_stage(req, polar, None)
      assert out is not None and out["overall"] == "pass"
      assert "1/1 criteria" in msg
  ```

- [ ] **2.2 Implement `_run_verdict_stage` in `main.py`** (beside `_run_struct_stage`,
  same always-returns-never-raises contract):

  ```python
  def _run_verdict_stage(
      requirements, aero_report: dict | None, struct_report: dict | None,
  ) -> tuple[dict | None, str]:
      """Phase 5: compare analysis reports against extracted DesignTargets.

      Pure-number judgment — no LLM, no subprocess. Returns
      (DesignVerdict.model_dump() | None, status_msg). None = not applicable.
      """
      targets = getattr(requirements, "design_targets", None) if requirements else None
      if targets is None:
          return None, "no design_targets extracted"
      try:
          from verdict import evaluate_design  # noqa: PLC0415
          v = evaluate_design(targets, aero_report, struct_report)
      except Exception as e:  # never block delivery
          return None, f"verdict failed: {type(e).__name__}: {_safe_error(str(e))}"
      if v.overall == "not_applicable":
          return None, "no criteria to evaluate"
      n_pass = sum(1 for c in v.criteria if c.passed)
      msg = f"{v.overall}: {n_pass}/{len(v.criteria)} criteria met"
      return v.model_dump(), msg
  ```

- [ ] **2.3 Targets imply analysis — auto-enable the stages.** A user who states
  "MS ≥ 0.5" has opted into structural analysis; making them also pass
  `run_struct=1` guarantees an `incomplete` verdict (review finding R5). At the
  top of the aero/struct block in `_pipeline` (`main.py:733`):

  ```python
  # Stated performance targets imply consent to the analyses that check them.
  _targets = getattr(requirements, "design_targets", None) if requirements else None
  if _targets is not None:
      if any(getattr(_targets, f) is not None
             for f in ("cl_max_min", "l_d_min", "stall_alpha_min")):
          run_aero = True
      if any(getattr(_targets, f) is not None for f in ("ms_min", "v_ne_min_ms")):
          run_struct = True
  ```

- [ ] **2.4 Wire the verdict into the SSE pipeline** after the struct stage
  (`main.py:781`):

  ```python
  # Phase 5: design verdict (Domain D, only when targets were extracted).
  design_verdict: dict | None = None
  if run_aero or run_struct:
      design_verdict, verdict_msg = _run_verdict_stage(
          requirements, aero_report, struct_report)
      if design_verdict is not None:
          status = "done" if design_verdict["overall"] == "pass" else "warning"
          yield _event("verdict", status, verdict_msg)
  ```

  And in the `complete` payload dict (`main.py:866-877` block):
  `"design_verdict": design_verdict,`
  Run tests + schema suite (`python backend/tests/test_prompts_schema.py` etc.) → green.
  Commit: `feat(pipeline): design-verdict stage in /generate`.

## Phase 3 — close the loop (bounded auto-refine)

**Review-revised structure.** Two criterion families, two mechanisms:

- **Struct criteria (`ms_min`) — deterministic spec re-sizing, zero LLM.**
  `_run_struct_stage(requirements)` is pure-number from
  `requirements.wing_box_spec` (`main.py:295-388`); prompting the Refiner to
  "increase spar cap" can NEVER change its output (review finding R1). The
  margin formula is analytic, so we *solve* for the required cap area instead
  of iterating: MS = allowable/stress − 1 and stress ∝ 1/cap_area, so
  scaling cap area by (1+MS_target)/(1+MS_actual) meets the target in one step.
- **Aero criteria (`cl_max_min`, `l_d_min`, `stall_alpha_min`) — LLM refine +
  XFOIL re-run**, bounded at 2 iterations (geometry → polar has no closed form).
- **`v_ne_min_ms` is check-only.** V_NE comes from the flight envelope
  (aircraft spec + category), not from wing structure — no geometry lever
  exists. It reports pass/fail and never triggers refinement (review R4).

The loop lives in `backend/closure.py`, dependency-injected — because the eval
harness calls `run_critic_loop` directly and never touches `main.py` (the
exact trap the Worktree-A handoff documented), anything embedded in the SSE
generator would be invisible to gate (b) and to tests (review R2).

- [ ] **3.1 Failing tests: aero lever table + struct exclusion** (append to
  `test_verdict.py`):

  ```python
  def test_refinement_prompt_names_aero_criterion_and_lever():
      from schemas import CriterionResult, DesignVerdict
      from verdict import refinement_prompt_for
      v = DesignVerdict(overall="fail", criteria=[
          CriterionResult(name="cl_max_min", target=1.5, actual=1.31,
                          passed=False, source="polar"),
      ])
      p = refinement_prompt_for(v)
      assert "Cl_max" in p and "1.31" in p and "1.5" in p
      assert "camber" in p            # the engineering lever

  def test_refinement_prompt_skips_struct_and_vne_criteria():
      from schemas import CriterionResult, DesignVerdict
      from verdict import refinement_prompt_for
      v = DesignVerdict(overall="fail", criteria=[
          CriterionResult(name="ms_min", target=0.5, actual=0.41,
                          passed=False, source="struct"),
          CriterionResult(name="v_ne_min_ms", target=150.0, actual=120.0,
                          passed=False, source="struct"),
      ])
      assert refinement_prompt_for(v) == ""   # nothing LLM-fixable
  ```

- [ ] **3.2 Implement `refinement_prompt_for` in `verdict.py`** — aero levers
  only; struct is handled deterministically in 3.3:

  ```python
  # LLM-refinable criteria only. ms_min is solved analytically by
  # resize_wing_box_for_margin (spec-level, no LLM); v_ne_min_ms has no
  # geometry lever at all (envelope is aircraft-spec-driven) — check-only.
  _AERO_LEVERS = {
      "cl_max_min": (
          "peak lift coefficient Cl_max",
          "increase airfoil camber (e.g. NACA 2412 → 4412) or increase "
          "wing chord; do not change span"),
      "l_d_min": (
          "peak lift-to-drag ratio L/D",
          "increase aspect ratio: extend span and reduce chord at constant area"),
      "stall_alpha_min": (
          "stall angle of attack",
          "reduce camber slightly or add washout (negative tip twist)"),
  }

  def refinement_prompt_for(verdict: DesignVerdict) -> str:
      """Failed AERO criteria → one NL instruction for run_refiner.

      Returns "" when no failed criterion is LLM-fixable (caller skips the
      refine call entirely).
      """
      items = []
      for c in verdict.criteria:
          if c.passed is False and c.name in _AERO_LEVERS:
              quantity, lever = _AERO_LEVERS[c.name]
              items.append(
                  f"- {quantity} is {c.actual:.3g}, required ≥ {c.target:.3g}. "
                  f"Fix: {lever}.")
      if not items:
          return ""
      return "\n".join(
          ["The generated design fails its stated performance requirements. "
           "Apply the SMALLEST geometry change that fixes each item:"]
          + items
          + ["Keep every dimension not named above unchanged."])
  ```

  Run → pass. Commit: `feat(verdict): aero criterion→lever refinement prompts`.

- [ ] **3.3 Deterministic wing-box re-sizing — failing test then impl.**
  Test (append to `test_verdict.py`):

  ```python
  def test_resize_wing_box_scales_cap_area_to_meet_margin():
      from schemas import DesignTargets, WingBoxSpec
      from verdict import resize_wing_box_for_margin
      wb = WingBoxSpec(chord_root_m=1.1, thickness_to_chord=0.12,
                       cap_area_m2=0.0008, spar_web_thickness_m=0.002)
      struct = {"bending_margin_of_safety": 0.2, "shear_margin_of_safety": 1.0}
      new = resize_wing_box_for_margin(DesignTargets(ms_min=0.5), struct, wb)
      assert new is not None
      assert abs(new.cap_area_m2 - 0.0008 * (1.5 / 1.2)) < 1e-9
      assert new.spar_web_thickness_m == wb.spar_web_thickness_m  # shear already OK

  def test_resize_wing_box_none_when_margins_already_met():
      from schemas import DesignTargets, WingBoxSpec
      from verdict import resize_wing_box_for_margin
      wb = WingBoxSpec(chord_root_m=1.1, thickness_to_chord=0.12,
                       cap_area_m2=0.0008, spar_web_thickness_m=0.002)
      struct = {"bending_margin_of_safety": 0.9, "shear_margin_of_safety": 1.0}
      assert resize_wing_box_for_margin(DesignTargets(ms_min=0.5), struct, wb) is None
  ```

  Implementation (in `verdict.py`):

  ```python
  def resize_wing_box_for_margin(targets, struct: dict | None, wing_box):
      """Analytically re-size spar caps/webs so MS meets targets.ms_min.

      MS = allowable/stress - 1 and stress ∝ 1/section_property, so scaling
      the property by (1 + MS_target)/(1 + MS_actual) hits the target exactly
      (within the analysis model). Returns an updated WingBoxSpec copy, or
      None when nothing needs resizing / inputs are missing.
      """
      if targets is None or targets.ms_min is None or struct is None:
          return None
      need = 1.0 + targets.ms_min
      updates: dict = {}
      ms_b = struct.get("bending_margin_of_safety")
      if ms_b is not None and ms_b < targets.ms_min and ms_b > -1.0:
          updates["cap_area_m2"] = wing_box.cap_area_m2 * (need / (1.0 + ms_b))
      ms_s = struct.get("shear_margin_of_safety")
      if ms_s is not None and ms_s < targets.ms_min and ms_s > -1.0:
          updates["spar_web_thickness_m"] = (
              wing_box.spar_web_thickness_m * (need / (1.0 + ms_s)))
      return wing_box.model_copy(update=updates) if updates else None
  ```

  (The `> -1.0` guard: MS ≤ −1 means stress ≥ ∞·allowable in the formula —
  degenerate input; refuse to resize rather than emit a negative scale.)
  Run → pass. Commit: `feat(verdict): analytic wing-box re-sizing`.

- [ ] **3.4 Failing loop-behavior tests** — `backend/tests/test_design_closure.py`,
  now targeting `closure.py` (no `import main` needed, review R2/R6):

  ```python
  """Stdlib-runnable: python backend/tests/test_design_closure.py"""
  import sys, pathlib, asyncio
  sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

  from schemas import DesignTargets, DesignRequirements

  def _req(**targets):
      return DesignRequirements(primary_function="w", inferred_domain="D",
                                design_targets=DesignTargets(**targets))

  def test_loop_stops_after_max_iterations():
      from closure import run_design_closure
      calls = {"refine": 0}
      async def fake_refine_aero(prompt):
          calls["refine"] += 1
          return {"Cl_max": 1.2, "L_D": [9.0], "stall_alpha": 11.0,
                  "converged_count": 8}                       # still failing
      async def fake_rerun_struct(requirements):
          raise AssertionError("no struct targets — must not be called")
      polar = {"Cl_max": 1.1, "L_D": [8.0], "stall_alpha": 10.0,
               "converged_count": 8}
      verdict, changed = asyncio.run(run_design_closure(
          _req(cl_max_min=1.5), polar, None,
          refine_aero=fake_refine_aero, rerun_struct=fake_rerun_struct,
          max_iters=2))
      assert calls["refine"] == 2
      assert verdict["overall"] == "fail" and verdict["iterations_used"] == 2

  def test_loop_zero_iterations_when_pass():
      from closure import run_design_closure
      async def boom(_): raise AssertionError("must not refine")
      polar = {"Cl_max": 1.7, "L_D": [20.0], "stall_alpha": 14.0,
               "converged_count": 12}
      verdict, changed = asyncio.run(run_design_closure(
          _req(cl_max_min=1.5), polar, None,
          refine_aero=boom, rerun_struct=boom, max_iters=2))
      assert verdict["overall"] == "pass"
      assert verdict["iterations_used"] == 0 and changed is False

  def test_struct_fail_resolved_by_resize_without_llm():
      from closure import run_design_closure
      async def boom(_): raise AssertionError("struct fix must not call LLM")
      reruns = {"n": 0}
      async def fake_rerun_struct(requirements):
          reruns["n"] += 1
          return {"bending_margin_of_safety": 0.52,
                  "shear_margin_of_safety": 1.0, "v_ne_ms": 120.0}
      struct = {"bending_margin_of_safety": 0.2,
                "shear_margin_of_safety": 1.0, "v_ne_ms": 120.0}
      req = _req(ms_min=0.5)
      req.wing_box_spec = __import__("schemas").WingBoxSpec(
          chord_root_m=1.1, thickness_to_chord=0.12,
          cap_area_m2=0.0008, spar_web_thickness_m=0.002)
      verdict, changed = asyncio.run(run_design_closure(
          req, None, struct,
          refine_aero=boom, rerun_struct=fake_rerun_struct, max_iters=2))
      assert reruns["n"] == 1
      assert verdict["overall"] == "pass" and changed is True

  if __name__ == "__main__":
      for name, fn in sorted(globals().items()):
          if name.startswith("test_"):
              fn(); print(f"PASS {name}")
  ```

- [ ] **3.5 Implement `backend/closure.py`** — the orchestration, callbacks
  injected:

  ```python
  """Design-closure orchestration: verdict → fix → re-analyze, bounded.

  Pure control flow — every side effect (LLM refine, geometry re-export,
  XFOIL, struct math) arrives as an injected async callback so main.py, the
  /jobs path, the eval harness, and tests share one implementation.
  Never raises: every failure path degrades to "return the last verdict".
  """
  from __future__ import annotations
  from verdict import (evaluate_design, refinement_prompt_for,
                       resize_wing_box_for_margin)

  async def run_design_closure(
      requirements,
      aero_report: dict | None,
      struct_report: dict | None,
      *,
      refine_aero,        # async (prompt: str) -> new_aero_report | None
      rerun_struct,       # async (requirements) -> new_struct_report | None
      max_iters: int = 2,
  ) -> tuple[dict | None, bool]:
      """Returns (DesignVerdict dict | None, geometry_changed flag)."""
      targets = getattr(requirements, "design_targets", None)
      verdict = evaluate_design(targets, aero_report, struct_report)
      if verdict.overall in ("not_applicable",):
          return None, False
      changed = False
      iters = 0

      # --- Struct criteria: one analytic re-size, no LLM, free ---
      if any(c.passed is False and c.source == "struct"
             for c in verdict.criteria):
          wb = getattr(requirements, "wing_box_spec", None)
          new_wb = (resize_wing_box_for_margin(targets, struct_report, wb)
                    if wb is not None else None)
          if new_wb is not None:
              requirements.wing_box_spec = new_wb
              try:
                  new_struct = await rerun_struct(requirements)
              except Exception:
                  new_struct = None
              if new_struct is not None:
                  struct_report = new_struct
                  changed = True
              verdict = evaluate_design(targets, aero_report, struct_report)

      # --- Aero criteria: LLM refine + re-analyze, bounded ---
      while verdict.overall == "fail" and iters < max_iters:
          prompt = refinement_prompt_for(verdict)
          if not prompt:                       # nothing LLM-fixable left
              break
          try:
              new_aero = await refine_aero(prompt)
          except Exception:
              break
          if new_aero is None:
              break
          aero_report = new_aero
          changed = True
          iters += 1
          verdict = evaluate_design(targets, aero_report, struct_report)

      out = verdict.model_dump()
      out["iterations_used"] = iters
      return out, changed
  ```

  Run `python backend/tests/test_design_closure.py` → all pass. Commit:
  `feat(closure): injected design-closure orchestration`.

- [ ] **3.6 Wire `closure.py` into the SSE generator** — replaces the advisory
  verdict call from 2.4. Two callback sets, selected by the existing
  `single` flag (single-part wings have no manifest-diff path — review R7):

  ```python
  design_verdict: dict | None = None
  if (run_aero or run_struct) and requirements is not None:
      from closure import run_design_closure  # noqa: PLC0415

      async def _rerun_struct(reqs):
          report, _ = await _run_struct_stage(reqs)
          return report

      _final_step: dict = {}                    # closure may swap geometry

      if single:
          async def _refine_aero(rp: str):
              # No manifest to diff — regenerate this one part with the
              # lever instruction appended, then re-export a STEP for XFOIL
              # (same pattern as the _aero_step_path block above).
              new_code, _sp, _cert = await run_critic_loop(
                  f"{prompt}\n\nREFINEMENT REQUIRED:\n{rp}",
                  max_retries=2, save_step=False)
              if not new_code:
                  return None
              _rs_path = str(_WORK_DIR / (uuid.uuid4().hex + "_closure.step"))
              _rs_exec = await execute_cad_script(
                  f"{new_code}\n\nimport cadquery as cq\n"
                  f"cq.exporters.export(result, r'{_rs_path}')\n")
              if _rs_exec["status"] != "success" or not os.path.exists(_rs_path):
                  return None
              _final_step["code"] = new_code
              report, _ = await _run_aero_stage(
                  {"part": _rs_path}, requirements)
              try: os.remove(_rs_path)
              except OSError: pass
              return report
      else:
          async def _refine_aero(rp: str):
              diff = await run_refiner(manifest, part_scripts, rp)
              new_steps = await _apply_diff_and_export(
                  diff, manifest, part_scripts, step_files)
              if not new_steps:
                  return None
              _final_step["steps"] = new_steps
              report, _ = await _run_aero_stage(new_steps, requirements)
              return report

      yield _event("verdict", "running", "Checking design against targets")
      design_verdict, _closure_changed = await run_design_closure(
          requirements, aero_report, struct_report,
          refine_aero=_refine_aero, rerun_struct=_rerun_struct)
      if design_verdict is not None:
          status = "done" if design_verdict["overall"] == "pass" else "warning"
          yield _event(
              "verdict", status,
              f"{design_verdict['overall']} after "
              f"{design_verdict['iterations_used']} refinement(s)")
  ```

  `_apply_diff_and_export` is the existing `/refine` endpoint's
  diff-application path (`main.py:1142` `refine_assembly`) extracted into a
  reusable coroutine — this task includes that extraction, exactly like
  `run_pipeline_into` was extracted in Worktree A commit `c92330e`.
- [ ] **3.7 Re-deliver the refined GLB.** The GLB bytes are read and the file
  deleted at `main.py:723-725`, *before* the closure loop runs — when
  `_closure_changed` and `_final_step` holds new geometry, the `complete`
  payload must carry the refined GLB, not the stale one (review R3). After
  the closure block, before `complete_payload` (`main.py:866`):

  ```python
  if design_verdict is not None and _closure_changed and _final_step:
      _new_glb = str(_WORK_DIR / (uuid.uuid4().hex + "_closure.glb"))
      _src = _final_step.get("code") or final_script
      _re_exec = await execute_cad_script(
          f"{_src}\n\nimport cadquery as cq\n"
          f"cq.exporters.export(result, r'{_new_glb}', "
          f"cq.exporters.ExportTypes.GLTF)\n")
      if _re_exec["status"] == "success" and os.path.exists(_new_glb):
          with open(_new_glb, "rb") as f:
              glb_b64 = base64.b64encode(f.read()).decode("utf-8")
          final_script = _src
          try: os.remove(_new_glb)
          except OSError: pass
      # Export failure → keep the original GLB; the verdict still reports
      # honestly which criteria the *original* geometry failed.
  ```

  (Multi-part: `_apply_diff_and_export` in 3.6 already lands refreshed STEP
  files; reassemble the GLB via the same Assembler export path `/refine` uses —
  part of the 3.6 extraction. Verify the exact `ExportTypes` enum name against
  `exporters.py` before coding; the existing GLB export at `main.py:~690`
  is the authority.)
- [ ] **3.8 Telemetry.** One JSONL record per closure run via the existing
  `telemetry.py` append-only pattern: `{"event": "design_closure", "overall":
  ..., "iterations_used": ..., "criteria": [...], "geometry_changed": ...}`.
  Without this we cannot tell whether closure ever rescues a design in the
  wild (review R8). Follow the field style of existing records in
  `telemetry.py`; debug-log only, never returned to clients.
- [ ] **3.9 Security checkpoint.** This phase touches `main.py` endpoints →
  per `.claude/rules/worktree-merge-gate.md` gate (c): walk
  `mirum-security-review` checklist (new code paths: refinement prompt is
  derived from internal verdict numbers, not raw user text → still passes
  through the existing sandbox/compile path; no new env/key exposure; loop
  bounded at 2 LLM calls + 1 analytic resize so no DoS amplification; new
  scratch files stay under `_WORK_DIR`). Dispatch `mirum-security-reviewer`
  before merge.

## Phase 4 — regression net for closure

**Honesty note (review R2):** the eval harness calls `run_critic_loop`
directly and bypasses `main.py` entirely — the Worktree-A handoff's central
realisation. Gate (b) therefore verifies that the new prompts still produce
*valid geometry*; it does NOT exercise the verdict/closure path. Closure
correctness is owned by the stdlib tests (Phases 1-3, offline, deterministic)
plus the live end-to-end check (4.3). Extending the runner to score verdicts
is deliberately deferred (see Out of scope) — don't pretend gate (b) covers it.

- [ ] **4.1 Add 5 Domain-D prompts** to `backend/eval_harness/prompts.json`,
  ids `D_closure_*`, `regression_for: ["D"]`:

  ```json
  {
    "id": "D_closure_trainer_wing",
    "domain": "D",
    "regression_for": ["D"],
    "prompt": "Design a 2-spar wing box for a 600 kg trainer, NACA 2412, span 8 m, chord 1.1 m, cruise 55 m/s at 2000 m, AL2024-T3. The structural margin of safety must be at least 0.5 and Cl_max at least 1.4."
  }
  ```

  Siblings: one L/D target, one stall-alpha target, one with an unmeetable
  target (Cl_max ≥ 4.0 — geometry must still generate; verdict honesty is
  checked live in 4.3), and one **negative control** (Domain-D wing prompt
  with *no* numeric requirements — guards against REA inventing targets;
  checked in 4.3 by asserting `design_verdict` is `null`).
  Run `python backend/tests/test_prompts_schema.py` → green.
- [ ] **4.2 Gate (b) run:** `python -m eval_harness.runner --regression-for D
  --report-md output/qa-reports/2026-06-11-D.md` in the cadenv container.
  No newly-failing ids vs the Phase-0 baseline. (Scope: geometry validity
  only, per the honesty note.)
- [ ] **4.3 Live end-to-end** (`docker compose up`, cadenv + CA-bundle mount):
  1. POST `D_closure_trainer_wing`'s prompt to `/generate` — SSE shows
     `verdict: running → done/warning`; `complete` carries `design_verdict`
     with a criteria array; GLB renders.
  2. POST the unmeetable-target prompt — `design_verdict.overall == "fail"`,
     `iterations_used == 2`, GLB still delivered.
  3. POST the negative-control prompt — `design_verdict` is `null` (REA did
     not invent targets).
  4. Watch total wall time: the closure loop adds up to 2 LLM+XFOIL rounds.
     If a run approaches the frontend's 180 s read timeout, that is the
     signal to prioritize Worktree A-06/`/jobs` polling for closure-enabled
     requests (review R9) — record the timing in the run notes either way.

## Out of scope (next plan, builds on this one)

- **Design exploration for novelty** (k-candidate planform sweep via
  `cad_aero.canonical_wing_parts` + cheap `prandtl_lifting_line` scoring,
  rank by verdict margin, return best + trade table). Needs `evaluate_design`
  from Phase 1 as its fitness function — that's why closure ships first.
- Worktree A remaining tasks (A-06..A-12) — infra, unchanged priority.
- Phase-2 synthetic-data corpus re-scope (propulsion handoff §6.6).
- AVL-based 3-D verdict criteria (lifting-line/XFOIL suffice for v1).

## Self-review

- **Coverage:** usable → verdict gates against stated requirements (1.x, 2.x);
  safe → structural MS criteria + honest-fail delivery (1.5, 4.1); novel/creative →
  bounded refinement iterates designs (3.x), exploration explicitly staged next.
  Handoff-vs-pivot question answered with rationale. ✔
- **Placeholder scan:** every task has real code or an exact command; 1.6, 3.6,
  and 3.7 name fallback discovery steps where an existing symbol name must be
  confirmed at implementation time (`_REA_RESPONSE_SCHEMA` name,
  `_apply_diff_and_export` extraction, GLB `ExportTypes` enum). No TBDs. ✔
- **Type/name consistency:** `DesignTargets`/`CriterionResult`/`DesignVerdict`
  defined in 1.2/1.4 before use in 1.5/2.2/3.2; `evaluate_design`,
  `refinement_prompt_for`, `resize_wing_box_for_margin` defined before
  `closure.py` imports them (3.5); `_run_verdict_stage` defined (2.2) before
  the SSE wiring uses it (2.4). ✔
- **Convention match:** never-raise analysis stages, `_safe_error` redaction,
  stdlib-runnable tests, lazy imports with `# noqa: PLC0415`, merge-gate
  compliance (schema tests, regression slice, security review). ✔

---

# Review report (xvant-plan-review, 2026-06-11)

**Posture: SELECTIVE EXPANSION** — baseline scope held; every expansion listed
as an explicit opt-in. All must-fix findings below are **already applied** to
the plan body above (tasks renumbered accordingly). Findings are kept here so
the reasoning survives.

## Verdict on the strategic decision

**The handoff-vs-pivot logic is sound and the draft plan's direction is
correct.** Worktree A is plumbing, not capability — its own handoff proves it.
A full pivot would discard a ~90%-complete analysis stack. Closure is the
highest-leverage step because it converts existing advisory physics into
enforcement, and it is the prerequisite fitness function for the exploration
work that actually delivers "novel/creative". One inversion check: if the
bottleneck were geometry-generation reliability (the 5/64 eval failures),
closure wouldn't help — but those failures are B/C-domain, not D, so the
aerospace path is clear. Decision stands.

## Must-fix findings (applied to plan body)

- **R1 — CRITICAL: the struct refine loop was a no-op treadmill.**
  `_run_struct_stage(requirements)` computes margins purely from
  `requirements.wing_box_spec` (`main.py:295-388`). The draft loop prompted
  `run_refiner` to "increase spar cap area" — but the Refiner edits
  manifest/scripts, never `requirements`, so re-analysis would return
  byte-identical margins forever and burn 2 LLM calls doing it.
  *Fix applied:* struct criteria now solved analytically
  (`resize_wing_box_for_margin`, task 3.3) — one-step exact sizing, zero LLM;
  the loop updates `requirements.wing_box_spec` and re-runs the pure-number
  stage. LLM refinement reserved for aero criteria where geometry genuinely
  drives the result.
- **R2 — CRITICAL: closure embedded in the SSE generator is invisible to the
  eval harness.** `eval_harness/runner.py` calls `run_critic_loop` directly —
  the exact trap the Worktree-A handoff documented — so the draft's Phase-4
  "regression net for closure" tested nothing of the sort, and the loop tests
  needed `import main` (FastAPI/limiter side effects).
  *Fix applied:* loop extracted to `backend/closure.py` with injected
  callbacks; tests target it directly; Phase 4 rewritten with an honesty note
  scoping gate (b) to geometry validity; verdict semantics verified live (4.3).
- **R3 — GLB staleness.** GLB bytes are read and the file deleted at
  `main.py:723-725`, *before* analysis stages run. The draft asserted "the
  refined GLB replaces the delivered one" with no code path producing it.
  *Fix applied:* task 3.7 re-exports and re-encodes when closure changed
  geometry; export failure degrades to original GLB + honest verdict.
- **R4 — wrong lever for `v_ne_min_ms`.** The draft's lever table claimed spar
  cap area raises V_NE; V_NE comes from the flight envelope (aircraft spec +
  category), not wing structure. A refinement loop chasing it would never
  converge. *Fix applied:* `v_ne_min_ms` is check-only, excluded from
  `_AERO_LEVERS` and from resizing.
- **R5 — stated targets + opt-out analysis = guaranteed `incomplete`.** Targets
  are extracted by the REA, but XFOIL/struct stages were opt-in flags; a user
  stating "MS ≥ 0.5" without `run_struct=1` would always get `incomplete`.
  *Fix applied:* task 2.3 auto-enables the stages a target implies.
- **R6 — NaN/None crash in the L/D extractor.** `_sanitize_floats` converts
  XFOIL NaN points to `null` → `None` in the dict the verdict receives;
  `max(p["L_D"])` would TypeError on the first unconverged polar (caught,
  but degrading every verdict to "verdict failed"). *Fix applied:* filtered
  generator with `default=None` (task 1.5).
- **R7 — single-part wings had no concrete refine path.** The common Domain-D
  case (one wing part) has no manifest to diff; the draft hand-waved a
  "short-circuit" while its code referenced `manifest`/`part_scripts`
  unconditionally (NameError in the single path). *Fix applied:* task 3.6
  branches on the existing `single` flag with real code for both paths.
- **R8 — zero observability.** No way to know whether closure ever rescues a
  design in production. *Fix applied:* task 3.8, JSONL telemetry record per
  closure run (existing `telemetry.py` pattern, local-only).

## Watch items (no plan change needed)

- **R9 — latency vs the 180 s frontend wall.** Worst case adds ~2 × (Refiner
  LLM + sandbox export + XFOIL) to a request — the very timeout class
  Worktree A exists to fix. SSE events between stages keep the connection
  alive, but a single XFOIL+refine round can be slow. 4.3 step 4 measures it;
  if it bites, route closure-enabled requests through the `/jobs` polling path
  (which makes A-06 the natural *next-next* task, pulled by real need rather
  than backlog order).
- **R10 — REA over-extraction risk.** If the REA invents thresholds the user
  never stated, users get spurious FAIL verdicts and lose trust. Schema
  description says "NEVER invent"; the negative-control prompt (4.1/4.3-3)
  is the tripwire. If it trips, tighten the REA system-prompt sentence (1.7).
- **R11 — verdict trusts the spec, not the geometry, for structure.** Margins
  are computed from `wing_box_spec`, so closure proves spec-consistency, not
  that the *generated solid* matches the spec. Geometry-derived struct inputs
  (measure spar dims from the STEP) is the rigorous v2 — belongs in the
  exploration plan, noted there.
- **R12 — cost.** Up to 2 extra Pro-tier Refiner calls + 2 XFOIL runs per
  closure-enabled request. Bounded and Domain-D-only; telemetry (3.8) gives
  the data to revisit `max_iters`.

## Opt-in expansions (surfaced, NOT added — say the word)

- **E1: runner verdict scoring.** Teach `eval_harness/runner.py` an optional
  post-run closure evaluation for `D_closure_*` prompts (import `closure` +
  real analysis stages). Makes gate (b) cover closure semantics. ~½ day.
- **E2: pull exploration forward.** k-candidate planform sweep scored by
  `prandtl_lifting_line` + `evaluate_design` is ~2 days once Phase 1 lands and
  is the direct "novel/creative" payoff. Could start as soon as Phase 1
  merges, in parallel with Phases 2-3.
- **E3: material as a closure lever.** `resize_wing_box_for_margin` could also
  propose AL7075-T6/Ti-6Al-4V swaps from the `_MATERIALS` table when resizing
  alone can't meet MS. Small, but changes mass — needs a mass-budget criterion
  first to avoid silently heavy wings.

## TODOS (deferred, written down per review discipline)

- Worktree A-06..A-12 — unchanged backlog; A-06 priority rises if R9 bites.
- Phase-0 parked B/C eval failures — triage after baseline.
- Propulsion handoff §6.2 (orphaned `propulsion/` package) and §6.6 (Phase-2
  synthetic data re-scope) — untouched by this plan.
- R11 geometry-derived structural inputs — exploration plan.
