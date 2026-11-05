# Explicit panel-distribution continuation

Raw evidence is now [published in GitHub with checksum manifests](../testing/artifacts/cem-panel-distribution/README.md).
The local-retention statements below describe the original validation/delivery
snapshot; the subsequent artifact delivery adds remote copies without changing
the evidence bytes or qualification outcomes.

Continues `50279906d857bfbb6a362ab78e984cd5e9f581a6` on
`cem-integration-verification`, in the existing isolated checkout. No worktree
was created. All whole-aircraft, physical-construction, production-admission and
verified-gain gates remain closed. The historical interrupted receipt, including
**567 passed, 1 failed**, is unchanged. See the accompanying
[section receipt](../testing/cem-panel-distribution-results.json) for this run.

## Source, runtime and retained evidence

Fetched the continuation and inspected current published A/B tips before editing.
The continuation still started at `50279906`; A remained
`6b4df7a38a5e5eaf700686ff21fde9acd62683d3` and B remained
`5f5fc120163e18d37daf765e5956635a63bb1911`. A's only unmerged change is acceptance
documentation already incorporated in the handoff; B is an ancestor. Neither
published branch was changed. Unpublished work in other tasks remains unknown.

The incoming checkout was clean main `36b996f` on `work`. No applicable AGENTS.md,
activation file, previous runtime or historical raw archives were present.
Fetching initially failed inside the restricted socket namespace; the same Git
operation through the configured proxy outside it succeeded. No authentication
was replaced and no new credential was requested. Actual API checks likewise use
the socket-capable context rather than skipping checks.

A new isolated Python 3.12.14 runtime uses repository pins and a retained frozen
lock. Signed official Debian indexes and checksum-verified packages supplied
workspace-local Fortran/CMake/X11 prerequisites. The checksum-pinned native build
retains all numerical/compiler safety flags. The initial link failed on an
extracted development-library symlink; resuming with the installed X11 runtime
library fixed that setup issue. Source archives, build log, manifest and binary
hashes are retained. Neither native binary matches the preceding runtime's hash.
Both require fresh evidence. All six installed-state and four exact-foil hashes
match the historical coordinate-window receipt, as does the recorded NumPy core
shared-object hash. This does not recover historical raw files or establish the
cause of the earlier runtime's coordinate drift.

## Declared development requirements

`scripts/cem/declare_section_envelope.py` reconstructs all six archived neutral
and +3-degree control states and rejects an installed identity mismatch before
experiments. It retains the swept solver-request windows and unresolved low-Re
canard/tandem windows without shrinking their bounds. The borrowed conventional
development requirements remain in the declaration, including the 15 m/s limit;
these section experiments do not claim that entire mission envelope is covered.

`scripts/cem/declare_initialization_envelope.py` separately brackets every foil's
zero-circulation strip request at the previously recorded **14 m/s, 0 degrees**,
sea-level, mesh-4 operating point. These additional required windows are retained
separately from the **6 m/s, 4 degrees** swept trial/converged windows. They are not
bridged across uncovered Re/alpha cells. Initialization coverage alone cannot
qualify induced-flow trials or converged conditions at 14 m/s. Required maneuver,
disturbance and control-authority envelopes remain absent, not silently inferred.

## Panel controls and reconstruction

Coordinate queries, interrupted-query records, full audits, banks and the
installed-state CLI now carry explicit `panel_distribution`. `None` emits the
same native commands as before. The supported JSON fields are:

- `refined_density_ratio`: finite numeric R from .01 through 10, excluding bool.
- `top_refined_x` and `bottom_refined_x`: two finite numeric chord coordinates,
  ordered strictly within [0, 1], mapped to XT and XB.

Unknown keys, empty objects, strings, booleans, nonfinite values and invalid
intervals are rejected before query artifacts or native work. Explicit settings
are emitted inside PPAR before PANE, in a fixed order. There is no automatic
setting choice, retry, altered foil, changed transition or substitute polar.

