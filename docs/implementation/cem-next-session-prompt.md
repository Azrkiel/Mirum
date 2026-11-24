# CEM continuation prompt

Use the latest published `cem-integration-verification` tip. This continuation
started at `50279906d857bfbb6a362ab78e984cd5e9f581a6`; fetch and inspect newer
published commits before editing. Use the existing isolated checkout and preserve
local work; do not create a worktree.

```text
Continue Azrkiel/Mirum on cem-integration-verification.
Read repository instructions and the cloud runtime skill first.
If present, source /workspace/mirum-env.sh in every shell.

Raw task archives and checksum manifests are now committed under
docs/testing/artifacts/cem-panel-distribution/. These are fresh task evidence,
not recovered historical archives. Verify their checksums before reuse.

Read docs/implementation/cem-low-re-research-handoff.md first: the code is
mid-change and the owner's latest choice (floor 30,000, penalty to 70,000) is
not applied yet. Then read docs/implementation/cem-low-re-method-decision.md. It is an owner
decision (2026-11-05) and governs any conflicting text below or in older
handoffs. In short: a hard section Re floor of 50,000 at the lowest declared
speed, with a degraded band from 50,000 to 70,000; NeuralFoil with
analysis_confidence >= 0.8 as the primary section source; XFOIL only as
single-point spot checks on selected designs at Re >= 50,000; and accuracy
anchored to measured UIUC wind-tunnel polars. The Re 33,000 canard/tandem
windows are withdrawn. Do not resume XFOIL tuning (panel distribution, VACCEL,
initialization) below Re 50,000.

Then read docs/implementation/cem-remaining-section-work.md and complete its
in-scope backlog in order. Whole-aircraft and construction qualification are
separate future projects. Keep their gates closed. Environment
publication/restoration is a separate outstanding onboarding follow-up.

Read current records:
- docs/implementation/cem-aircraft-dynamics-control.md
- docs/testing/cem-aircraft-dynamics-control-results.json
- docs/implementation/cem-aircraft-physics.md
- docs/testing/cem-aircraft-physics-results.json
- docs/implementation/cem-panel-distribution-continuation.md
- docs/testing/cem-panel-distribution-results.json
- docs/implementation/cem-native-angle-continuation.md
- docs/testing/cem-native-angle-results.json
Then read the coordinate-window, coordinate-verification, VACCEL and interrupted
records, the recorded development requirements and A/B qualification handoffs.

The pre-integration section run passed 601 tests, no failures or skips, two
warnings, exit 0 (844.68 seconds). A final fetch then found published physics
merge f1b193f245ba418bae31f2cefe5c8b4be0cbb9bf. It was integrated without
changing any numerical-bound source or installed identity; standard solver
replays matched again. The section receipt records the separate final combined
regression after that integration: 635 passed, no failures or skips, two warnings,
exit 0 in 879.91 seconds. The original archive is preserved separately. Preserve the historical 572-passed native-angle run and
the byte-for-byte unchanged 567-passed/1-failed interrupted receipt separately.
This runtime regenerated all six historical installed identities and all four
exact foils; its NumPy core hash also matches the preceding receipt. The earlier
runtime's coordinate-drift cause remains unresolved. Historical raw archives
were absent, not restored/replayed. Current native binary hashes differ and
all numerical claims require their own fresh source/binary-bound evidence.

Fetched A 6b4df7a38a5e5eaf700686ff21fde9acd62683d3 and
B 5f5fc120163e18d37daf765e5956635a63bb1911 before editing. Neither branch was
modified. A's only unmerged acceptance documentation is already incorporated;
B is an ancestor. Re-fetch current published tips; unpublished work is unknown.

Production admission/search/comparison/archive/worker/API/UI gates, verified-gain
and physical-construction flags remain closed. Full force/moment equilibrium,
body/junction/nonlinear/stall models, full inertia/dynamics/authority, delivered
construction geometry/load paths and material/process/bench measurements remain
unqualified. A's failed low-Re reference and actual paired-arm spiral rejection
remain qualification limits. A section bank or circulation root cannot waive
those gates or provide missing measurements.

New coordinate query/audit/replay and installed-state CLI paths carry validated
panel_distribution settings. None leaves default native commands unchanged.
Supported JSON fields: refined_density_ratio (.01 through 10), top_refined_x and
bottom_refined_x (strictly ordered two-number lists inside [0,1]). Booleans,
unknown keys, empty objects, nonfinite values and invalid intervals are rejected.
Settings bind exact coordinates, native commands, query/audit/native source and
binary identities and retained artifacts. Mixed distributions within a foil's
bank are rejected. Use --panel-distribution settings.json on the installed CLI.
Changed query/audit sources require fresh receipts; do not rewrite fingerprints.

HISTORICAL CONTEXT (superseded by the low-Re decision; kept so the receipts
remain interpretable). These were the rectangles declared before that decision.
The swept banks remain valid evidence. The canard/tandem 6 m/s rectangles are
withdrawn as below the method-validity floor, and the 14 m/s canard/tandem bank
requirement is replaced by NeuralFoil plus spot checks. Any new operating
domain must still be declared on disk before native execution. Do not shrink a
declared domain after seeing results, use only favorable endpoints or union
disjoint cells.
- Swept neutral/reflexed: Re 49,000–74,000, alpha 3–6 degrees.
- Swept +3 pitch foil: Re 49,000/56,000/64,000, alpha 2.5–5.5 by .375 degrees.
- Canard/tandem neutral: Re 33,000–73,000, alpha 4–8 degrees.
- Canard/tandem +3 fore/front pitch foil: Re 33,000–61,000, alpha 5–8 degrees.
The low-Re refinement adds geometric-mean Re and .5-degree alpha spacing, with
R=10, XT=.29–.95 and VACCEL=0, across the same rectangles. Both fail and replay:
Re 33,000 interior rows 7.5 degrees (neutral) and 7 degrees (deflected) remain
missing in all 160-panel initialization sequences; panel disagreements exceed
unchanged limits. No replacement bank exists from those refined attempts.

The earlier R=4, XT=.29–.95 experiments also retain midpoint/initialization/panel
failures. Full coarse diagnostic sweeps and independent repeats are not complete
continuous-window qualification. At Re 33,000, Hk drops still exceed the verified
manual's guideline at several angles/panel counts, even after R=10/VACCEL=0.
Lower surfaces with no eligible post-transition pairs remain unassessed.

The recorded 14 m/s, 0-degree zero-circulation requests were also explicitly
retained, not dropped in favor of 6 m/s results. Swept default-setting banks pass
and replay at Re 114,000–170,000, alpha 0/1/2 (neutral), and Re 114,000–149,000,
alpha 0/.75/1.5 (+3 foil). These are separate windows, not a union with low-Re
cells or proof of 14 m/s trial/converged flight. Canard/tandem default refinements
at Re 77,000–170,000/142,000 and alpha 0–4/1–4 retain missing 1.5-degree rows;
deflected midpoint queries also miss 1.25 degrees. VACCEL=0 follow-ups hit the
20-second limit and originally had unmatched interrupted replay. Consult the
receipt's retained subsequent diagnosis; neither timeouts nor a later matching
failed replay supply a bank. Preserve unavailable first-mismatch payloads as
unavailable. Do not assume timing-sensitive partial hashes constitute a repair.

Both fresh 6 m/s / 4-degree swept states reach independently replayed circulation
roots through complete passed/replayed banks. Each trace has 1,848 covered
requests, zero rejected trials. Pitch moments remain approximately -0.028136 and
-0.043376 Nm, so aircraft equilibrium is unestablished. Six selected actual
converged minimum/maximum-Re conditions pass separate native initialization,
repeat, panel and independent replay checks. Only these six have that direct
point evidence; other strips remain bank-interpolated. Native rounded alpha is
used, never relabelled or clamped to the higher-precision request.

The physics package now supplies frame/wrench/full-tensor-mass/trim/dynamics
machinery and explicit required-input contracts. These are not actual-aircraft
qualification. Use the existing physics.assessment and interfaces.REQUIRED_INPUTS;
do not reimplement them or treat calculated screening inertia as measured. The
published physics raw archive was unavailable locally, not restored/replayed.
Dynamics and controls (unsteady terms, derivatives, mode requirements,
physical-surface authority, declared envelopes with replay) are now implemented
in the same package; every actual-family envelope is blocked by the inputs listed
in cem-aircraft-dynamics-control.md. Use physics.envelope and
physics.qualification rather than reimplementing them.

Next work, in dependency order (see cem-low-re-method-decision.md):
1. Inspect current source/runtime/binaries and retained artifact identities.
   Do not infer restoration from a Git commit or recreate absent historical rows.
2. P1 is implemented for the production glider path
   (backend/cem/glider/validity.py, VERSION 0.2.0). Apply the same
   station_validity rule at each development family's trim and authority-limit
   states when wiring it into admission. Never lower the floor to make a search
   return a design.
3. Check the frozen swept states against P1 (their banks start at Re 49,000).
   Resize the canard/tandem development geometries, or raise the declared
   minimum speed, so every station clears 50,000. Declare the new domains on
   disk first, then rerun the family diagnostics on NeuralFoil. If a family
   cannot meet P1, record an explicit no-design result.
4. P2: keep analysis_confidence >= 0.8 as a hard station gate and expose it as
   an optimizer constraint.
5. P3: build a single-point XFOIL spot-check runner for selected designs from
   the existing query/audit/replay code: 160/240 panels, native headers,
   raw-artifact binding, independent replay and unchanged tolerances. A
   non-converged point is "no XFOIL evidence" and is never filled.
6. P4: add a measured UIUC low-speed-airfoil reference test (Re 60,000-100,000)
   and feed the measured error into the degraded-band and uncertainty bands.
   NeuralFoil/XFOIL agreement is consistency, not accuracy (NeuralFoil is
   trained on XFOIL).
Keep the untested remainder of the 15 m/s mission and the missing family
control/disturbance requirements explicit.

Run all native jobs sequentially with no competing regression/qualification.
Preserve the 20-second invocation, 1 MiB native-file and 150-artifact audit caps;
Cl .025, Cd .001, Cm .005; native-angle and strict coordinate/source checks.
No extrapolation, endpoint clamping, row splicing, disjoint-cell bridging,
tolerance weakening or failed/aborted evidence promotion.

Required combined regression:
 python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
   frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
Diagnose restricted socket execution rather than skipping API tests. After
relevant changes repeat actual provider-free API startup/stop/restart, computed
feasibility and all three unqualified-family rejection checks. The retained
helper uses only a process-local non-credential sentinel, never a provider key.

Inspect actual environment and configuration draft status. Install/start fields
were saved, read back and the exact saved install executed. Snapshot refresh is
not a bare-checkout bootstrap. A repository selector or pushed source does not
establish publication or genuinely new-task restoration. Review/save environment
settings and publish through the product, then validate restoration in a new task.
Real provider generation and interactive rendering remain unperformed.

Continue useful implementation and validation until the declared scope passes
or concrete blockers exhaust useful progress. Update the explicit qualification
matrix, handoff, evidence receipt and this prompt, retaining passed, failed,
aborted, unmatched, unassessed and unsupported states separately. Keep all
production/physical gates closed, then commit and push.
```
