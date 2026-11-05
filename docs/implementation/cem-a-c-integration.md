# A into C: aerodynamic integration handoff

Software follow-up: [coordinate-family verification](cem-coordinate-verification.md)
adds covered circulation solves, coordinate-output metadata checks and native
initialization/refinement/interpolation audits. The results below remain the
historical integration receipt.

This integrates A's `aae2b7e` and
`26cd52830a95058a6c00d26c4ece9fb377764d5f` into
`cem-task-c-aircraft-families`, based on C's `f921387` continuation. The merge
preserves A's commits and the existing C development modules. No production
family is enabled by this integration.

## What C now receives

- Frozen conventional, T-tail and V-tail numerical references with explicit
  geometry, mass/CG/inertia, polar grids, conditions, limits and unstable
  aft-CG countercases. Each family selects and reconstructs its own reference.
- A covered damped Newton solver for A's circulation and inclined-glide
  equilibrium calculations, with explicit coverage/convergence failures and
  rejected trial-step diagnostics.
- AVL operating-point, axes and drag-conservation checks, and XFOIL output
  checks for requested NACA/Re/Mach/transition settings.
- Initial-condition, panel-refinement and intermediate Reynolds/angle audits,
  including a reproducible failed low-Re interpolation reference.

The original [A handoff](cem-task-a-handoff.md) and its
`cem-a-qualification-results.json` remain A's source-run evidence. They are not
receipts for this merged C source/runtime. Current integration evidence must be
regenerated and recorded separately.

## Boundaries preserved

`mirum-aero-1`, the analytic fixtures and the A/B load contracts are unchanged.
A's three positive cases are idealized rigid numerical benchmarks, not measured
aircraft, broad family envelopes or material/construction qualification. Their
countercases establish pitch-instability rejection at a declared operating point;
they do not establish unstable trim or flight trajectories.

The NACA 0006 low-Re reference remains a failed qualification when its midpoint
query lacks a row. Reconstructing that failure is successful evidence replay,
not a passing section qualification. No missing rows are combined, fabricated
or interpolated over, and real design spiral rejection remains a rejection.

Tailless swept, canard and tandem remain development-only. This merge does not
automatically apply A's production adapters to reflexed or deflected coordinate
geometry. In particular:

1. C's `coupled_aero.py` still uses its own SciPy root solve and adjacent-alpha
   warm start. Adopting A's covered solver requires explicit coordinate-bank
   coverage errors, tests and new evidence; the shared vortex kernel import
   alone does not perform that migration.
2. C's coordinate polar path still needs equivalent reported-output metadata
   checks adapted to actual coordinate foils. A's NACA-name check cannot simply
   be copied to a reflexed/deflected airfoil.
3. C's complete sampled Re 30,000/300,000 grids do not establish intermediate
   interpolation coverage. Since 2026-11-05 the
   [low-Re decision](cem-low-re-method-decision.md) governs this: no section
   below Re 50,000 is admissible, and each actual coordinate foil/control state
   is checked with NeuralFoil plus single-point XFOIL spot checks (160/240-panel,
   header and replay discipline unchanged), not complete banks.
   The NACA 0006 failure does not establish failure of C's different foils;
   those require their own evidence.
4. Installed body/junction effects, full force/moment equilibrium, nonlinear
   interaction/stall sequence, directional control, full inertia/dynamic modes
   and required control-authority envelopes remain open. Physical admission
   additionally requires B's family-applicable construction/load-path evidence.

## Reproduce on C

Use the existing checkout; do not create a worktree implicitly. Activate the
prepared environment and run native qualifications sequentially to preserve
the 20-second per-invocation limit:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
python scripts/cem/qualify_aero.py --case conventional --directory /tmp/new-conventional-reference
python scripts/cem/qualify_aero.py --case t_tail --directory /tmp/new-t-reference
python scripts/cem/qualify_aero.py --case v_tail --directory /tmp/new-v-reference
python scripts/cem/qualify_aero.py --directory /tmp/new-t-reference --replay
python scripts/cem/qualify_aero_section.py --directory /tmp/new-low-re-reference
python scripts/cem/qualify_aero_section.py --directory /tmp/new-low-re-reference --replay
```

Use fresh directories. The low-Re command and faithful failed-reference replay
exit 1 for the retained qualification failure; verify its actual stage/reason
rather than interpreting a nonzero status as an installation failure.

C's coupled reports bind the source of A's shared vortex-kernel file. That file
changes in the merge, so pre-merge C reports must be regenerated even though
the `segment_velocity` formula itself is unchanged. Preserve historical receipts
and distinguish them from evidence regenerated for this integration.

## Current-instance validation

The combined CEM/glider/UI/AVL/XFOIL regression completed **526 passed, no skips**
in 794.06 seconds, with two existing dependency deprecation warnings. Native
positive/countercase qualification and replay, failed low-Re qualification and
forged-evidence rejection executed. Dependency compatibility and staged/working
tree diff checks passed.

Standalone CLI generation and raw-evidence replay also passed for all three
references on C. Each retained its expected aft-CG instability rejection and
`verified_gain=false`. Their predicted distances/static margins match A's
reported values, while source/executable evidence identities are recorded for
this runtime separately.

The standalone low-Re reference and replay both exited 1 as expected: the
qualification remains failed at interpolation coverage, Re
34641.016151377546, missing alpha 2.125 degrees. Its failed result is retained;
it is not converted into a passing qualification by successful replay.

Native polar reconstruction, coupled replay and two-mesh static derivatives
were regenerated for all three C families. The scientific summaries are
unchanged; only their source-bound report hashes changed. The tandem's negative
lifting-only static margin is retained. Pre-merge raw evidence is preserved at
`/workspace/mirum-coupled-development-pre-a-integration`; refreshed evidence is
at `/workspace/mirum-coupled-development`.

The merged-runtime receipt is
[`../testing/cem-a-c-integration-results.json`](../testing/cem-a-c-integration-results.json).
Raw A reference and section reports are under
`/workspace/mirum-a-c-integration-ekamjoqx`. The original A-run receipt remains
unchanged. Raw directories are local artifacts, not hosted downloads or proof
of restoration in a fresh task.

No deployment, environment publication or restoration in a new task is implied
by Git integration. Future source/runtime/binary changes require matching new
evidence; these receipts do not qualify an expanded geometry or flight envelope.
