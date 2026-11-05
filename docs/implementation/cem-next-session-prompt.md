# CEM continuation prompt

Copy the prompt below into the next session. This prompt was prepared from
`f783e3d879adbea485c03930b5a123139127083a`; implementation/evidence is at
`84b7a82b568524a4905e80397456255b7436715e`. Use the latest published continuation
branch, inspect newer commits before editing, and preserve user changes.

```text
Continue Azrkiel/Mirum on branch cem-integration-verification.
Baseline handoff: f783e3d879adbea485c03930b5a123139127083a.
Use the existing isolated checkout. Do not create a worktree.
Read repository instructions and the cloud runtime skill first.
If present, activate:
  source /workspace/mirum-env.sh

Read these current records first:
- docs/implementation/cem-interrupted-audit-continuation.md
- docs/testing/cem-interrupted-audit-results.json
Then read the supporting records:
- docs/implementation/cem-vaccel-initialization.md
- docs/testing/cem-vaccel-initialization-results.json
- docs/implementation/cem-coordinate-verification.md
- docs/implementation/cem-coordinate-windows.md
- docs/testing/cem-coordinate-window-results.json
- docs/implementation/cem-integration-continuation.md
- docs/testing/cem-family-development-envelope.json
- docs/implementation/cem-task-a-handoff.md
- docs/implementation/cem-task-b-qualification-handoff.md

Current state:
- Interrupted coordinate queries retain failure-stage inputs, source/executable,
  foil, commands, log and partial-output identities. Fresh full-audit replay of
  the deflected canard/tandem VACCEL=0 timeout now matches. That audit remains
  failed; this does not repair the absent historical full-audit replay or grant
  qualification/admission. Adversarial evidence tests passed.
- Regenerated foil/installed identities and native binary hashes differ from the
  historical receipt. Matching recorded package versions and disabling optional
  NumPy CPU features did not restore historical coordinate identities. The
  precise cause remains unresolved; do not relabel new coordinates as old ones.
- Required combined regression: 567 passed, 1 failed, no skips, two warnings.
  Failure: test_actual_installed_states_are_not_covered_by_historical_narrow_windows.
  The historical-domain diagnostic strictly rejects changed installed identities.
  The suite is not green. Do not weaken the identity check or rewrite historical
  evidence to make it pass.
- Fresh neutral swept Re 49,000–74,000 / alpha 3–6 degree audits passed/replayed.
  Direct initialization/panel checks at both previously reported neutral-foil
  request locations completed/replayed and agreed with the fresh bank within
  unchanged tolerances. Neutral reached a covered circulation root at 6 m/s,
  alpha 4 degrees, residual 8.7643e-15. This is not aircraft equilibrium or an
  independent native qualification at every converged strip.
- The deflected swept Re 49,000–64,000 / alpha 3.5–5.5 degree window passed and
  replayed. The subsequent +3-degree state failed with 5,010 coefficient requests,
  61 rejected requests and “No covered finite difference for root Jacobian”.
  First rejection: deflected foil, Re 49040.00482968711,
  alpha 3.02376103536733 degrees.
- The separately declared deflected alpha 2.5/4/5.5 degree window failed midpoint
  interpolation and replayed. At Re 56,000 / alpha 3.25 degrees, Cl error .0508
  exceeds .025 and Cm error .009325 exceeds .005. Its rows cannot supply a bank.
- Arbitrary-precision narrow alpha endpoints exposed a native rounding/boundary
  representation limitation. Those narrow audits failed/replayed and remain
  failed; separate direct queries do not promote them.
- Low-Re canard/tandem missing rows and interpolation failures persist. VACCEL=0
  has not demonstrated a coverage repair. Production gates remain closed.

Next work, in dependency order:
1. Fetch and inspect A/B before integrating. Last confirmed tips:
   A: 6b4df7a38a5e5eaf700686ff21fde9acd62683d3
   B: 5f5fc120163e18d37daf765e5956635a63bb1911
   No new published implementation was available at handoff. Respect unpublished
   or ongoing work; do not modify their branches without authorization.
2. Diagnose historical coordinate drift with authoritative runtime/source and
   artifact evidence. Verify retained receipts before reuse. Distinguish restoring
   historical byte-identical inputs from qualifying fresh inputs. Resolve the
   failing regression without removing its scientific/identity assertions. If
   original evidence is unavailable, preserve the failure and report that limit.
3. Investigate the measured deflected swept interpolation discrepancy with fresh
   exact-foil initialization, panel and midpoint checks. Declare grids/settings
   before each experiment. Trace subsequent trial and converged conditions using
   only passed/replayed complete banks. Never extrapolate, splice missing rows,
   bridge disjoint cells, silently extend bounds or weaken tolerances. Address
   native alpha representation explicitly without granting out-of-domain access.
4. Investigate low-Re canard/tandem failures using the checksum-verified XFOIL
   manual and explicit retained settings. The manual recommends inspecting
   bubble-region Hk and Ue/Vinf gradients before changing point bunching. Those
   boundary-layer dumps and a justified panel-distribution experiment remain
   unperformed. Bind any added diagnostic/settings to inputs, artifacts and
   independent replay; do not claim a numerical or physical repair prematurely.
5. Continue body/junction, aircraft equilibrium, nonlinear interaction, stall,
   dynamics, control-authority and construction work only where revision-specific
   inputs support it. Identify missing body/junction OML, full assembly inertia,
   actual hinge/directional hardware and authority requirements, aerodynamic/material
   correspondence, full attachment/fuselage load paths, all load components/cases,
   and material/process/coupon/bench evidence. Generic literature values and
   software fixtures cannot replace aircraft measurements or physical evidence.

Run native qualifications sequentially, without competing native jobs.
Preserve the native 20-second invocation limit, artifact caps, verification and
existing numerical tolerances. Reproducing a failure never grants qualification.
Regenerate source-bound evidence when implementation/input/runtime identities change.

Required combined regression:
  python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
    frontend/test_cem_ui.py analysis/tests/test_avl.py \
    analysis/tests/test_airfoil.py -q

Diagnose sandbox socket restrictions rather than skipping API tests. Repeat
actual provider-free API startup/stop/restart, computed feasibility and all three
unqualified-family rejection checks after relevant changes. Existing setup and
smoke helpers under /workspace/mirum-onboarding may be reused only after checking
presence and identities. A process-local non-credential sentinel is for
provider-free checks only, not real provider generation.

Retained evidence, if present:
- /workspace/mirum-interrupted-audit-evidence.tar.gz
  SHA256 e6129bb687bf35d9f2e6ac36d4e2a81e66dde5a9f9c25695d07d594890fcc73c
  Receipt records 2,132 verified member files. This is local workspace retention.
- /workspace/mirum-onboarding/interrupted-continuation.bundle
  SHA256 ac7ef8fd0cb62a1b55342732a26860733e2180644bd0e5b291ef59210cd77c60
  Requires baseline 3485598c3a31fce341f0ae6245d1a466688fe03f and retains
  implementation 84b7a82 only, not later documentation commits.

Check actual environment/draft status. Install/start instructions were saved,
including the delivered revision, but repository-selector writes were rejected
and the last checked draft still selected main. Set the continuation revision
through supported environment settings before publication. Publication and
validation in a genuinely new task remain unperformed; neither a saved draft,
local bundle, running API nor current-instance validation establishes them.
Real provider generation and interactive browser rendering are also unperformed.

Complete useful implementation and independent validation. Preserve all production
admission/search/comparison/archive/worker/API/UI gates until evidence qualifies.
Update the handoff and receipt with passed, failed, unsupported and unperformed
outcomes distinguished, then commit and push the continuation. Do not report a
clean regression, environment publication or restored historical evidence without
checking the actual result.
```
