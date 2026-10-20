# Phase 1 API/UI review (completes the pending review)

The handoff recorded that the independent API/UI review of the structural
acceptance slice ended with `Error: Reached max turns (12)` and no verdict.
This document is that review.

## Scope

Diff `integration/text2cad-hardening..engineering/brep-acceptance`:
`backend/verification/{acceptance,acceptance_worker,solid_set}.py`, the
`/generate`, `/run`, `/refine` and `/export/parasolid` changes in
`backend/main.py`, and the evidence handling in `frontend/app.py`. A separate
read-only security pass covered the same diff against
`.claude/skills/mirum-security-review/SKILL.md`.

## Findings fixed

Each fix has a regression in `backend/tests/test_phase1_review_fixes.py` that
failed before the fix.

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Medium | Multi-part `run_aero` passed the whole positioned assembly STEP (fuselage, spars, ...) to the mid-span slicer, which assumes the wing's own frame. Before phase 1 the wing part was selected. | Prefer the certified `wing`/`oml`/`skin` part STEP; fall back to the accepted final STEP. |
| 2 | Low | Single-part Domain F results lost the requested throat/exit/expansion fields; only the multi-part branch attached `requested_spec_metadata`. | Attach it for both branches. |
| 3 | Medium | `MAX_SOLIDS = 32` while `AssemblyManifest` allows 50 parts, so valid 33-50 part assemblies always failed after all generation work. | `MAX_SOLIDS = 64`; message derives from the constant. 50 separated solids pass natively. |
| 4 | High (pre-existing on `main`) | `/refine` reuse built `_WORK_DIR / f"{pid}_....step"` from a client-supplied `part_id`, so `../` escaped the work dir and `'` broke the generated string literal. | Sanitize with `agents._sanitize_part_id` and emit the path with `repr`. |

## Findings not fixed (recorded)

- **Generated code can still write files outside `_WORK_DIR`** (e.g.
  `cq.exporters.export(obj, '/abs/path.svg')`). The AST policy and audit hook
  block imports and process spawning, not file writes. Pre-existing and already
  documented as "defense in depth, not a hostile-code sandbox". Needs a
  filesystem sandbox (read-only root / mount namespace) for the CAD subprocess.
  The production container's read-only `/app` limits the impact.
- **Raw `str(e)` from unexpected server-side faults** in the `_execute_accepted`
  callers can include `/tmp/mirum/...` paths, which `_safe_error` does not
  redact. Pre-existing pattern; low.
- **The certificate attests the captured bytes, not the script's intent.** A
  script can replace `cq.exporters.export` before capture. Hashes and the
  verdict still describe the delivered bytes and `engineering_certified` stays
  `false`, which is the documented scope.
- **Exact-zero overlap policy.** `no_positive_volume_interpenetration` requires
  a Boolean common volume of exactly 0. Face-touching boxes pass, but curved
  contacts could produce tiny numerical volumes and be rejected (fail-closed).
  Watch for false rejections on real assemblies.
- **The 0.001 mm tolerance ceiling** has not been measured against real
  lofted Domain D wings; it may reject them (fail-closed).

## Verification

Immutable `Dockerfile.verify` image, network disabled, read-only root, dropped
capabilities, 4 GiB / 2 CPU:

```text
588 passed, 1 deselected, 274 warnings in 328.24s
```

584 from the handoff plus the 4 new regressions. The deselected test is the
explicit live-provider test. Model seams are fixtures; no live generation
quality is claimed.

## Verdict

No remaining blocking defects in the reviewed slice. The unfixed items are
pre-existing or documented scope limits. This is still not production or
engineering approval: the live eval-harness regression slice (merge gate b)
needs a real provider key and was not run.