Reports bind exact coordinates, settings, commands, native executable identity,
query/native sources and retained artifacts. Full audits additionally bind the
audit and metadata-check sources and runtime versions. Independent reconstruction
checks deterministic foil/input identities even when coefficients happen to
agree after native rounding. Mixed distribution settings within a foil's bank
are rejected. Interrupted evidence preserves the explicit settings and cannot
be promoted to completed rows. Positive and adversarial checks exercise settings,
source tampering, forged/rehashed input commands, mixed banks, interrupted inputs,
full audit reconstruction and native controls. Native-angle domain checks,
positive-slope rejection, strict coordinate identities and all production guards
are preserved.

The installed-state CLI accepts `--panel-distribution settings.json` for both
generation and replay. Settings are part of its declared-input equality check.
All native jobs must run sequentially. The 20-second invocation limit, 1 MiB
native file limit, 150-artifact audit limit and Cl .025 / Cd .001 / Cm .005
thresholds are unchanged. Failed/incomplete/aborted outputs never supply banks.

## Boundary-layer investigation

The manual's SHA256 remains
`c36482e92f6cab424e965ccb48d0d81ebc030204ebf44a4205b366b9d7db3bea`.
The verified source BLDUMP writes HK in its column labelled H. The diagnostic
helper instruments complete initialization command sequences with a dump after
each requested ALFA, retaining commands, exact coordinates, native identity,
manual/source identities, every dump and independent repeats. It excludes wake
rows and orients each surface downstream; an untransitioned surface with no
eligible pairs is unassessed. A missing polar row cannot yield a converged BL
claim merely because a dump file exists.

Fresh default sweeps locate upper-surface Hk violations from approximately x/c
.308 to .938 across neutral and deflected sections. Unlike earlier ALFA-0-to-
target checks, these sweeps expose initialization/panel-dependent missing rows.
A newly declared R=4, XT=.29-.95 experiment includes all observed bubble regions
with margins. It improves some coverage but leaves missing rows and excessive
Hk gradients. A subsequent R=10 experiment with VACCEL=0 and unchanged transition
examines the remaining sensitivity. Refinement retains the same required low-Re
rectangles, adds geometric-mean Re stations and uses half-degree alpha spacing;
it does not splice any earlier rows. Full fresh audit/replay outcomes follow.

An initial diagnostic attempt aborted while parsing the wake's different column
count. It also overlapped the end of a test invocation and supplies no qualifying
evidence. The parser was corrected, its wake/transition countercase tested, and
the entire diagnostic rerun sequentially in a new directory. Its aborted files
are retained separately. A focused audit test run also overlapped a query-source
edit and failed source-bound reconstruction; the stable-source isolated rerun
passed. Neither failed attempt was relabelled as a passing receipt.

## Aircraft and construction limits

A circulation root is a lifting-model result, not full force/moment equilibrium.
Revision-bound body/junction geometry and validated interference/separation/error
models, nonlinear interaction and stall sequencing remain unsupported. Delivered
mass/CG/full inertia, hinge/directional hardware and required disturbance/control
moments remain absent. No dynamic or control-authority admission follows.

B's flat unswept/untwisted NACA 0012 prototype, 20 mm root cutbacks and local keel
do not represent these families. Aero/material geometry correspondence, complete
attachment/fuselage load paths, all strip force/couple components and load cases,
fresh sizing/export/measurement and fixed-hardware trim remain required. Core,
bond/peel/contact, material/process coupons and bench evidence cannot be replaced
by fixtures or generic values. A's low-Re reference failure and real paired-arm
spiral rejection remain historical qualification limits.

## Reproduce and retain

Use this existing isolated checkout; do not create a worktree. From
`/workspace/Mirum`, source `/workspace/mirum-env.sh` in each shell. The saved
installation refreshes a retained snapshot and checks its manifest and frozen
lock; it is not a claim that a bare checkout contains the missing native tools.

Generate the declarations before experiments, use fresh directories, then run
`scripts/cem/qualify_section_envelope.py` with an explicit declaration and optional
settings JSON / `--vaccel 0`. It performs the full initialization/repeat, 160/240
panel, independent midpoint and replay paths. The diagnostic helper remains
separate and never produces a qualified bank. Batch-driver exit zero means the
recording workflow completed, not that failed section states passed; inspect
every bank/point state and replay outcome. The installed-state audit CLI instead
returns nonzero for failed banks. The swept-condition helper checks
all traced bank requests and independently queries selected converged operating
points; only those queried points acquire direct native-check evidence.

