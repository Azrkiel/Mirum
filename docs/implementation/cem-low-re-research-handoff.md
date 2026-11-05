# Handoff: low-Re section policy, code state and open research

Date: 2026-11-05. Read this **before**
[cem-low-re-method-decision.md](cem-low-re-method-decision.md). This handoff
records a later owner decision that the decision record and several handoffs do
not yet reflect.

## State of the branch (read first)

The work stopped mid-change at the owner's request so it could be committed.
**The code on this branch is in an intermediate state:**

| Item | State |
| --- | --- |
| `backend/cem/glider/validity.py` (new) | Shared station rule used by the evaluator, the parent evidence validators, the comparison validator and uncertainty propagation. Currently `REYNOLDS_FLOOR = 50000.` and `REYNOLDS_DEGRADED_BELOW = 70000.` |
| `model.py`, `evidence.py`, `comparison.py`, `uncertainty.py` | Wired to `validity.py`. Degraded designs get a profile-drag log-normal factor reaching +30% at P90, and the parent rejects bands that omit it |
| `cem/__init__.py` | `VERSION = 'mirum-cem-glider-0.2.0'` |
| `backend/tests/test_cem_low_re_validity.py` (new) | 10 tests, all passing, but pinned to the 50,000 floor |
| `backend/tests/test_cem_shapes.py` | Fixture updated to a 60,000 station Re with the degraded flag |

**Full CEM suite at this state:** 519 passed, 11 failed, 6 skipped, 24 errors.

- The **24 errors** (7 in `test_cem_pipeline.py`, 17 in `test_cem_segmented.py`)
  are caused by the 50,000 floor. The reference example glider's lowest station
  is at **Re 30,948** (`section Re below method validity floor (30948 < 50000)`),
  so the shared fixtures cannot build an admitted design.
- 9 of the 11 failures were already failing before this change in this
  container: 8 because XFOIL isn't installed (`xfoil executable unavailable`),
  plus `test_cem_comparison.py::test_real_pair_report_is_revision_bound_and_fails_closed_on_tampering`,
  whose strict type check in `evidence.number` rejects `numpy.float64` with
  numpy 2.5 (the lock pins 2.2.6). **The other 2 failures were not identified**
  before the work stopped. Rerun and name them before merging.

## Owner decision after the decision record (not yet applied)

The 50,000 hard floor rejects the CEM's own reference glider. That glider has
wing tips of about 13.5 cm and a tail of about 10 cm at about 5 m/s, which is
normal for small 10 m-launch gliders. Asked how to resolve this, the owner chose:

> **Keep the hard floor at Re 30,000 and apply the degraded-band penalty to
> every station from 30,000 up to 70,000.** XFOIL spot checks still run only at
> Re ≥ 50,000.

To apply it:

1. In `validity.py`, set `REYNOLDS_FLOOR = 30000.`. Keep
   `REYNOLDS_DEGRADED_BELOW = 70000.`. Consider a steeper penalty tier for
   30,000–50,000 (see research question R2). Update the comments.
2. Update `test_cem_low_re_validity.py` boundaries to 29,999/30,000 and
   69,999/70,000. The `test_cem_shapes.py` fixture can stay.
3. Rerun the full suite. The 24 errors should disappear, and the example
   glider becomes a degraded-band design, so its P10 band will drop. Update any
   assertion that pins the old bands, and record the new values.
