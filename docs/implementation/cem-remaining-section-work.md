# Remaining work: numerical section qualification

Status: **incomplete**. This is the explicit backlog for the section-aerodynamics
continuation that started at `50279906`. Code, tests, evidence and handoffs were
delivered in `7121188`; raw archives were published in `b2b70cb`. Delivery and
passing software tests did not complete the numerical qualification goal.

This checklist clarifies scope; it changes no scientific result or admission
flag. It incorporates the published dynamics/control documentation now present
at `341c16c`, without counting that separate machinery as section qualification.

## In scope: outstanding numerical work

Execute in dependency order. Keep each item open until its evidence is recorded.

- [ ] Verify the receiving checkout, runtime, native binaries, exact installed
  states and archive checksums before resuming. Read the current receipts and
  inspect newer published A/B/integration tips without changing their branches.
  Use the existing isolated checkout; do not create a worktree. Activate
  `/workspace/mirum-env.sh` if present. Published archives are under
  `docs/testing/artifacts/cem-panel-distribution/`; check `manifest.json` before
  reuse. Changed bound sources/binaries require fresh evidence, not retagged
  receipts. Keep absent historical raw archives explicitly unavailable and the
  earlier coordinate-drift cause unresolved unless new evidence establishes it.
- [ ] Reconfirm the required exact foil/control identities, grids and development
  operating conditions from the declaration drivers and qualification matrix.
  Preserve neutral regions within deflected aircraft. Do not shrink bounds or
  select only favorable points to obtain a pass. Any added mission/control
  scope must be explicitly declared before experiments.
- [ ] Resolve low-Re initialization sensitivity and missing interior rows for
  **both neutral and +3-degree canard/tandem sections**, using the checksum-
  verified XFOIL manual and fresh boundary-layer evidence. Current refined
  Re 33,000 attempts miss neutral alpha 7.5 degrees and deflected alpha 7 degrees.
  Complete three-angle diagnostic sweeps did not repair those finer sweeps.
- [ ] Resolve the excessive post-transition Hk gradients and panel disagreements.
  The tested R=4 and R=10 distributions, including VACCEL=0, were insufficient.
  Bind every further setting or numerical-method change to exact coordinates,
  commands, sources, binaries and retained artifacts. No eligible lower-surface
  pairs means unassessed, not passed. Do not assume a larger density ratio or
  successful individual query establishes adequate resolution across a bank.
- [ ] Resolve the separate canard/tandem **14 m/s, 0-degree initialization-window**
  failures: missing 1.5-degree rows at Re 77,000, missing deflected 1.25-degree
  midpoint rows, and bounded VACCEL=0 timeouts. Preserve original unmatched
  interrupted replays and distinguish any later matching failed replay. Retain
  new diagnostic expected payloads and interrupted raw outputs; the first
  mismatch payloads were not retained and cannot be claimed recovered.
- [ ] For every required bank, run complete initialization sequences, ascending
  repeats, 160/240-panel checks, positive-slope/coverage checks, independent
  alpha/log-Re midpoint queries and full independent replay. A failed or
  incomplete bank supplies no replacement rows. Interpolation that could not be
  evaluated remains unestablished. Follow each runner's documented state and
  exit semantics; successful batch execution alone is not qualification.
- [ ] Verify required swept-section coverage through only complete passed/replayed
  fresh banks, tracing actual initialization, Newton-trial and converged requests.
  Preserve the existing six selected native-condition checks as individual-point
  evidence; other strip conditions remain interpolated. Add independent native
  checks where needed for the declared scope. The separate 14 m/s windows prove
  initialization coverage only; do not claim trial/converged flight there or
  bridge them to the disjoint 6 m/s windows.
- [ ] After relevant implementation changes, run the required combined regression
  below and actual provider-free API startup/stop/restart, computed feasibility
  and all three unqualified-family rejection checks. Diagnose socket restrictions
  instead of skipping API tests. Add meaningful positive/adversarial tests for
  any new settings, provenance or reconstruction behavior.
