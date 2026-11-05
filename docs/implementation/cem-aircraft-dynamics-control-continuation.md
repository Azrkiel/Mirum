# Continuation prompt: Task C dynamics and controls after the machinery delivery

Continue Azrkiel/Mirum from the commit that adds
[`cem-aircraft-dynamics-control.md`](cem-aircraft-dynamics-control.md) on
`cem-integration-verification` (based on `b2b70cb`). Fetch and inspect newer
published commits on `cem-integration-verification` and the A/B/C task
branches before editing, and again before regenerating evidence: the branch
advanced once during the previous session. Preserve local changes. Use the
existing isolated checkout; do not create a worktree. Read repository
instructions first.

Read, in order:

- `docs/implementation/cem-aircraft-dynamics-control.md` (scope, validation,
  actual results, remaining dependencies)
- `docs/testing/cem-aircraft-dynamics-control-results.json` (receipt) and
  `docs/testing/artifacts/cem-dynamics-control/` (raw evidence + manifest)
- `docs/implementation/cem-panel-distribution-continuation.md` (current
  section-aerodynamics matrix: only swept F1/F2 6 m/s banks pass)
- `docs/implementation/cem-aircraft-physics.md`, `cem-task-c.md`,
  `cem-task-c-handoff.md`, `cem-task-a-handoff.md`,
  `cem-task-b-qualification-handoff.md`

## Runtime

The previous session ran in a fresh cloud container without `/workspace`.
Recreate an equivalent runtime: a Python 3.12 venv with the 118 packages pinned
in `docs/testing/cem-native-angle-results.json` (`runtime.packages`), then
`scripts/cem/build_native_aero.py --directory <dir> --source-mirror
http://archive.ubuntu.com/ubuntu/pool/universe/x/xfoil/` (gfortran from the
distribution). The resulting XFOIL/AVL SHA-256 values matched the previous
receipt exactly (`0633e3…`, `228ac1…`). A session-local activation exported the
venv, `MIRUM_XFOIL_BIN`, `MIRUM_AVL_BIN` and single-thread BLAS. If
`/workspace/mirum-env.sh` exists, use it instead.

## Do not regress

- Production admission/search/comparison/archive/worker/API/UI gates closed;
  `verified_gain=false`; physical construction disabled; physical validation
  never claimed from software or fixtures.
- Sequential native execution, 20 s invocation limit, 1 MiB file cap,
  150-artifact audit cap, existing tolerances; no extrapolation, splicing,
  weakened identity checks or failed-evidence promotion.
- Unsupported contributions stay `unsupported`; a circulation root or
  lifting-only pitch closure is not trim. Missing rate/unsteady terms, hardware,
  hinge moments, directional control and requirements block — they are never
  defaulted. Static derivatives never replace rate or unsteady terms.
- The family mode rule (all five modes identified, real part < −1e-4) and the
  mission limits (Dutch roll ζ ≥ 0.05, spiral time to double ≥ 8 s) both apply.
- Envelopes are declared (written to disk) before evaluation; every declared
  point is reported; no scope narrowing after results.

## Next work, in dependency order

1. Supply missing inputs in their required forms (see the dependency table):
   body OML, `JunctionEvidence`, `ControlHardware`, `HingeMomentEvidence`,
   `AuthorityRequirementSet`, a complete `StabilityRequirement`, unsteady terms,
   a directional-control declaration or artifact-backed exemption, and
   `MeasuredAircraftProperties`.
2. With A: passed/replayed banks covering the trim, derivative-step and
   authority-limit domains (and canard/tandem F3/F4, which currently fail), plus
   rate and sideslip aerodynamics (a candidate is extending `coupled_aero` with
   rotational/sideslip local velocity and qualifying it independently, e.g.
   against native AVL derivatives, before use).
3. Re-run `scripts/cem/assess_family_dynamics.py` with those inputs in a new
   directory; qualify each family individually. Record every outcome as produced.
4. Stall sequencing and nonlinear manoeuvres need separate nonlinear evidence.

Run the focused suites, the combined regression and the API checks:

```sh
python -m pytest backend/tests/test_cem_family_dynamics_control.py \
  backend/tests/test_cem_family_physics.py backend/tests/test_cem_family_physics_integration.py -q
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
python scripts/cem/assess_family_physics.py --directory <new empty dir A>
python scripts/cem/assess_family_dynamics.py --directory <new empty dir B> --section-audits <dir A>
```

Repeat provider-free API start/stop/restart with real local sockets: metadata
200, computed feasibility 200, and HTTP 422 for each of the three
unqualified-family-only searches. Diagnose socket restrictions rather than
skipping. Update the handoff, receipt and this prompt; commit and push.
