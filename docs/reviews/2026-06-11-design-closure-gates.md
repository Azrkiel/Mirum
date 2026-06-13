# Merge-gate evidence — mirum/design-closure (2026-06-11)

Per `.claude/rules/worktree-merge-gate.md`. Diff scope: `main` (c213a04) → HEAD.
Touches `main.py` endpoints + `schemas.py` → gates (a), (b), (c) all apply.

## Gate (a) — schema tests: PASS

```
python backend/tests/test_prompts_schema.py   → 9/9 passed
python backend/tests/test_runner_filters.py   → 10/10 passed
python backend/tests/test_md_report.py        → 11/11 passed
```

## Gate (b) — regression slice: PASS (live, 2026-06-12)

`python -m eval_harness.runner --regression-for D` run live on the host
(Gemini via host CA bundle: gRPC needs
`GRPC_DEFAULT_SSL_ROOTS_FILE_PATH`/`SSL_CERT_FILE` pointed at a PEM export
of the Windows trust store — same TLS-interception issue as the container,
memory `reference_mirum_docker_ssl`).

**Result: 20/20 passed (100%)** — report `output/qa-reports/2026-06-12-D.md`.
Domain D 16/16 (incl. all 5 new `D_closure_*` ids), Domain C carriers 4/4,
LVM median 4.7, no newly-failing ids vs. the 2026-06-08 D baselines.
Honesty note (plan Phase 4): the runner calls `run_critic_loop` directly,
so this covers geometry validity of the prompts, not verdict semantics.

## Plan task 4.3 — live end-to-end: PASS (2026-06-12, container)

Backend: `mirum-qa-cert:latest` image, worktree code bind-mounted, host
CA-bundle mounted, host port 8010 (8000 held by a stale wslrelay; the
hardened `t2ciii-backend:latest` refuses to start without netfilter caps —
use the qa-cert image for local e2e). Scripts: `output/e2e_closure_check.py`,
`output/e2e_struct_closure.py`.

| Check | Result |
|-------|--------|
| Targets-bearing prompt → `design_verdict` present, criteria exactly as stated (none invented) | PASS |
| Verdict SSE events `running → done/warning` | PASS |
| Unmeetable target → honest non-pass verdict, GLB still delivered | PASS (`incomplete` — see scope note) |
| Negative control → `design_verdict` null, zero verdict SSE events | PASS |
| GLB delivered on every request | PASS (3/3 + struct case) |
| Struct criterion evaluated live: prompt states MS ≥ 0.5 + full wing-box sizing → bending MS 0.53 computed, verdict `pass`, criteria actual/target correct | PASS |

Scope note: XFOIL is not present in the qa-cert image and prompts without
explicit spar-cap/web sizing skip the struct stage by design — so the live
runs exercised extraction, verdict wiring, honest-incomplete, and an
evaluated struct criterion, while the failing-criterion → resize/LLM-refine
loop is pinned by the 26 offline closure/verdict tests (stubbed callbacks).
A full-fidelity aero closure run needs the cadenv image with XFOIL (plan
watch item R9 measures its latency).

Deferred: plan task 0.3 full-corpus baseline (expensive; the D slice is the
gate for this diff — run the full corpus on main after merge per the
merge-gate operating notes).

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