4. Update the docs that currently say 50,000:
   - in `cem-low-re-method-decision.md`, the P1 section, the chord table, the
     effect-on-work table and the F1/F2 note;
   - `cem-remaining-section-work.md`;
   - `cem-next-session-prompt.md`;
   - `cem-handoff.md`, `cem-glider.md` and `cem-roadmap.md`;
   - `cem-task-a.md`, `cem-task-a-handoff.md` and
     `cem-task-b-qualification-handoff.md`;
   - `cem-task-c-handoff.md` and `cem-task-c-continuation.md`;
   - `cem-aircraft-dynamics-control*.md`, `cem-integration-continuation.md`,
     `cem-a-c-integration.md`, `cem-aerodynamic-verification.md` and
     `docs/plans/cem-family-aerodynamics-continuation.md`;
   - the superseded-scope banners on the seven historical records.

   What changes: under a 30,000 floor the canard/tandem sections at Re 33,000
   become **admissible in the degraded band**, so the "resize the canard" and
   "F1/F2 49,000 edge is below the floor" statements no longer apply. The F3/F4
   **XFOIL bank** requirement stays withdrawn (P3), because XFOIL is not run
   below 50,000.
5. Run the repo's merge-gate schema tests and the combined regression in the
   decision record. Then commit.

Do not merge this branch into `cem-integration-verification` until steps 1–3
pass.

## Open research questions

Literature already used: Lissaman (1983, *Annu. Rev. Fluid Mech.*), critical
Re ≈ 70,000; Klose, Spedding & Jacobs (arXiv:2108.04910), XFOIL against DNS and
two wind tunnels at Re 20,000; Sharpe & Hansman (arXiv:2503.16323), NeuralFoil
and its confidence score; AIAA doi:10.2514/1.45921, bubble penalty below about
50,000. The questions below are not settled by that literature.

- **R1, measured accuracy of NeuralFoil from Re 30,000 to 70,000.** Compare
  NeuralFoil (and XFOIL where it converges) against UIUC Low-Speed Airfoil Tests
  polars (Selig et al., *Summary of Low-Speed Airfoil Data*, vols. 1–5) for
  SD7003, E387, S1223 and a thin flat-bottomed or reflexed section at Re 40,000,
  60,000 and 100,000. Report Cl, Cd and Cm error per band. This is decision P4,
  and it should replace the +30% placeholder.
- **R2, penalty shape.** The current single log-normal factor (+30% at P90) is
  a placeholder. Should there be a steeper tier for 30,000–50,000, where the
  literature reports the largest drag rise and hysteresis? Derive it from R1's
  measured error, not by tuning to keep designs admitted.
- **R3, lift and stall, not just drag.** At Re 20,000 XFOIL underpredicts lift
  and its stall jump depends on sweep direction (Klose et al.). The current
  penalty only widens profile drag. Investigate whether CL slope, CLmax and Cm
  also need low-Re uncertainty, especially for tails and canards used for trim.
- **R4, tail and control surfaces.** The floor applies to tail stations as well
  as the wing. Lightly loaded tails tolerate low Re better than wings. Research
  whether a separate, documented rule for tail and control surfaces is
  justified (for example, judged by sensitivity of trim and stability). Do not
  relax the floor without that evidence.
- **R5, turbulence and Ncrit.** Real launch and flight air is more turbulent
  than Ncrit 9 assumes. Find measured or literature guidance on Ncrit for small
  outdoor gliders, because it moves the separation-bubble behaviour that drives
  this whole problem.
- **R6, whether NeuralFoil's confidence score tracks real error.** It is trained
  on XFOIL convergence, not on measured error. Check against R1's data whether a
  confidence ≥ 0.8 actually predicts small error from Re 30,000 to 70,000.
- **R7, flight calibration plan (G7).** Design the cheapest flight test that
  separates profile drag from induced drag for a degraded-band glider, so the
  calibration step can replace the placeholder with measured values.

## Other context for the next session

- `main` and the CEM branches have **no shared git history**. Bringing CEM into
  `main` needs a path-limited port (copy `backend/cem/`, `scripts/cem/`,
  `examples/cem/`, the CEM tests and the CEM docs), not a merge. A trial
  port gave 535 passed, 9 failed (environment-only) and 6 skipped. Do not take
  the CEM branch's `.gitignore`, `compiler.py`, `main.py` or `schemas.py`, which
  are older than `main`'s.
- This container lacked XFOIL. Native tests need the environment in
  `docs/implementation/cem-next-session-prompt.md`.
