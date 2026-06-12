# Merge-gate evidence — mirum/design-closure (2026-06-11)

Per `.claude/rules/worktree-merge-gate.md`. Diff scope: `main` (c213a04) → HEAD.
Touches `main.py` endpoints + `schemas.py` → gates (a), (b), (c) all apply.

## Gate (a) — schema tests: PASS

```
python backend/tests/test_prompts_schema.py   → 9/9 passed
python backend/tests/test_runner_filters.py   → 10/10 passed
python backend/tests/test_md_report.py        → 11/11 passed
```

## Gate (b) — regression slice: PENDING (live run required)

`--regression-for D --dry-run` confirms the slice picks up all 5 new
`D_closure_*` ids (20 prompts total). The live run needs
`GEMINI_API_KEY_HEAVY` + the cadenv Docker container with the host
CA-bundle mount (TLS-intercepting proxy on this machine; see memory
`reference_mirum_docker_ssl`). **A dry-run alone does not satisfy gate (b)**
— the live `--regression-for D` run plus baseline diff must complete before
this branch merges. Honesty note recorded in the plan (Phase 4): the runner
calls `run_critic_loop` directly, so gate (b) covers geometry validity of
the new prompts, not verdict semantics (those are pinned by the offline
suites and checked live in plan task 4.3).

## Gate (c) — security review: PASS

Dispatched 2026-06-11 (mirum-security-reviewer role executed by a
fresh-context agent; repo agent not registered in this session root).
Verdict: **CRITICAL: none. HIGH: none → GATE (c): PASS.**

Findings and their resolutions (all addressed in commits b7b8935 + the
review-exchange fix commit):

- **M1** premature `_final_step` commit (verdict/GLB divergence) → fixed:
  candidates staged in `_pending_step`, committed by closure only on strict
  improvement (best-attempt contract), and only after their polar ran.
- **M2** resource amplification (unreachable targets force max burn) →
  mitigated: no-progress/no-improvement cutoff ends the loop after one
  wasted round; `max_iters=2` hard cap; existing semaphore + 30 s sandbox
  and XFOIL timeouts. Schema bounds deliberately NOT tightened: an
  out-of-range stated target would fail DesignRequirements validation and
  null ALL extracted specs, breaking the honest-fail contract
  (`D_closure_unmeetable`). Watch item: rate-limit if telemetry shows abuse.
- **L1** temp-file leakage on failure paths → fixed (single-path STEP,
  closure GLB always removed; `_apply_diff_and_export` reuse STEPs are
  consumed downstream and live under bounded `/tmp/mirum/work`).
- **L2** resize bypasses WingBoxSpec validation → fixed: rebuilt through
  the constructor (validators re-run; refuse on violation), scale clamped
  at 10×, 2% headroom.
- **INFO** injection surface: none added — refinement prompts are built
  exclusively from the fixed `_AERO_LEVERS` table + `:.3g`-formatted
  numbers; sandbox path unchanged; telemetry numbers-only local JSONL.

## Independent pre-merge review (review-exchange): processed

Fresh-context reviewer verdict: NOT MERGE-READY pending (1) best-attempt
delivery, (2) `l_d_min` lever validity, (3) gate evidence. (1) and (2)
fixed in the review-exchange commit (commit-on-improvement mechanism;
section-level L/D lever — a 2-D polar is planform-insensitive at fixed Re).
(3) is this document plus the pending gate-(b) live run. Deferred minors:
OCC-slice-fallback detection, REA all-or-nothing validation on out-of-range
targets (pre-existing failure class), combined aero+struct loop test.
