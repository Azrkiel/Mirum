# Remaining work: section aerodynamics

Status: **re-scoped 2026-11-05** by
[the low-Re method decision](cem-low-re-method-decision.md). Read that record
first. It governs wherever this backlog previously required complete XFOIL banks
below Re 50,000, or treated XFOIL bank completeness as the canard/tandem gate.

History: this backlog was opened for the section continuation that started at
`50279906`. Code, tests, evidence and handoffs were delivered in `7121188`, and
raw archives were published in `b2b70cb`. That work did not qualify the F3/F4
canard/tandem windows. Those failures stay recorded exactly as produced. The
change below is a design-scope decision taken before new experiments, not a
reinterpretation of results.

## Withdrawn or closed (do not resume)

- ~~Resolve low-Re initialization sensitivity and missing interior rows for
  neutral and +3° canard/tandem sections at Re 33,000.~~ **Withdrawn**: below the
  Re 50,000 method-validity floor (decision P1). The recorded failures
  (neutral 7.5° and deflected 7° missing at Re 33,000; panel ΔCl ≈ .035–.037 and
  ΔCd ≈ .0012–.0013 over limits) remain as historical evidence.
- ~~Resolve excessive post-transition Hk gradients and panel disagreements with
  further distribution, VACCEL or initialization settings.~~ **Closed: outside
  method validity.** The literature in the decision record explains why an
  integral boundary-layer method cannot represent this regime reliably.
- ~~Complete continuous XFOIL banks for the F3/F4 14 m/s initialization windows.~~
  **Bank requirement withdrawn** (decision P3). The missing 1.5°/1.25° rows and
  VACCEL=0 timeouts stay as diagnostics. These conditions are now covered by
  NeuralFoil (P2) plus single-point XFOIL spot checks at actual converged states.
- ~~Trace required swept-section coverage through complete passed banks only.~~
  Passed swept F1/F2 banks remain valid evidence and may still be used. New
  coverage no longer has to come from banks.

## In scope now, in dependency order

- [ ] **Verify the checkout and evidence** before resuming: runtime, native
  binaries, installed identities and the archive checksums in
  `docs/testing/artifacts/cem-panel-distribution/manifest.json`. Keep absent
  historical raw archives explicitly unavailable.
- [ ] **P1, Re floor in code.** Raise `reynolds_floor` in
  `backend/cem/glider/model.py` from 30,000 to 50,000. Apply it to every lifting
  and control surface station at the lowest declared speed, including trim and
  authority-limit states. Add the 50,000–70,000 degraded band (initial +30% Cd
  at P10, labelled as a placeholder) and the
  `section Re below method validity floor` fail reason. Bump the provenance
  version. Add positive tests and countercases at 49,999/50,000 and
  69,999/70,000.
- [ ] **Check the frozen swept states against P1.** Their passed F1/F2 banks
  start at Re 49,000. Either declare ≥ 2% more chord or speed so the lowest
  station clears 50,000, or record their rejection under P1. Do not lower the
  floor to keep them.
- [ ] **Resize canard/tandem development geometries.** Increase canard and fore
  surface chord (or the declared minimum speed) so every station is ≥ 50,000 at
  the lowest speed. Write the new operating domains to disk **before** running
  anything, then rerun the family development diagnostics on NeuralFoil (P2).
  If a family cannot satisfy P1 within its geometry/mass limits, record that as
  an explicit no-design result.
- [ ] **P2, confidence as constraint.** Keep `analysis_confidence ≥ 0.8` as a
  hard station gate and expose it as an optimizer constraint for the families in
  development.
- [ ] **P3, elite spot-check runner.** Reuse the existing coordinate-query,
  audit and replay code for single converged points (plus a derivative
  neighbourhood) instead of rectangles. Keep the 160/240-panel, native-header,
  raw-artifact and independent-replay checks, and keep Cl .025 / Cd .001 /
  Cm .005. Record non-converged points as "no XFOIL evidence", never filled.
  Mark designs with insufficient agreeing points `unverified`.
