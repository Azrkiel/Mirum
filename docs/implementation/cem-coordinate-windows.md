# Bounded coordinate-window continuation

> **Historical record — superseded scope (2026-11-05).** The results here are
> unchanged and remain valid evidence of what was run. The
> [low-Re method decision](cem-low-re-method-decision.md) now governs section
> work: no section below Re 50,000 is admissible, and further XFOIL tuning below
> that floor (panel distribution, VACCEL, initialization) is closed. Use
> [cem-remaining-section-work.md](cem-remaining-section-work.md) for current tasks.

Continuation of `e1f540d` and [coordinate-family verification](cem-coordinate-verification.md).
The new [receipt](../testing/cem-coordinate-window-results.json) belongs to this
runtime and the source hashes recorded there. Historical receipts are unchanged.
No aircraft family or construction mode is admitted by this work.

## Numerical work completed

The coordinate auditor previously abandoned every initialization comparison,
panel comparison and positive-slope check at a Reynolds station when any one
initialization lacked rows. It now compares each independently complete pair,
compares the complete ascending/refined grids even if another initialization
failed, and checks each complete grid's lift slope. Incomplete grids remain
failures and supply no replacement rows. A regression combines a missing
initialization with a panel disagreement to ensure both failures survive.

Reports retain every evaluated initialization/panel pair and the angle and two
coefficients at its largest absolute error. Midpoint diagnostics retain Re,
alpha, predicted/observed coefficients and the error for each worst coefficient.
Unevaluated interpolation diagnostics remain null. Replay reconstructs these
fields; editing a diagnostic is rejected. This source change invalidates old
source-bound receipts; it does not rewrite them.

Four unique exact foils occur across the three default family archives at neutral
and +3 degree pitch-channel states. The receipt stores each complete archive,
explicit controls, installed-state identity and the exact-coordinate hashes.
Neutral regions remain present in deflected aircraft; they were not replaced by
a wholly deflected wing. Each foil audit uses four initialization sequences
(including an ascending repeat), 160/240 panels and native midpoint queries.
Each of the six audit attempts made 11 queries and independently replayed all
11. All used the original 20-second native invocation limit.

| Exact foil (SHA256 prefix) | Declared Re endpoints | Alpha grid, degrees | Result | Max midpoint errors Cl / Cd / Cm |
| --- | --- | --- | --- | --- |
| Swept neutral/reflexed `e974ec3cea` | 100,000–120,000 | -1, 0, 1 | passed | .019475 / .000325 / .004 |
| Swept +3° pitch region `d8e3b2bda8` | 100,000–120,000 | -1, 0, 1 | passed | .016325 / .0002925 / .0032 |
| Canard/tandem neutral `e9426ae7ef` | 100,000–120,000 | -1, 0, 1 | failed | not established |
| Canard/tandem +3° pitch region `f925aa8eb7` | 100,000–120,000 | -1, 0, 1 | failed | not established |
| Canard/tandem neutral `e9426ae7ef` | 200,000–220,000 | 2, 3, 4 | passed | .00045 / .000035 / .00005 |
| Canard/tandem +3° pitch region `f925aa8eb7` | 200,000–220,000 | 2, 3, 4 | passed | .001875 / .000235 / .00005 |

The failed neutral trial lacks alpha -0.5° at midpoint Re 109,544.51150103322.
The failed deflected trial lacks alpha 0° at Re 100,000 in ascending and repeated
ascending queries. The positive-angle window avoids that unresolved region;
it does not repair it. Both failed audits replayed faithfully and remain failed.

Tolerances remain Cl .025, Cd .001 and Cm .005. These are development numerical
diagnostic tolerances, not measured aircraft error bounds. The passing results
establish the declared sampled section audits only. They do not establish a
continuous flight envelope, stall behavior, physical validity or aircraft gains.
The swept and canard/tandem windows are different; do not combine them into one
polar grid or extrapolate them to uncovered local flight conditions. Wider
historical windows remain failed. The canard/tandem trials show why even an
arbitrarily small window cannot be assumed to converge.

## Reproduce and retain evidence

Use the existing isolated checkout, without creating a worktree. Activate
`source /workspace/mirum-env.sh`. Source archives, native build settings, binary
hashes, runtime versions and implementation identities are recorded in the
receipt. Run native qualifications sequentially; do not run another qualification
job alongside the combined test suite. Do not relax the timeout to rescue a run.

The exact batch drivers are retained with raw evidence under
`/workspace/mirum-onboarding/`. `audit-windows.py` and
`audit-positive-window.py` build the default archives and explicit control states,
deduplicate exact coordinate foils and run `audit` followed by independent
`validate`. They deliberately refuse existing output directories. For a fresh
rerun, select new directories in copies of these drivers, or supply the receipt's
archive and controls to `scripts/cem/audit_coordinate_polars.py` with the declared
`--reynolds` and `--angles` grids and a fresh `--directory`, followed by `--replay`.
A faithful failed audit exits 1; a passing section audit exits 0 and still grants
no production admission.

