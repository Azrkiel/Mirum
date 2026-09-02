# Mirum Harness Improvement Plan — Code Review + Verification-Driven Critic

## Context

Full code review of the Mirum pipeline (agents, main, compiler, schemas, failure_analysis, retrieval, verification, eval harness, cad_utils). The owner's goal: **design quality is capped by the model; improve the harness so the same fixed model produces better geometry.**

The review's core finding: Mirum already computes almost every signal it needs — failure classification with hand-written hints, few-shot retrieval, a 6-check geometry certificate, G2/curvature gates, a solver-status marker, even a working render→Gemini-Vision path — **but nearly every feedback loop is severed**. Signal is computed and then discarded, and several bugs silently destroy quality today:

### Verified silent quality destroyers
1. **`str(MateType)` triple bug** (`agents.py:1237, 1369, 1403`): Python 3.11 `str(Enum)` → `"MateType.REVOLUTE"`, never matches `MATE_CONSTRAINTS` keys → **every mate type in every assembly silently degrades to a Plane constraint**. Also makes kinematic metadata always empty and the SLIDER unit check dead. (Verified in-repo: `str()` output confirmed; `main.py:972` already uses `.value` correctly.)
2. **Solver failure shipped as success** (`agents.py:1252-1256, 1318-1327`): `assembly.solve()` failure prints `SOLVER_FAILURE`, saves the GLB anyway with parts stacked at origin; pipeline reports `assembler: done`. Marker only feeds telemetry. Also: no part is ever `Fixed` — the solver has no datum.
3. **Retry prompt omits the previous code** (`agents.py:739-744`, `failure_analysis.py:204-239`): the Machinist is told "fix the code" it cannot see and regenerates from scratch. `code` is available at the call site (`agents.py:967`) and used only for failure classification. Error context is also overwritten each attempt — no history, so the model ping-pongs between failure modes.
4. **Geometry certificate thrown away** (`agents.py:956-964`, `verification/geometry.py:283`): runs after the retry loop; failures never trigger retry, never logged; skipped entirely when `save_step=False` — i.e. **all single-part requests and all eval runs**.
5. **No dimension-vs-spec check anywhere**: REA extracts `key_dimensions`; cert measures the bounding box; the two are never compared. A "100×60×5mm plate" delivered as 100×60×50 passes every check.
6. **Retrieval index poisoning** (`retrieval.py:138, 196, 208`; `agents.py:850/874/920`): historical entries keyed by `part_id` slug (telemetry never logs the description) vs live entries keyed by prose → TF-IDF degrades after restart; stale IDF; score filter applied only to the top candidate; Domain C scripts that fail G2/curvature are double-logged and **re-served as "successful" few-shot examples**.
7. **Wasted/discarded LLM calls**: per-part LVM judge (`agents.py:947`, unreferenced task, GC risk) feeds nothing; `/plan` + `/generate` double-plan discards the user-approved manifest (plan drift + 2 wasted Pro calls); `run_machinist_batch` dedup never called while the Planner mandates `leg_1..leg_4` as identical separate parts (4× cost).
8. **Eval measures less than prod**: pass = "compiled within 3 retries"; cert and Domain C gates disabled in eval; `expected_features` never verified; Domain G slice unrunnable (`runner.py:266` choices hardcode A–F).
9. Untapped levers: all Gemini calls at default sampling (no temperature anywhere; `model_registry` with per-agent temps + `/admin/model-config` exists, wired to nothing); no Pro escalation on retry; `get_fallback_script` never called; no hints for `syntax`/`security`/`timeout` (the most common LLM failure classes); render/vision inspection never fires for the delivered artifact.

### User decisions (locked in)
- **Cost stance:** spend more Gemini calls where they demonstrably buy quality; fund with the savings (LVM removal, dedup, plan reuse).
- **Failure policy:** deliver-with-warnings (`status: degraded` + attached check report), never silent `done`, never hard-fail geometry that merely failed a soft check.
- **Scope now:** implement **P0 + P1**; P2/P3 are the documented roadmap for later sessions.

---

## Phase P0 — Silent quality destroyers (implement now; all deterministic, Δcalls ≤ 0)