The required combined regression remains:

```sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Actual provider-free API smoke checks use a process-local non-credential sentinel
only for metadata, computed feasibility and unavailable-family rejections. They
start, stop and restart actual owned API processes and make no provider call.
Real provider generation and interactive rendering remain unperformed.

Raw artifact retention is local workspace only. Git delivery, saved environment
configuration, publication and genuinely new-task restoration remain separate.
Review/save environment settings and publish to snapshot the prepared instance;
then verify restoration in a new task. Neither publication nor new-task
restoration is claimed by this continuation.

## Explicit section-qualification matrix

Exact foil identities (full coordinates, control archives and installed-state
identities are retained in the receipt):

- F1, swept neutral/reflexed:
  `e974ec3ceac9c7cc5beb6a177a37b65d3a4f06c8d6a65d059f139444c2820eca`.
- F2, swept +3° pitch region:
  `d8e3b2bda89b9977d81a456e93fa17f14d245d01e5116eade8e14428325c95bd`.
- F3, canard/tandem neutral:
  `e9426ae7efb82cf7284e292c707259f143eaab7862141409a9b3da31779a8b97`.
- F4, canard/tandem +3° fore/front pitch region:
  `f925aa8eb7278e37bf672436156955aaf51d4a37c4e72859213dfd2650f04f0d`.

All other control channels are zero. F1/F3 neutral regions remain present in
+3° aircraft; no entire aircraft was replaced by one deflected section. The six
installed-state hashes and exact channel names are in the receipt. Here, “pass”
means the declared numerical section check, never physical validation. Every
row retains its own complete audit; different windows/settings are not joined.
Re bounds below are endpoints; three-station grids add their geometric mean.
All use Ncrit 9, trips 1/1, Mach 0 and native ITER 150.

| Foil and window | Declared Re / alpha grid | Distribution / acceleration | Initialization and 160/240 panels | Independent midpoint checks | Full replay / supported use |
| --- | --- | --- | --- | --- | --- |
| F1, 6 m/s swept scope | 49,000–74,000, two Re; 3/4.5/6° | Native defaults | All four initializations/repeat and panel checks pass | Pass; max Cl .0139, Cd .0007025, Cm .0015 | Matched pass; development interpolation in this window only |
| F2, 6 m/s swept scope | 49,000/56,000/64,000; 2.5–5.5° by .375° | Native defaults | All initializations/repeat and panel checks pass | Pass; max Cl .0142783, Cd .00029536, Cm .00261653 | Matched pass; development interpolation in this window only |
| F3, low-Re refined | 33,000–73,000, three Re; 4–8° by .5° | R=10, XT=.29–.95, VACCEL=0 | All four 160-panel sequences miss 7.5° at Re 33,000; panel Cl .0354 and Cd .00124 exceed limits | Not established: incomplete coarse bank; independent midpoint queries retained | Matched failure; **no bank** |
| F4, low-Re refined | 33,000–61,000, three Re; 5–8° by .5° | R=10, XT=.29–.95, VACCEL=0 | All four 160-panel sequences miss 7° at Re 33,000; panel Cl .0371 and Cd .0013 exceed limits | Not established: incomplete coarse bank; independent midpoint queries retained | Matched failure; **no bank** |
| F1, 14 m/s initialization | 114,000–170,000, two Re; 0/1/2° | Native defaults | Initialization/repeat and panel checks pass | Pass; max Cl .02045, Cd .0002025, Cm .00335 | Matched pass; section window/zero-circulation initialization coverage only |
| F2, 14 m/s initialization | 114,000–149,000, two Re; 0/.75/1.5° | Native defaults | Initialization/repeat and panel checks pass | Pass; max Cl .00795, Cd .0002225, Cm .00155 | Matched pass; section window/zero-circulation initialization coverage only |
| F3, 14 m/s refined | 77,000–170,000, three Re; 0–4° by .5° | Native defaults | Reset-each misses 1.5° at Re 77,000; evaluable panel checks pass | Both midpoint queries pass (.02145/.00063/.00365); missing initialization still fails the bank | Matched failure; **no bank** |
| F4, 14 m/s refined | 77,000–142,000, three Re; 1–4° by .5° | Native defaults | 160-panel initialization sequences complete; 240-panel grid misses 1.5° at Re 77,000 | Both midpoint queries miss 1.25°; interpolation unestablished | Matched failure; **no bank** |

The preceding R=4 low-Re banks and coarse 14 m/s canard/tandem banks also fail
and independently replay. Their full interpolation errors remain in the receipt.
No row from them repairs a refined bank. The separately declared VACCEL=0,
default-distribution 14 m/s follow-ups hit the 20-second limit: F3 at the Re
77,000 240-panel sweep, F4 at the first independent midpoint. Their original
full replays are unmatched. Neither interrupted attempt supplies a bank or a
passing reconstruction; subsequent diagnosis remains separate from promotion.

The BL investigations retain additional spatial-resolution gaps. At Re 33,000,
R=10/XT=.29–.95/VACCEL=0, upper-surface Hk drops at 160/240 panels are approximately
2.905/2.206 (F3, 4°), 3.034/2.757 (F3, 6°), 2.980/2.270 (F4, 5°), and
2.434/1.771 (F4, 6.5°). F4 at 8° is also panel-sensitive: 2.498/.854. These
exceed the manual guideline at one or both resolutions. Lower surfaces without
eligible post-transition pairs remain unassessed. Complete coarse diagnostic
rows do not waive these gaps or the newly exposed missing interior rows.

## Actual swept conditions and supported use

Using only the fresh passed/replayed F1/F2 6 m/s banks, both neutral and +3°
states reach circulation roots in three iterations. Each retains **1,848**
initialization/Newton/converged coefficient requests, with **zero** rejected
requests. Independent standard solver replay matches the scientific payload of
the separately fingerprinted instrumented solve. The neutral residual is
8.8682e-15 and pitch moment -0.0281360 Nm; the +3° residual is 7.46865e-12 and
pitch moment -0.0433757 Nm. Both retain missing body/other contributions and do
not establish aircraft equilibrium.

`scripts/cem/check_swept_native_conditions.py` checks every traced coefficient
request against those exact fresh banks. It then declares six actual converged
conditions: the minimum- and maximum-Re strip for each foil/control-state pair.
At each, all four 160-panel initializations (including exact ascending repeat),
240-panel refinement and independent query replay pass. The largest bank or
initialization/panel differences are Cl .0110904, Cd .000298838 and Cm .00194227.
Comparisons use actual native rounded alpha, not relabelled arbitrary-precision
requests. These are independent native checks at **six selected conditions**;
other trial/converged strips retain interpolated-bank coverage. The 14 m/s
windows do not establish nonzero-circulation trials or converged flight there.

All eight directly comparable R=4 instrumented BL-query and standard audit grids
have identical scientific rows/missing-row lists. This validates the observed
instrumentation consistency for those cases, not the failed bank or bubble
resolution. Passing software tests cannot replace missing native coverage.

**The declared required envelope is not complete.** Reproducible interior
missing rows, excessive panel/interpolation differences, unresolved Hk resolution
and bounded interrupted attempts block the canard/tandem section qualification.
The implemented constant-distribution controls and tested refinements have not
resolved those numerical failures. This is not a proof that every possible
future distribution or numerical method fails. Further work must retain the
same required rectangles, resolve the spatial/initialization coupling and pass
whole fresh audits/replay within the unchanged limits. No extrapolation,
endpoint clamping, row union, tolerance weakening or failed-evidence promotion
is an acceptable repair.

## Interrupted replay diagnosis and delivery checks

Both VACCEL=0 14 m/s attempts remain failed. Subsequent independent full replay
matched the neutral **failed** audit, while the deflected replay still rejected
`Interrupted query differs from native reconstruction`. Both later directly
repeated interrupted conditions hit the same 20-second limit. Source, runtime,
executable, foil, input and retained polar-output identities match; partial log
hashes differ. This demonstrates sensitivity of interrupted log identities to
the bounded invocation. The first unmatched replay's expected payload was not
retained by `validate` and is not retrospectively recovered. Original unmatched
outcomes remain recorded; later matching failure replay is separate and cannot
provide a bank. The new diagnosis retains its expected reports and fresh raw
interrupted artifacts. No identity or timeout rule was weakened.

The required combined regression passed **601 tests, no failures or skips**, with
**two warnings**, in **844.68 seconds**. Its exit status is **0**; the log SHA256
is `6a8ff5f372c32d8d6cd23e9fd732609fe60500b16f6659c306059173c3588072`.
All new native checks were included. A separate non-native focused run passed
57 tests with 17 native cases deselected; that focused run alone was not the
native validation. The source-edit-related earlier failed focus run is retained
separately and does not replace this final combined result.

All **14** retained bank audits pass current source, binary and capped-artifact
identity inspection, with **181** completed query reports plus the separately
retained interrupted invocations. This is integrity verification, not a claim
that their scientific qualification passed. The qualification matrix retains
every failed and unmatched state. All six regenerated installed identities and
four exact coordinate foils match the historical window receipt.

Actual API startup, stop and restart on the **same port and work directory**
passed metadata HTTP 200, computed feasibility HTTP 200 and the exact unavailable-
family HTTP 422 rejection for each of tailless-swept, canard and tandem in both
cycles. Earlier two-cycle smoke evidence is retained separately. No provider
call was made. Repeat dependency compatibility checks cover 124 frozen packages;
the final saved install verifies native/runtime-library/helper identities and
executes successfully. Both install/start fields were saved and read back with
exact matching contents; the exact saved installation script was exercised.

Raw evidence is retained locally as
`/workspace/mirum-panel-distribution-integrated-evidence.tar.gz`. The final receipt records
its digest and independently verified member inventory. It contains fresh
inputs/declarations, successful/failed/aborted audits and diagnostics, selected
native checks, solver traces, source versions, signed-package provenance,
checksum-pinned native sources/binaries/build manifest, runtime lock and tested
setup/API helpers. It does not contain recovered historical raw archives.
Source delivery and the repository selector are bound after committing; the
receipt does not embed its own enclosing commit. Review/save the environment
settings and publish to snapshot this prepared instance. Publication and
restoration in a genuinely new task remain unperformed.

## Published update integrated during delivery

The final pre-push fetch found new published merge
`f1b193f245ba418bae31f2cefe5c8b4be0cbb9bf`, containing
`099d524` aircraft-physics machinery. A/B tips remained unchanged. Local work was
preserved, the existing checkout fast-forwarded, and the section changes reapplied.
The only conflict was the continuation's cross-links; both records are retained.
See [aircraft physics](cem-aircraft-physics.md) and its separate receipt.

The update adds frame/wrench/mass/trim/dynamics machinery, explicit missing-input
contracts and 34 analytical/integration tests. It keeps real-aircraft,
construction and verified-gain gates closed. Calculated screening full-tensor
inertia is machinery/data modelling, not measured delivered inertia or aircraft
qualification. Its original raw archive is unavailable locally and was not
restored or used as this runtime's native evidence.

Every numerical/test source previously recorded for the section work remains
byte-identical. Reconstructed installed declarations remain byte-identical, and
both standard coupled solver reports independently replay after integration.
The retained native banks therefore keep their original exact source/binary
identities; none was rewritten to fit the update. The builder change only adds
an optional source mirror and manifest field: pinned archives, Fortran flags,
AVL revision and headless patch are unchanged. This runtime retains its existing
executables, built with the original builder whose exact source is preserved in
`/workspace/mirum-setup/build-native-source-used.py`. The updated refresh manifest
checks both that provenance and the current builder source.

The 601-test run above belongs to the pre-integration section revision. The
current receipt separately records the complete repeated regression after
integrating the published physics update, along with repeated setup/API checks.
The original pre-integration archive remains unchanged; final integrated evidence
uses `/workspace/mirum-panel-distribution-integrated-evidence.tar.gz`.

The final integrated regression passed **635 tests, no failures or skips**, with
**two warnings**, in **879.91 seconds**, exit **0**. Its log SHA256 is
`0d2d55cad98e7ca64a44dcb690b784d945a697943d140fc3778694afaf2076a1`.
Actual same-port/work-directory API restart checks and the saved installation
script passed again after integration. The new aircraft ledger was also run for
all six actual archived control states: swept lifting used only the retained
passed/replayed banks, canard/tandem supplied no failed bank, and every assessment
remained **unsupported** with every qualification/production/construction/gain
flag false. All six full-tensor mass states remain `calculated_screening`.
