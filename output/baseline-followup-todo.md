# Baseline-Report Follow-Up — Remaining To-Do

Carried over from the baseline-report to-do (items 1–6 already landed). Each item below is self-contained: what to change, where, and why.

**Source baseline:** `backend/eval_harness/runs/baseline.jsonl` (24 prompts, 21 pass / 3 fail).
**Already shipped:**
- Item 1: `backend/eval_harness/` committed as the measurement floor (commit `faaa64c`).
- Item 2: Domain C grew 4 → 12 prompts; Domain D grew 3 → 7. See `backend/eval_harness/prompts.json`.
- Item 3–5: `failure_analysis.build_retry_context()` enriches `error_context` with classified failure op, likely-violated rule + actionable hint, and active rule list. Wired into `agents.run_critic_loop`. New `strategies.get_active_rules()` mirrors the rule selection.
- Item 6: `retrieval.extract_techniques()` AST-scans CadQuery code; both `add_to_index()` and `build_index()` tag entries with technique tags (`loft`, `polar_array`, `thread`, `shell`, `extrude`, `revolve`, `fillet`, `chamfer`, `boolean`, `text`, etc.). New `retrieval.retrieve_for_failure()` queries by `(failure_class, technique)`.
- Item 7: `agents.run_machinist` now accepts `retry_failure_class` / `retry_techniques`; on retry it pulls few-shots via `retrieve_for_failure` instead of description similarity. `run_critic_loop` extracts techniques from the failed code and threads `failure_op` + techniques into the next iteration (compile-fail, G2-continuity, and Gaussian-curvature paths all wired).
- Item 9: `prompts.json` carries an `expected_features` array on every prompt. The runner awaits `compute_lvm_score` after each pass, surfaces overall + per-domain median LVM in the summary, and exposes `--strict-lvm` to demote passes whose LVM score < 3.0.

---

## Item 8 — Measurement loop: re-run eval after each change

**Command (from inside the backend conda env, with both Gemini keys exported):**
```
python -m eval_harness.runner --baseline runs/baseline.jsonl
```

**Acceptance rule:** keep a change only if **both** are true:
- Total pass rate does not regress (`Baseline → Current` shows `+N` or `0`).
- At least one of the targeted failures (`B_threaded_rod`, `C_water_bottle_grip`, `C_organic_drawer_pull`) flips to `pass`.

If a step regresses, revert that step's commit, not the whole stack.

**Note on environment:** the runner needs the OCCT-loaded conda env (or the backend Docker container). It cannot run from the bare CLI shell — execute it on the user side. Output JSONL lands at `$MIRUM_EVAL_RUNS_DIR/<timestamp>.jsonl` (defaults to `/tmp/mirum/eval_runs/`). Keep the run that becomes the new baseline; commit it to `backend/eval_harness/runs/` if it supersedes `baseline.jsonl`.

**Cadence:** run after item 7 (failure-aware retrieval) lands, and again after item 9 (LVM scoring) if it changes acceptance criteria.

### 2026-04-25/26 — Remeasurement attempt blocked

**Round 1 — model ID dead.** The constant `gemini-3.1-flash-preview` (hardcoded in `agents.py`, `continuity_check.py`, `evaluation.py`, `main.py`) returns `404 NotFound` from v1beta. ListModels with our keys shows only `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`, `gemini-flash-latest` etc. Swapped Machinist model to `gemini-3-flash-preview` everywhere, plus replaced the `→` in `runner._diff()` with `->` (cp1252 console choke).

**Round 2 — eval ran, results not actionable.** Two passes:

- `--concurrency 3`: 13/36 pass (-8 vs baseline 21/24). Most failures took 0.17–2s.
- `--concurrency 1`: 5/36 pass (-16 vs baseline). First five Domain A prompts passed in 27–66s, then *every subsequent prompt* fast-failed in 0.16–2s.

**Root cause: free-tier quota exhausted on `gemini-3-flash`.** Probed both keys after the run:
- HEAVY key: trivial call returns OK.
- LITE key: `ResourceExhausted: Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, model: gemini-3-flash`.

So the LITE key had been making `gemini-3-flash` calls and burned its 20/day allowance. That points to a **pre-existing key-leak bug** in the orchestrator: `classifier.classify_part()` calls `genai.configure(api_key=LITE)` before its lite-tier request, then `agents.run_machinist()` issues its heavy-model request without re-binding to HEAVY — so the global SDK is still pinned to LITE, and Machinist requests get billed against the LITE key. After ~20 successful Machinist calls (a few prompts × retries), LITE quota dies and every subsequent Machinist call instant-fails. Same pattern would affect production, where it eats LITE quota silently.

The runner truncates exception messages to the last non-empty line — `ResourceExhausted` traceback ends in `[]` from a list literal in the Google SDK error-string, hence the `]` in every fail row. Worth widening the runner's error capture to keep `type(exc).__name__` plus the first line.

**Two follow-ups before the next remeasurement:**

1. **Fix the key-leak in `agents.py`.** Add `genai.configure(api_key=os.environ["GEMINI_API_KEY_HEAVY"])` at the top of `run_machinist`, `run_planner`, `run_clarifier`, `run_rea`, mirroring what `evaluation.compute_lvm_score` and `classifier.classify_part` already do. Each call site re-binds to its tier instead of relying on global state.
2. **Free tier won't carry the eval anyway.** 36 prompts × (1 classify + 1–3 machinist + 1 LVM) puts heavy-model traffic well past 20/day per key. Either upgrade the project to paid usage, or split the eval across days with `--filter` and aggregate.

Failed run outputs (no useful per-prompt data — every record is `error: "]"`):
- `\tmp\mirum\eval_runs\1777175728.jsonl` (model-404 attempt)
- `\tmp\mirum\eval_runs\1777180085.jsonl` (concurrency 3, partial pass)
- `\tmp\mirum\eval_runs\1777180530.jsonl` (concurrency 1, partial pass)

---

## Quick links

- Failure attribution map: `backend/failure_analysis.py` → `OP_TO_RULE_HINT`, `_SPECIFIC_OVERRIDES`-equivalent in `get_rule_hint`.
- Active rule resolver: `backend/strategies.py` → `get_active_rules`, `_resolve_rule_names`.
- Technique AST scan: `backend/retrieval.py` → `extract_techniques`, `_TECHNIQUE_METHODS`.
- Failure-aware retrieval: `backend/retrieval.py` → `retrieve_for_failure`, `_FAILURE_TO_TECHNIQUE`.
- Runner: `backend/eval_harness/runner.py` (entry point: `python -m eval_harness.runner`).
- Baseline floor: `backend/eval_harness/runs/baseline.jsonl`.
