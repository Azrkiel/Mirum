# Decision: low-Reynolds method validity and section-evidence policy

Status: **adopted 2026-11-05 by the project owner.** This record supersedes every
earlier handoff, backlog item or prompt that requires complete XFOIL section
banks below Re 50,000, or that treats XFOIL bank completeness as the gate for
canard/tandem family development. Where another CEM document conflicts with this
one, this one governs. Historical receipts and results are **not** changed; they
remain true records of what was run.

## Why

The canard/tandem section backlog (F3/F4) stalled on XFOIL failures at Re 33,000:
missing interior rows near 7–7.5°, 160/240-panel disagreements above tolerance,
excessive post-transition Hk gradients and initialization sensitivity. R=4, R=10,
VACCEL=0 and refined spacing did not fix them. The literature says this is
expected behaviour of the method in that regime, not a setting to be found:

- Lissaman, "Low-Reynolds-Number Airfoils", *Annu. Rev. Fluid Mech.* 15 (1983),
  citing McMasters & Henderson (1980): airfoil performance changes abruptly at a
  critical Re of about **70,000**. Below it, laminar separation without (or with
  unstable) reattachment dominates.
- Klose, Spedding & Jacobs, "Direct numerical simulation of cambered airfoil
  aerodynamics at Re = 20,000" ([arXiv:2108.04910](https://arxiv.org/abs/2108.04910)):
  XFOIL underpredicts lift at almost every angle. Its lift jump moves between
  9.5° (sweep up) and 6.5° (sweep down). Two wind tunnels also disagree (9° vs 7°).
  The cause is unsteady trailing-edge vortex shedding, which an integral
  boundary-layer method cannot represent.