- [ ] Update the qualification matrix, evidence receipt and continuation prompt
  with exact foil/control identity, declared domain/settings, initialization,
  panel/midpoint outcomes, independent replay, supported use and remaining gaps.
  Publish new raw artifacts with checksums, then commit and push. Preserve the
  historical **567-passed/1-failed** receipt byte-for-byte and all historical
  failed/aborted/unmatched outcomes. Do not mark the required envelope complete
  while any required coverage or sensitivity failure remains.

The required rectangles remain:

| Exact foil role | Re bounds | Alpha bounds, degrees |
| --- | --- | --- |
| Swept neutral/reflexed F1 | 49,000–74,000 | 3–6 |
| Swept +3-degree pitch region F2 | 49,000–64,000 | 2.5–5.5 |
| Canard/tandem neutral F3 | 33,000–73,000 | 4–8 |
| Canard/tandem +3-degree fore/front region F4 | 33,000–61,000 | 5–8 |
| F1, separate 14 m/s initialization | 114,000–170,000 | 0–2 |
| F2, separate 14 m/s initialization | 114,000–149,000 | 0–1.5 |
| F3, separate 14 m/s initialization | 77,000–170,000 | 0–4 |
| F4, separate 14 m/s initialization | 77,000–142,000 | 1–4 |

These are not new declarations of passing banks. Exact full hashes, control
channels, intermediate Re stations, alpha grids/settings and current results are
in the [qualification matrix](cem-panel-distribution-continuation.md#explicit-section-qualification-matrix)
and [receipt](../testing/cem-panel-distribution-results.json). F1/F2 passes must
be preserved; F3/F4 required windows remain failed. The untested remainder of the
borrowed max-15 m/s mission is not silently treated as qualified or added to this
bounded continuation's completion claim.

Required regression:

```sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

All native execution remains sequential, with no competing native jobs. Preserve
20 seconds per invocation, 1 MiB per native file, 150 artifacts per audit and
Cl .025 / Cd .001 / Cm .005 tolerances. Preserve native-angle domains and strict
coordinate/source identities. No extrapolation, endpoint clamping, row splicing,
disjoint-cell bridging, tolerance weakening or promotion of failed evidence.

## In scope: environment-onboarding follow-up

- [ ] Review/save and publish the prepared environment through environment
  settings. Scripts and repository selection are saved; this is a user/product
  publication action, not an action completed by Git push.
- [ ] Validate restoration in a genuinely new task: checkout/revision, activation,
  dependencies, native/source/library identities, retained artifacts and the
  documented startup/readiness workflow. Publication or an archive checksum
  alone does not prove restoration. Record actual validation results.

## Separate future projects: excluded from this section task

Whole-aircraft and physical-construction qualification are **not** completion
requirements for this bounded numerical section task. Their existing machinery
and gaps remain documented, rather than being counted as missing code to rewrite:

- Whole-aircraft work: revision-bound body/junction evidence and complete force/
  moment equilibrium; delivered mass/CG/full inertia; hardware, rate/sideslip,
  nonlinear/stall, dynamics, authority and mission-envelope qualification.
- Construction work: aero/material geometry correspondence, full attachment and
  fuselage load paths, all loads/couples/cases, sizing/export/measurements,
  fixed-hardware closure, material/process/coupon/bond/core/contact/bench evidence.

See [aircraft physics](cem-aircraft-physics.md),
[dynamics/control](cem-aircraft-dynamics-control.md) and
[B's qualification handoff](cem-task-b-qualification-handoff.md) for their own
requirements. Existing analytical machinery and calculated screening tensors are
not measured or actual-aircraft qualification. Later declared dynamics/control
envelopes are separate work; their bank dependencies do not silently widen the
rectangles above. Deployment, real provider generation, interactive rendering,
fabrication acceptance and production release are not part of this section task.

**Keeping these gates closed is in scope now.** Even after the section backlog
passes, do not enable whole-aircraft, construction, production or verified-gain
flags without their separate required evidence. A circulation root or section
pass is not aircraft equilibrium or physical validation.