| # | Fix | Files |
|---|-----|-------|
| P0.1 | `str(rule.mate_type)` → `rule.mate_type.value` at all 3 sites; add `Fixed` constraint on the first/base part; loud (logged) degradation for GEAR/SCREW/RACK_PINION/CAM which have no `MATE_CONSTRAINTS` entry | `agents.py:1237,1369,1403` |
| P0.2 | Guard `rule.translation is None` in translation-mode assembler (mirror the guard at `agents.py:1199-1204`) | `agents.py:1080` |
| P0.3 | Return `solver_ok` from `run_assembly_critic_loop`; surface `degraded` in SSE + `complete` payload instead of silent `done` | `agents.py:1263-1331`, `main.py:620-679` |
| P0.4 | try/except around `classify_part` → default domain "A" on transient Gemini errors instead of killing the part | `agents.py:826-828` |
| P0.5 | Retrieval fixes: `log_attempt` gains `description` field; `build_index` prefers it; per-candidate score filter; recompute IDF every 25 inserts; move Domain C success-log/index-add to after the geometric gates pass | `retrieval.py`, `telemetry.py:87-95`, `agents.py:850,941` |
| P0.6 | `generate_failure_report` skips `token_usage` records (currently counted as failed attempts of op "unknown", inflating `/stats`) | `failure_analysis.py:274` |
| P0.7 | LVM judge off in prod (`config.LVM_SCORE_IN_PROD=False`, env-overridable); keep `evaluation.py` for eval/P3 reuse; strong task reference where enabled | `agents.py:947`, `config.py` |
| P0.8 | Honor `config.CRITIC_MAX_RETRIES` (`agents.py:804`) and `config.CADQUERY_TIMEOUT_S` (`compiler.py:264,288` hardcode 30). Compiler diff = exactly that constant; sandbox invariants untouched | `agents.py`, `compiler.py`, `config.py` |
| P0.9 | Cancel in-flight sibling part tasks when the first part fails in multi-part path | `main.py:584-618` |
| P0.10 | Fix stale 2-tuple unpacks in `subagents/{critic,machinist}.py`; add `"G"` to eval runner `--regression-for` choices | `subagents/`, `eval_harness/runner.py:266` |

## Phase P1 — Verification-driven critic (implement now; the capability core)

**P1.1 `CheckReport`** — new small module `backend/verification/report.py` (the only new module): `CheckResult(name, passed, skipped, failures[], data{})` + `CheckReport(part_id, attempt, checks[])` with `.passed`, `.hard_failed` (compile failed), `.failure_class()` (feeds `retrieve_for_failure`), `.to_prompt_context()` (feeds `build_retry_context`), `.to_telemetry()`. New failure classes: `geometry_cert`, `dimension_mismatch`, `solver` join the existing 17 ops.

**P1.2 Restructure `run_critic_loop`** (`agents.py:802-981`) — per attempt:
compile → (on success) **always** export one temp STEP → `certify_geometry` → dimension gate → Domain C gates (**including on the final attempt**, where they mark degraded instead of retrying) → all folded into a `CheckReport`. Report failure consumes a retry from the shared `max_retries` budget (raise `CRITIC_MAX_RETRIES` to 4 via config if eval shows crowding). `_save_part_step` failure becomes a `CheckResult("export")` failure inside the loop (fixes the no-retry dead-part bug at `agents.py:763/949`). Returns `(code, step_path, report)` — same 3-tuple arity as today. Domain C export count per attempt drops 3→1.

**P1.3 Dimension-vs-spec gate** — plumb `DesignRequirements` from `main.py:462` into `run_critic_loop`. Tolerance: `config.DIM_TOLERANCE_REL=0.05` (+0.1mm absolute floor), env `MIRUM_DIM_TOL`. Conservative matching: single-part, each `key_dimensions` entry must match some bbox extent (diameter/thickness heuristics) within tolerance; multi-part, per-part check only when the dimension name contains the part_id token, plus a full-set check on the assembled result (one extra `certify_geometry`, no LLM). Failure message carries measured vs expected (`total_length_mm: expected 150.0, closest extent 92.3, 38% off`) — exactly what the retry prompt needs.

**P1.4 Previous code + history in the retry prompt** — extend `build_retry_context(traceback, code, active_rules, report=None, history=None)` to include: the previous script verbatim (truncated ~4k chars, tail-preserved), the existing class/hint block, `report.to_prompt_context()`, and a one-line-per-attempt history digest (fixes context overwrite). Highest-leverage item in the plan; shippable as a first sub-PR since `code` already reaches `build_retry_context`.

**P1.5 Hint coverage** — add `OP_TO_RULE_HINT` entries for `syntax`, `security` (must enumerate the actual sandbox allowlist), `timeout`, `import`, `constraint`, `extrude`, `revolve`, `spline`, plus the new P1.1 classes. Source text from the matching `strategies.py` rules.