- Sharpe & Hansman, "NeuralFoil" ([arXiv:2503.16323](https://arxiv.org/abs/2503.16323)):
  XFOIL solutions are not unique (partly a real physical hysteresis) and are only
  C0-continuous in the inputs. Only 56% of 7.9 million generated XFOIL cases
  converged. NeuralFoil's `analysis_confidence` was trained on that convergence
  record, so it flags exactly these regions.
- AIAA, "Planform and Camber Effects on the Aerodynamics of Low-Reynolds-Number
  Wings" (doi:10.2514/1.45921): below about Re 50,000 the separation bubble
  raises drag and degrades performance.

The backlog required complete, repeatable, sweep-direction-independent XFOIL
banks at Re 33,000. Those properties do not exist reliably in that regime, so the
requirement could not be met by more numerical work. Surfaces operating there are
also poor designs: they are slow, small and dominated by separation.

This is a **design-scope decision made before new experiments**, not a post-hoc
narrowing of a declared window to manufacture a pass. The withdrawn windows stay
recorded as failed. They are not relabelled as passed.

## Policy

### P1. Minimum section Reynolds number (design rule)

- **Hard floor: Re ≥ 50,000** at every station of every lifting and control
  surface (wing, tail, canard, tandem front/rear, elevons), evaluated at the
  **lowest declared operating speed** of the mission, including trim and
  authority-limit states.
- A design with any station below the floor is **rejected** with
  `fail_reasons` naming the surface and station (`section Re below method
  validity floor`). It is not penalized and kept.
- **Degraded band: 50,000 ≤ Re < 70,000.** Admission is allowed, but:
  - the uncertainty propagation widens profile drag for affected stations. The
    initial prior is **+30% Cd at P10**. This is an engineering placeholder, not
    a published value. Replace it with the measured error from P4, then with
    G7 flight calibration;
  - the report states that the design operates in the degraded band;
  - a `verified_gain` claim may not rest on a station in this band unless P3
    spot checks pass there.
- Replace the current `reynolds_floor=30000.` in `backend/cem/glider/model.py`
  (`validity_envelope`). Version the change in provenance so old IDs are
  preserved, and add positive and countercase tests at 49,999 / 50,000 and
  69,999 / 70,000.
- Never lower this floor to make a search return a design. Raising it per
  airfoil from measured data (P4) is allowed.

Physical meaning at sea-level ν ≈ 1.48e-5 m²/s:

| Lowest speed | Min chord for Re 50,000 | Min chord for Re 70,000 |
| --- | --- | --- |
| 6 m/s | ≈ 12.3 cm | ≈ 17.3 cm |
| 8 m/s | ≈ 9.3 cm | ≈ 13.0 cm |
| 10 m/s | ≈ 7.4 cm | ≈ 10.4 cm |

The failed F3/F4 low-speed sections ran at Re 33,000, about 8 cm chord at 6 m/s.
To pass the floor, those canard/fore surfaces need about 50% more chord, or a
higher declared minimum speed. Some small-canard layouts will become infeasible.
That is a valid engineering result: report it as an explicit no-design outcome.

### P2. Primary section data: NeuralFoil with a confidence gate

- NeuralFoil is the primary polar source for search, trim, derivatives and
  uncertainty within P1's floor.
- `analysis_confidence ≥ 0.8` remains a hard per-station admission gate. Also
  pass it to the optimizer as a constraint, as the NeuralFoil paper recommends,
  so search stays inside the trusted region.
- NeuralFoil is trained on XFOIL, so it is **not independent evidence**. It
  inherits XFOIL's biases and adds smoothness and guaranteed answers. Do not
  describe NeuralFoil and XFOIL agreement as validation.

### P3. XFOIL becomes an independent spot check, not a bank prerequisite

- XFOIL runs only at Re ≥ 50,000, and only on **selected elites/designs**, at
  their actual converged operating points (each station's Re and alpha, each
  control state), plus a small neighbourhood for derivatives.
- Each spot check keeps the existing discipline: exact coordinates and
  transition, native-header checks, 160/240-panel comparison, raw-artifact
  binding, independent replay and the unchanged Cl .025 / Cd .001 / Cm .005
  tolerances.
- A **non-converged XFOIL point is "no XFOIL evidence at that point"**. It is
  recorded and never filled, spliced or interpolated. It does not by itself
  reject a design that passes P1/P2. However, a design whose spot checks are
  mostly missing or disagreeing is `unverified` and cannot support
  `verified_gain`.
- Complete continuous XFOIL banks over declared rectangles are **no longer a
  prerequisite** for family development, search admission or dynamics/control
  work. Banks that already passed (swept F1/F2) remain valid evidence and may
  still be used where they apply.

### P4. Accuracy is anchored to measured data

- Add a measured-reference test using the UIUC Low-Speed Airfoil Tests (Selig et
  al., *Summary of Low-Speed Airfoil Data*, vols. 1–5; m-selig.ae.illinois.edu)
  for 2–3 airfoils representative of the installed foils (for example SD7003 and
  E387, plus one reflexed or thin section if available) at Re 60,000–100,000.
- Record NeuralFoil and XFOIL error against measured Cl, Cd and Cm per Re band.
  Feed that error into the P1 degraded-band penalty and the general uncertainty
  bands, and cite the measured source and its hash.
- Measured data is the only route to an accuracy claim. Agreement between
  NeuralFoil and XFOIL, or between XFOIL runs, is a consistency check only.
- G7 flight calibration remains the final arbiter for the actual aircraft.

### P5. Unchanged

- All production, manufacturing, `verified_gain`, family-admission,
  whole-aircraft and physical-construction gates stay closed until their own
  evidence exists. P1–P4 change how section aerodynamics are sourced. They do
  not satisfy body, junction, equilibrium, inertia, dynamics, authority, stall,
  construction or material requirements.
- No tolerance weakening, extrapolation, endpoint clamping, row splicing,
  disjoint-cell bridging or promotion of failed evidence.
- Historical receipts (including the 567-passed/1-failed receipt) stay
  byte-for-byte unchanged.

## Effect on existing work items

| Item | Previous status | Disposition under this decision |
| --- | --- | --- |
| F3 canard/tandem neutral, Re 33,000–73,000, 6 m/s | Failed; open backlog | **Withdrawn**: below P1 floor. Failure stays recorded. Resize the surface (or raise minimum speed) so all stations are ≥ 50,000, then declare a new operating domain before running anything |
| F4 canard/tandem +3° fore/front, Re 33,000–61,000 | Failed; open backlog | **Withdrawn**, same as F3 |
| F3/F4 14 m/s initialization windows, Re 77,000+ | Failed: missing 1.5°/1.25° rows, VACCEL=0 timeouts | **Bank requirement withdrawn** (P3). Use NeuralFoil (P2) plus spot checks at actual converged points. The recorded failures stay as diagnostics |
| Low-Re Hk-gradient / panel-distribution / VACCEL investigation at Re 33,000 | Open backlog | **Closed: outside method validity.** Do not continue tuning |
| Swept F1/F2 banks, Re 49,000–74,000 | Passed / replayed | **Still valid evidence.** Note: the 49,000 lower edge is just below the P1 floor. The frozen swept states need ≥ 2% more chord or speed for their lowest stations to clear 50,000, or they are rejected by P1 |
| Task A NACA 0006 low-Re reference, Re 30,000–60,000 | Failed at the Re ≈ 34,641 midpoint | **Withdrawn below 50,000.** If a low-Re numerical reference is still wanted, declare a new window at Re 50,000–100,000. Replace the "accuracy" role with P4 |
| Model-wide station floor `reynolds_floor=30000` | In code | **Raise to 50,000** (P1, code change pending) |

## Implementation order

1. P1 in code: floor, degraded band, fail reason, tests, provenance version bump.
2. P2: confidence as an optimizer constraint for families still in development,
   using the production gate that already exists.
3. Resize the canard/tandem development geometries (or declare a higher minimum
   speed) so they satisfy P1. Rerun the development diagnostics on NeuralFoil.
4. P3: an elite spot-check runner that reuses the existing coordinate-query,
   audit and replay machinery for single points, not rectangles.
5. P4: the UIUC measured-reference test and the error-to-uncertainty mapping.
6. Then continue the whole-aircraft, dynamics/control and construction work
   documented in the family handoffs, which this decision leaves unchanged.

Run the existing combined regression after each step:

```sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```
