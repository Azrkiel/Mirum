# Continuation prompt: aircraft physics after the machinery delivery

Superseded as the latest Task C continuation by [cem-aircraft-dynamics-control-continuation.md](cem-aircraft-dynamics-control-continuation.md).

Continue Azrkiel/Mirum from the commit that adds
[`cem-aircraft-physics.md`](cem-aircraft-physics.md) (based on
`cem-integration-verification` at `50279906d857bfbb6a362ab78e984cd5e9f581a6`).
Fetch and inspect newer published commits on `cem-integration-verification`
and the A/B/C task branches before editing. Use the existing isolated
checkout; do not create a worktree. Read repository instructions first.

Read, in order:

- `docs/implementation/cem-aircraft-physics.md` (gap matrix, machinery, scope)
- `docs/testing/cem-aircraft-physics-results.json` (receipt)
- `docs/implementation/cem-native-angle-continuation.md` and its receipt
- `docs/implementation/cem-task-c.md`, `cem-task-c-handoff.md`,
  `cem-task-a-handoff.md`, `cem-task-b-qualification-handoff.md`

## Runtime

This delivery ran in a fresh cloud container without `/workspace`. The
session-local activation `/home/user/mirum-env.sh` (not committed) activates a
Python 3.12 venv installed from the receipt's 118 pinned packages and exports
`MIRUM_XFOIL_BIN`/`MIRUM_AVL_BIN`. Native tools were rebuilt with
`scripts/cem/build_native_aero.py --source-mirror
http://archive.ubuntu.com/ubuntu/pool/universe/x/xfoil/` because
`deb.debian.org` is blocked by the network policy; both pinned source archive
SHA-256 values matched. The compiler (Ubuntu GFortran 13.3) differs from the
historical one, so executable hashes differ and native outputs are fresh
evidence only. Recreate an equivalent runtime if the container is new.

## Do not regress

- Production admission/search/comparison/archive/worker/API/UI gates closed;
  `verified_gain=false`; physical construction disabled.
- Sequential native execution, 20 s invocation limit, 1 MiB file cap,
  150-artifact audit cap, existing tolerances; no extrapolation, splicing,
  disjoint-cell bridging, weakened identity checks or failed-evidence promotion.
- Historical failed regressions and qualification limits (A low-Re
  interpolation/missing rows, real paired-arm spiral rejection, failed coarse
  and 0.75-degree deflected audits, aborted oversized refinement).
- Unsupported contributions stay `unsupported`; never zero-filled. A
  circulation root or lifting-only pitch closure is not aircraft trim.

## Next work, in dependency order

1. Obtain or build each missing input in its documented required form
   (`interfaces.REQUIRED_INPUTS`): revision-bound body OML per family,
   `JunctionEvidence`, `ControlHardware`, hinge-moment evidence,
   `AuthorityRequirement`, and `MeasuredAircraftProperties`.
2. With A: extend passed/replayed coordinate banks to every installed and
   deflected foil over the converged local Re/α domain needed by trim
   iterations (not just the single 6 m/s, 4° point), plus rate/sideslip
   aerodynamics if dynamics are required.
3. Run `assessment.assess` on the real archive with those inputs. Only a
   complete supported ledger may enter `trim.solve`; then `dynamics.linearize`
   and `dynamics.control_authority`. Record every outcome class as produced.
4. Qualify stall sequencing separately with nonlinear evidence.

Run the focused suites and the combined regression:

```sh
python -m pytest backend/tests/test_cem_family_physics.py \
  backend/tests/test_cem_family_physics_integration.py -q
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
python scripts/cem/assess_family_physics.py --directory <new empty directory>
```

Repeat provider-free API start/stop/restart, computed feasibility and the
three unqualified-family HTTP 422 checks after relevant changes. Update the
handoff, receipt and this prompt; commit and push.