The raw evidence archive and its digest are recorded in the receipt. It includes
all six raw audit directories, the drivers and the native build manifest.
These are local workspace artifacts, not remotely published evidence. Environment
publication and validation of restoration in a new task remain separate actions.

## Delivery checks

The affected native suite passed 25 tests. The final combined regression passed
**541 tests, no failures or skips**, with two dependency deprecation warnings,
in 807.00 seconds. Its full log SHA256 is recorded in the receipt. That command is:

```sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
```

Actual HTTP requests returned 200 for metadata and provider-free feasibility,
and 422 for production discovery restricted to each unqualified family. The
full API import requires `GEMINI_API_KEY_HEAVY`; this check used a process-local
`offline-fixture-never-sent` sentinel solely for provider-free routes. It is not
a credential and does not establish working provider generation. No real provider
key was present or requested. UI regression checks are fixture tests, not an
interactive browser rendering check.

Repeat installation passed with all 94 CEM packages unchanged and compatible,
and native source/build settings and executable hashes verified. The API was
stopped and restarted, then the same functional HTTP checks passed again.
The environment draft now saves the complete frozen-dependency `install_script`,
`start_skill` with restart/readiness instructions, and the actual handoff-base
repository revision/path. These replace the initial main-branch PicoGK setup:
this branch lacks `codex-cloud/` launchers. No network or secret requirement was
added. Draft saving is not publication; review/save the settings and publish to
snapshot this instance. A genuinely new task is still needed to test restoration.

The continuation and this receipt are committed together. No push was performed.
The receipt identifies the handoff base and verified source hashes; it does not
embed its own enclosing commit ID. The existing base `e1f540d` was already pushed.

## Part A/B availability checked

The remote A head checked during this continuation was
[`6b4df7a`](https://github.com/Azrkiel/Mirum/commit/6b4df7a38a5e5eaf700686ff21fde9acd62683d3).
Its only change beyond the A commits already integrated here updates
`cem-task-a-handoff.md` with acceptance criteria; it contains no new solver or
qualification evidence. It explicitly leaves low-Re interpolation, justified
body/interference and numerical error bounds, paired-comparison acceptance and
C's actual-family trim/stall/stability qualification open. Its numerical rigid
references do not provide measured construction or full delivered inertia.

The remote B head was `5f5fc120163e18d37daf765e5956635a63bb1911`, already an
ancestor of this checkout. Its prototype and qualification handoff are therefore
already available here; there is no newer pushed construction evidence to
integrate. Uncommitted work in another task is not visible through these refs.
Both heads were inspected read-only; no in-progress A/B branch was modified or
merged. Recheck the refs when their owners deliver fresh evidence.

## Remaining prerequisites for the requested engineering gates

| Requested outcome | Current evidence and precise next prerequisite |
| --- | --- |
| Actual foil windows | The four bounded sampled audits above pass. Wider low-Re and near-zero-alpha coverage remains unresolved; qualify every actual local Re/alpha/control state required by a declared mission before using it in aircraft closure. |
| Complete aircraft aerodynamics | Installed archives contain lifting OML only. Supply revision-bound body/junction geometry and justified body/interference force and moment models, plus validation references and a declared speed/alpha/flight envelope. Full equilibrium and nonlinear/stall qualification cannot follow from these section receipts. |
| Stability and control | Supply delivered full inertia, hardware/payload positions, directional-control geometry and required maneuver/disturbance moments. Then qualify full-aircraft derivatives, dynamic modes, fixed-revision equilibrium and authority/stall sequencing over that envelope. Existing lifting-only static derivatives do not satisfy this gate. |
| B's construction | B supports a flat unswept, untwisted NACA 0012 prototype with a root cutback, local keel and unresolved mechanisms. None of the three families has corresponding qualified attachments or complete load paths. See [B's qualification handoff](cem-task-b-qualification-handoff.md): family-specific delivered geometry, all load cases, structural sizing and independently measured mass/CG/full inertia are required. Coupon/process, bond peel/core/contact and complete fuselage-path evidence are absent. Software fixtures cannot supply these measurements. |
| Production integration | Keep admission/search/comparison/archive/worker/API/UI gates closed until the preceding family-specific evidence exists and the parent can independently reconstruct it. No flag, tolerance or production guard was weakened here. |
| Publication/restoration | Installation/startup instructions can be saved, but this chat cannot establish publication or a fresh task's restoration. Review/save settings and publish through the environment UI, then perform the checks in a genuinely new task. |

These missing inputs prevent completion of all requested aircraft/physical
qualification tasks. They are not waived by a clean regression, successful native
replay or passing narrow section audit.
