# Coordinate-family verification after A/C integration

This continuation closes the three software verification gaps identified in
[cem-a-c-integration.md](cem-a-c-integration.md). It does not qualify additional
aircraft families or construction methods.

The coordinate circulation solver now uses A's covered damped Newton method.
Coordinate-bank extrapolation and post-peak queries raise the shared
`PolarCoverageError`, so rejected trial steps can be damped without invented
coefficients. An uncovered initial point or an unestablished root remains a
numerical failure. Reports retain convergence, iteration and rejected-domain
trial diagnostics and bind the shared numerical solver's source identity.
Adjacent-alpha continuation remains an explicit, recorded initial condition.

Exact-coordinate XFOIL queries check the reported coordinate foil name,
Reynolds number (with native header rounding), zero Mach, both Ncrit values and
both transition trips. The foil's name identifies the loaded coordinate file;
it is never interpreted as a NACA code. Retained coordinate bytes, input
commands, native executable identity and independent native replay bind the
actual reflexed or deflected shape. All original NACA checks remain in place.

`families/coordinate_audit.py` audits a declared section window with four
initialization sequences (including a repeat), 160/240-panel refinement and
direct native queries at geometric-mean Reynolds numbers and alpha-cell
midpoints. It checks all initialization pairs, repeatability, positive lift
slope, complete rows and coefficient disagreement. The declared development
tolerances are Cl 0.025, Cd 0.001 and Cm 0.005; these are numerical diagnostic
limits, not validated aircraft error bounds. Independent queries continue when
one grid is incomplete. Missing rows are retained and never combined into a
replacement polar. Unevaluated interpolation errors are reported as null.

Every query retains raw artifacts and a scientific report. Audit replay checks
artifact hashes and repeats native queries before comparing the complete audit.
A faithful failed-audit replay returns `False` for admission, just as a passing
section audit does. Rehashed or promoted reports cannot supply qualification.

## Reproduce

Use the existing checkout, without creating a worktree. Activate the prepared
environment in each shell:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python -m pytest backend/tests/test_cem_coordinate_audit.py \
  backend/tests/test_cem_family_coordinate_polars.py \
  backend/tests/test_cem_family_coupled_aero.py -q
```

The installed-state CLI requires an exact family archive and an explicit JSON
value for every installed control. It deduplicates actual foil coordinates,
audits every unique foil and binds the receipt to the installed-state identity.
Run each required control state separately. Fresh directories preserve previous
evidence:

```sh
python scripts/cem/audit_coordinate_polars.py /tmp/family-archive.json \
  --controls /tmp/controls.json --directory /tmp/new-coordinate-audit
python scripts/cem/audit_coordinate_polars.py /tmp/family-archive.json \
  --controls /tmp/controls.json --directory /tmp/new-coordinate-audit --replay
```

`--reynolds` and `--angles` accept explicit increasing grids. Generation and
faithful replay exit 1 if any foil audit fails. Exit 0 establishes only the
declared section audit, never aircraft admission. Source, binary, coordinate or
control changes require fresh evidence. The CLI refuses to overwrite an existing
generation directory.

For A's positive/countercase references and intentionally failed low-Re section
reference, retain the original commands and boundaries in the integration
handoff. Run native qualifications sequentially and retain the native runner's
20-second limit. Source-bound coupled reports from before this continuation are
historical and must be regenerated.

## Remaining engineering gates

Installed body/junction effects, complete force/moment and trajectory closure,
nonlinear interaction/stall sequencing, directional control, full inertia and
dynamic modes, calibrated authority envelopes and B's family-applicable
construction/load-path evidence remain open. Neither complete sampled polars,
successful interpolation, converged lifting loads nor stable lifting-only
static derivatives enables production admission or verified performance gains.

## Current-instance results

The [new verification receipt](../testing/cem-coordinate-verification-results.json)
records this source/runtime separately from A's and the merged branch's historical
receipts. Raw output directories in that receipt are local artifacts.

The focused suite passed **24 tests**, including real native queries, a passing
narrow NACA 0012 coordinate audit (Re 900,000–1,000,000, alpha −1°–1°),
failed-reference replay, altered-report rejection and covered root-solver failures.

Neutral and 3° pitch-control states were audited for all three families. The four
unique exact foils each completed 11 native queries; every query was repeated
and the complete audit replay matched. The wider Re 30,000–300,000,
alpha −4°–8° windows remain failed:

| Exact foil | Retained audit findings |
| --- | --- |
| Neutral reflexed swept | Panel-refinement and midpoint-interpolation disagreement |
| Deflected swept | Missing requested rows at Re 300,000; interpolation not established |
| Neutral canard/tandem section | Initialization sensitivity and midpoint-interpolation disagreement |
| Deflected canard/tandem section | Missing alpha 8° in an initialization sweep at Re 30,000, plus midpoint disagreement |

A's conventional, T-tail and V-tail references and their instability countercases
were regenerated and replayed successfully. The low-Re NACA 0006 reference
retains its interpolation failure at Re 34,641.016151377546 and missing alpha
2.125°; failed-reference replay matched. Coupled lifting-load reports and static
derivatives were regenerated and replayed at both mesh levels for each family.
The tandem's negative lifting-only static margin is retained.

The provider-free API returned metadata and a computed feasibility result with
HTTP 200, and HTTP 422 for each
unqualified-family-only production search. Repeat installation, native binary
hash checks and dependency compatibility passed. Reusable installation and
startup fields were saved in the environment draft. Review/save the environment
settings and publish to snapshot this instance; publication and restoration in a
new task have not been performed.

The combined CEM/glider/UI/AVL/XFOIL run completed **538 passed, 1 failed, no
skips**, with two dependency deprecation warnings, in 896.89 seconds. Its only
failure was the existing broad NACA 0006 initialization audit: ascending native
queries reached the unchanged 20-second limit while other native jobs were
running. The identical test passed on an isolated rerun (**1 passed**, 62.52
seconds across its separate invocations). No timeout was relaxed and no assertion
was disabled. The final affected suite separately passed all **24 tests** in
48.37 seconds. The combined run began before the last native-audit test was
added; that test is included in the final affected suite.
