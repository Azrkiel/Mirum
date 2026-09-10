# Aero eval readiness — findings and fixes

## Context

Verification pass over the aero path before running merge-gate (b). Two
things came out of it: a **regression I introduced** in the P1 critic-loop
work that hits Domain D hardest, and a pre-existing gap where the aero evals
do not actually evaluate anything aerodynamic.

### What is already fine

- **Aero test suite**: 64 passed, 1 failed, 29 skipped. The failure
  (`test_rea_wing_extraction`) is a live-API test — it fails only because
  this sandbox has no valid Gemini key. All 29 skips are "cadquery not
  importable"; they run inside the Docker image, which has CadQuery and
  apt-installed XFOIL. Nothing here indicates a code defect.
- **Domain D eval slice runs**: `--regression-for D` matches 15 prompts
  (22 Domain D prompts total). CI's three jobs pass on the PR head.
- **The aero stage logic itself is correct** after the pipeline rewrite —
  it reuses the critic loop's verified STEP, falls back to an on-the-fly
  export, and cleans up that fallback file.

---

## Fix 1 — STEP file leak on single-part error paths (regression, must fix)

`backend/main.py`. In P1 I changed the single-part branch from
`run_critic_loop(part, save_step=False)` to `save_step=True` so the aero and
certificate stages could reuse one verified STEP instead of re-exporting the
geometry twice. The happy path deletes that file at the end of `_pipeline`
(main.py:983), but **two early returns between assignment and cleanup do
not**:

| Line | Path | Leaks |
|---|---|---|
| 665 | single-part export subprocess failed | yes |
| 840 | script succeeded but no .glb was produced | yes |

Anything that raises out of the async generator in that window (e.g.
`parameterize_script`) leaks the same way, as does a client disconnecting
mid-SSE.

Why this matters for aero specifically: the Domain D planner rule
(`agents.py:549-569`) **forces wings, stabilizers, and fins into a single
part**, so aero requests are exactly the ones taking this branch. Leaked
files land in `/tmp/mirum/parts` on a tmpfs that `docker-compose.yml` caps
at 256 MB, and `cleanup_tmp_parts()` only runs at startup — so a run of
failing wing prompts accumulates multi-MB STEP files until the container is
restarted.

**Fix:** wrap the body of `_pipeline` after the critic-loop call in
`try/finally`, deleting `_single_step_path` in the `finally`. A `finally` in
an async generator also runs on `GeneratorExit`, so it covers client
disconnects too — which the current happy-path-only cleanup does not. Reuse
the existing `agents._remove_quiet` helper rather than another bare
try/except.

**Test:** add to `backend/tests/test_critic_loop_verification.py` — drive
`_pipeline` with a stubbed critic loop returning a real temp file, force the
export-error path, and assert the file no longer exists.

---

## Fix 2 — Make the aero evals actually check aero (recommended)

Right now a Domain D prompt passes if its script compiles and clears the
geometry certificate. **Nothing verifies it built the requested airfoil.**

- `expected_naca_code` (`'0012'` on `D_aero_sweep_naca0012`, `'0018'` on
  `D_fuselage_cross_section`) and `min_converged_alpha` (`14`) are read by
  **zero lines of code** anywhere in the repo — confirmed by grep. They were
  specified in `output/mirum-implementation-todo.md:1460` and never wired.
- `expected_features` is not verified either: `_md_report._fmt_features`
  computes `matched = len(expected) - len(missing_features)` where
  `missing_features` comes from the Gemini judge's free-text list. The two
  lists are never reconciled, so the "matched/N" column is arithmetic over
  unrelated sets.

So a script that produces a clean NACA 0024 solid scores identically to one
that produces the requested 0012.

**Changes:**

1. **`expected_naca_code` — deterministic, no LLM, no XFOIL.** In
   `eval_harness/runner.py::_run_one`, when the prompt carries the field,
   check the generated script actually references that profile (the
   `make_naca_*` / `naca_code=` call sites in `cad_utils.py:172,289` and
   `cad_aero.py:126`). Mismatch → fail with
   `NACA_MISMATCH: expected 0012, code uses 0024`. Cheap and catches the
   most common Domain D error.
2. **`min_converged_alpha` — opt-in `--with-aero` flag.** The harness runs
   only the Machinist critic loop today; the aero stage never executes. Add
   a flag that, for prompts carrying the field, exports the STEP and calls
   the existing `analysis.airfoil.analyze_wing_step` (reused via
   `main._run_aero_stage`'s gating logic), asserting
   `converged_count >= min_converged_alpha`. Off by default — it needs XFOIL
   on PATH and is much slower, so it should not silently change the default
   corpus run.
3. **Fix the features-matched miscount** in `eval_harness/_md_report.py`:
   compare `expected_features` against the script text case-insensitively
   and report a real matched count, rather than subtracting an unrelated
   list's length. `backend/tests/test_md_report.py` pins the current
   behaviour, so update that test alongside.

---

## Verification

```bash
# Gate (a) — must stay green (pure stdlib, <1s)
python backend/tests/test_prompts_schema.py
python backend/tests/test_runner_filters.py
python backend/tests/test_md_report.py

# New + existing regression tests
python -m pytest backend/tests/ -q      # expect no NEW failures vs the
                                        # 29 live-API/CadQuery baseline

# Leak fix, observed directly (needs Docker — CadQuery required)
docker compose up -d --build
ls /tmp/mirum/parts | wc -l             # before
#   ...drive a single-part prompt that fails export...
ls /tmp/mirum/parts | wc -l             # must be unchanged

# Gate (b) — the aero slice, on your machine (needs a Gemini key)
python -m eval_harness.runner --regression-for D \
  --report-md output/qa-reports/$(date +%F)-D.md
# With Fix 2 applied, add --with-aero to exercise XFOIL convergence.
```

Expect the Domain D pass rate to move relative to the old baseline: the loop
now verifies geometry rather than just compilation, and with Fix 2 a wrong
airfoil profile fails where it previously passed. That is the honest number.

## Files

- `backend/main.py` — `_pipeline` try/finally (Fix 1)
- `backend/eval_harness/runner.py` — NACA check, `--with-aero` (Fix 2)
- `backend/eval_harness/_md_report.py` — features-matched (Fix 2)
- `backend/tests/test_critic_loop_verification.py`, `test_md_report.py` — tests