- [ ] **P4, measured anchor.** Add UIUC Low-Speed Airfoil Test polars for 2–3
  representative airfoils at Re 60,000–100,000 with source hashes. Measure
  NeuralFoil and XFOIL error per Re band, and replace the degraded-band
  placeholder with the measured error.
- [ ] **Regression and API checks** after each change: the combined suite below,
  plus provider-free API startup/stop/restart, computed feasibility and all three
  unqualified-family rejection checks. Diagnose socket restrictions instead of
  skipping API tests.
- [ ] **Records.** Update the qualification matrix, evidence receipt and
  [continuation prompt](cem-next-session-prompt.md). Preserve the historical
  **567-passed/1-failed** receipt byte-for-byte and all historical
  failed/aborted/unmatched outcomes. Publish new raw artifacts with checksums,
  then commit and push.

Required regression:

```sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Unchanged native discipline for every XFOIL run that still happens: sequential
execution, 20 seconds per invocation, 1 MiB per native file, 150 artifacts per
audit, native-angle domains and strict coordinate/source identities. No
extrapolation, endpoint clamping, row splicing, disjoint-cell bridging,
tolerance weakening or promotion of failed evidence.

## Historical windows (record only)

These were the declared rectangles before the decision. They are kept so the
receipts remain interpretable. They are **not** current requirements.

| Exact foil role | Re bounds | Alpha bounds, degrees | Recorded result | Current disposition |
| --- | --- | --- | --- | --- |
| Swept neutral/reflexed F1 | 49,000–74,000 | 3–6 | Passed/replayed | Valid evidence; lowest edge below P1 floor |
| Swept +3° pitch region F2 | 49,000–64,000 | 2.5–5.5 | Passed/replayed | Same as F1 |
| Canard/tandem neutral F3 | 33,000–73,000 | 4–8 | Failed | Withdrawn (P1) |
| Canard/tandem +3° fore/front F4 | 33,000–61,000 | 5–8 | Failed | Withdrawn (P1) |
| F1, 14 m/s initialization | 114,000–170,000 | 0–2 | Passed/replayed | Valid evidence |
| F2, 14 m/s initialization | 114,000–149,000 | 0–1.5 | Passed/replayed | Valid evidence |
| F3, 14 m/s initialization | 77,000–170,000 | 0–4 | Failed | Bank requirement withdrawn (P3) |
| F4, 14 m/s initialization | 77,000–142,000 | 1–4 | Failed | Bank requirement withdrawn (P3) |

Exact hashes, control channels and grids remain in the
[qualification matrix](cem-panel-distribution-continuation.md#explicit-section-qualification-matrix)
and [receipt](../testing/cem-panel-distribution-results.json).

## In scope: environment-onboarding follow-up (unchanged)

- [ ] Review, save and publish the prepared environment through the environment
  settings. This is a user/product action, not something a Git push does.
- [ ] Validate restoration in a genuinely new task: checkout, activation,
  dependencies, native/source/library identities, retained artifacts and the
  startup/readiness workflow. Record the actual results.

## Separate future projects (unchanged)

Whole-aircraft and physical-construction qualification are separate projects
with their own requirements:

- Whole-aircraft: revision-bound body/junction evidence and complete force/moment
  equilibrium; delivered mass, CG and full inertia; hardware, rate/sideslip,
  nonlinear/stall, dynamics, authority and mission-envelope qualification.
- Construction: aero/material geometry correspondence, attachment and fuselage
  load paths, all load cases, sizing/export/measurements, fixed-hardware closure,
  and material/process/coupon/bond/core/contact/bench evidence.

See [aircraft physics](cem-aircraft-physics.md),
[dynamics/control](cem-aircraft-dynamics-control.md) and
[B's qualification handoff](cem-task-b-qualification-handoff.md).

**Keep these gates closed.** The section decision changes how section data is
sourced. It does not enable whole-aircraft, construction, production or
verified-gain flags.