**P1.6 Solver-failure policy** — on solver failure: one deterministic retry via translation-mode assembler when translations exist; else deliver constraint output as `degraded` with an explicit warning. Move `check_assembly_interpenetration` post-solve (or drop the pre-solve call — it's noise by contract since all parts sit at origin) and run OCCT work via `asyncio.to_thread`.

**P1.7 Telemetry** — `log_attempt` accepts `check_report` + `description`; `generate_failure_report`//`/stats` gain geometric failure-rate columns.

**Net Δcalls for P0+P1:** negative (−N LVM per request, cancelled siblings) with more verification per call; cert-triggered retries bounded by existing `max_retries`.

## Roadmap (P2/P3 — later sessions, documented for continuity)

- **P2.1** Wire `model_registry` into all Gemini calls (per-agent temperature/max_tokens; makes `/admin/model-config` real; today everything runs at default ~1.0 temp).
- **P2.2** Pro escalation on the final retry only (registry-driven, disable-able).
- **P2.3** Best-of-N first attempt for low-first-pass domains (C, F), selected deterministically by CheckReport score — zero judge calls.
- **P2.4** Route multi-part through the existing `run_machinist_batch` dedup (fix its return shape); duplicate parts share one critic loop, copy STEP files.
- **P2.5** Plan reuse: server-side TTL cache keyed by manifest hash; `/plan` returns a token `/generate` accepts. Token (not client-supplied manifest) because anchors are embedded into sandbox-executed scripts — security review required.
- **P2.6** `get_fallback_script` on single-part final failure, `fallback: true` flagged, off in eval.
- **P3.1** Render→Gemini-Vision check of the delivered artifact (reuse `render_zebra` + `evaluation.py` judge + the thread-pool pattern from `geometry_analysis.py:391-475`); +1 Flash-vision per request, cheaper than the removed LVM calls.
- **P3.2** Unbreak Domain C G2 VLM fallback (`skipped_async_ctx` is structurally always taken under FastAPI).
- **P3.3** Eval honesty: pass = compiled AND cert-clean AND dims-in-tolerance AND not degraded; verify `expected_features` via deterministic probes + render judge; wire dead `expected_naca_code`/`min_converged_alpha` fields; re-baseline.
- **P3.4** Telemetry/retrieval persistence: default `MIRUM_TELEMETRY_DIR` to a mounted volume (corpus currently destroyed on restart).
- **P3.5** Domain C deterministic `cad_utils` helpers (20/83 eval prompts, zero helpers today) using the `cad_aero._validate_solid` self-checking pattern.

## Verification

1. **Unit tests (no Gemini):** `test_mate_constraints_lookup` (every `MateType.value` resolves or is explicitly degraded), `test_assembler_translation_none`, `test_build_index_uses_description`, `test_retrieval_score_filter`, `test_failure_report_ignores_token_usage`, `test_config_timeout_wired`, CheckReport aggregation/serialization, dimension-gate math + message text, `build_retry_context` includes previous code/history/cert failures, export-failure-consumes-retry.
2. **Integration (no Gemini):** monkeypatch `run_machinist` with scripted sequences (compile-fail → cert-fail → good); assert loop behavior, telemetry records, index admission.
3. **Schema gate (a):** `python backend/tests/test_prompts_schema.py`, `test_runner_filters.py`, `test_md_report.py` — must pass.
4. **Eval gate (b):** full A–G run before/after P1 (`python -m eval_harness.runner --report-md output/qa-reports/<date>-p1.md`); compare pass rate + attempts-per-success; expect the honest baseline to shift once verification is visible — document the re-baseline.
5. **Security gate (c):** P0.8 touches `compiler.py`, P0/P1 touch `main.py` event payloads and `telemetry` — dispatch `mirum-security-reviewer` on the diff before merge; sandbox invariants (`_ALLOWED_MODULES`, `_BANNED_BUILTINS`, RLIMIT_AS, 30s→config timeout, no API key in subprocess env) must be regression-free.
6. End-to-end: `docker compose up --build`, run a multi-part prompt with mates, confirm mates are no longer all-Plane, solver failures surface as `degraded`, and retry prompts contain the previous script.

## Branch
All work on `claude/most-recent-update-gp0c2v`; commit per P0/P1 item group; push + PR when P0+P1 complete and gates pass.
