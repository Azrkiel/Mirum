# Exact-foil initialization and acceleration continuation

> **Historical record — superseded scope (2026-11-05).** The results here are
> unchanged and remain valid evidence of what was run. The
> [low-Re method decision](cem-low-re-method-decision.md) now governs section
> work: no section below Re 50,000 is admissible, and further XFOIL tuning below
> that floor (panel distribution, VACCEL, initialization) is closed. Use
> [cem-remaining-section-work.md](cem-remaining-section-work.md) for current tasks.

Follow-up: [interrupted-audit reconstruction and fresh swept trials](cem-interrupted-audit-continuation.md). The results below remain the historical run; the follow-up does not repair its absent raw replay.

Continuation from `3fe617c95968fc7a3a7928c64012f6f77be03fbc` on
`cem-integration-verification`, in the existing isolated `/workspace/Mirum`
checkout. No worktree was created. Production gates remain closed.

## Upstream and retained-state inspection

Read the continuation, family development target, coordinate-window and
coordinate-verification records, and A/B handoffs. Native Git failed to reach
`proxy:8080` inside the command sandbox; the same read outside that namespace
worked. Fetching A/B found their tips unchanged at `6b4df7a38a5e5eaf700686ff21fde9acd62683d3`
and `5f5fc120163e18d37daf765e5956635a63bb1911`. B is already an ancestor;
A's only unmerged commit supplies acceptance documentation already incorporated
in the receiving handoff. No new solver, construction or measurement evidence
was available to integrate. Neither upstream branch was changed. Unpublished
work in other tasks remains unknown.

The incoming checkout was clean `work` at main's `36b996f`, not the requested
continuation. The requested branch was fetched and checked out at its exact
starting commit. `/workspace/mirum-env.sh`, the prior venv/native toolchain,
`continuation-evidence.tar.gz`, `continuation.bundle`, and the older
`coordinate-window-evidence.tar.gz` were absent. Their receipt hashes could not
be verified; no old raw audit replay is claimed. The initial configuration draft
selected main and had null install/start fields. This is evidence of missing
retained state, not successful restoration of the previous environment.

## Explicit numerical experiment

The checksum-verified Debian XFOIL 6.99 source manual, section "Viscous solution
acceleration", says VACCEL drops small off-diagonal terms in the Newton system.
It recommends investigating a reduction, including zero, below Re 100,000.
It states that VACCEL does not affect the final converged solution *if attained*.
This is numerical guidance, not a measured low-Re accuracy or aircraft bound.
The manual is retained with the verified source archive and build artifacts.

Coordinate queries/audits and the installed-state CLI now accept explicit
`vaccel` / `--vaccel`. Omission (`None`) leaves native behavior unchanged;
finite values from 0 through .01 are supported, rejecting booleans and invalid
values before native work. Commands set `VACC` inside `VPAR` before the first
`ALFA`. Reports, audit inputs and source identities bind the setting; replay
compares regenerated deterministic input and foil artifact identities as well
as scientific rows. This prevents an altered setting from passing merely because
the final rounded rows happen to agree. A coordinate bank rejects mixed settings
within a foil's Reynolds grid. There is no automatic retry or setting selection.

Example, with an explicit archive and every installed control supplied:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python scripts/cem/audit_coordinate_polars.py /tmp/archive.json \
  --controls /tmp/controls.json --directory /tmp/new-audit \
  --reynolds 49000 64000 --angles 4 4.75 5.5 --vaccel 0
python scripts/cem/audit_coordinate_polars.py /tmp/archive.json \
  --controls /tmp/controls.json --directory /tmp/new-audit \
  --reynolds 49000 64000 --angles 4 4.75 5.5 --vaccel 0 --replay
```

These example bounds belong to the deflected swept foil, not every foil in an
arbitrary archive. The CLI audits every unique installed foil and fails if any
fails. Use fresh output directories; never combine incomplete grids or settings.
Changed implementation identities invalidate source-bound historical receipts.
The old receipts are preserved as historical; the new receipt is separate.

## Fresh evidence and interpretation

The retained `audit-initialization.py` driver reconstructs all six historical
archive/control identities, deduplicates their four exact foils, and computes
each foil's actual zero-circulation strip conditions at 6 m/s, alpha 4 degrees,
sea level, mesh 4. It brackets Re to the next 1,000 and alpha to the next half
degree, with three declared angles. Each foil is audited separately under the
native default and explicit zero acceleration. The driver retains complete
archive/control identities and the exact derived bounds in `summary.json`.
Each audit attempts four initialization sequences, 160/240 panels and native
midpoints, followed by independent reconstruction of completed queries. Seven
audit replays matched; the eighth stopped at a native timeout and its incomplete
query could not be reconstructed by the existing audit replay. Tolerances remain Cl .025,
Cd .001, Cm .005; the 20-second invocation limit is unchanged. Jobs are sequential.

These are sampled section audits near **initialization**, not a claim that every
interior state, Newton trial or converged flight condition is qualified. Neither
speed/alpha defaults nor A's conventional numerical development target supplies
family maneuver/disturbance requirements. The historical domain diagnostics at
6 m/s / 4 degrees and 14 m/s / 0 degrees were freshly recomputed and reproduce the
previous outside-window counts; those diagnostics do not replay historical audits.

## Environment reproduction and release boundaries

A fresh isolated Python 3.12.14 venv was installed using repository pins for the
backend/frontend/CAD/CEM stack, including numpy 2.2.6, scipy 1.15.3 and casadi
3.8.2. Other resolved packages are frozen in the retained `runtime.lock`; they
are not assumed identical to the absent old lock. Signed Debian indexes and
verified packages supplied a workspace-local Fortran 14 compiler and X11 headers.
No package-signature, source-checksum or TLS verification was disabled. The first
build failed because the extracted compiler could not locate the host GCC linker
plugin; adding the host GCC search directory corrected it. That failed build is
preserved separately. The repository's pinned native builder then succeeded.
Both executable hashes exactly match the historical receipts.

`/workspace/mirum-onboarding/install.sh` verifies retained native/source/package
identities and syncs the frozen Python lock. It refreshes a **retained workspace
snapshot**, not a bare main checkout. Missing retained artifacts require explicit
reconstruction with signed Debian packages and the pinned native builder. The
activation file is `/workspace/mirum-env.sh`. API startup needs
`python -m uvicorn --app-dir backend main:app` from the repository root; importing
`backend.main` without its backend import path failed and was corrected.
Provider-free HTTP checks use a process-local non-credential sentinel and do not
establish provider generation. Only test-owned API processes are stopped.

The environment draft's installation and startup fields are saved separately
from execution. Publication requires the user to review/save the settings and
publish through the product. `requires_publish=true` is not publication. Neither
publication nor genuinely new-task restoration of this prepared state has been
performed. Bundle integrity alone is not restoration evidence.

## Remaining engineering gates

A remains open: qualify uncovered actual foil/transition/control states and every
required initialization, trial and converged state without extrapolation or row
splicing. A passing sampled window cannot waive initialization sensitivity,
missing rows, panel or interpolation failure elsewhere. Qualify a declared
mission/envelope rather than treating solver defaults as a mission.

The family archives still lack revision-bound body/junction OML and justified
interaction/error models. Full force/moment equilibrium, nonlinear interactions
and stall sequencing require those models plus qualified section coverage.
A's slender circular-body adapter explicitly excludes junction/separation effects.
Published generic methods cannot supply revision-specific physical measurements.

Delivered mass/CG/full inertia, directional-control hardware, and required
maneuver/disturbance authority envelopes remain absent. Full aircraft derivatives,
dynamic modes and fixed-hardware trim/authority qualification remain unsupported.

B's available V2 construction is a flat, unswept, untwisted NACA 0012 prototype
with a 20 mm root cutback, collar/plates/rivets and local keel. It does not match
these families. Aerodynamic/material correspondence, all strip force/couple
components and load cases, complete fuselage paths, sizing and delivered closure
remain required. Core elasticity, peel/defects/contact and material/process coupon
or bench evidence remain absent; software fixtures cannot supply measurements.

Admission/search/comparison/archive/worker/API/UI guards remain closed. No gain,
physical construction, fabrication acceptance or certification is claimed.

## Covered-bank solver trial diagnosis

The retained `trace-covered-trials.py` driver uses only ascending grids from
fresh **passed and replayed** default-setting audits. It does not borrow any rows
from failed audits, use historical grids, or execute native jobs. Every scalar
bank request is retained in `solver-trials.json`, including rejected requests.

Both swept control states now initialize inside their fresh sampled cells, but
neither establishes a root. Neutral recorded 5,319 coefficient requests, including
119 rejected calls: the first outside request was Re 70,622.30909938432,
alpha 3.960215683522048 degrees, below the declared 4-degree bound. The +3-degree
state recorded 5,807 requests, including 108 rejected calls; its first rejection
was Re 73,028.50942643797, alpha 3.4709593121292173, outside both the neutral
foil's declared Re upper limit 73,000 and lower alpha limit 4 degrees. The
reported reason identifies the first failing bound. Newton damping eventually
reports "No improving covered root step; no equilibrium established" in both
states. No coefficient was evaluated outside its bank's cells.

This is a **failed solver attempt**, not converged flight coverage. The queried
trial values identify exact next qualification targets; they are not independently
qualified by the passing initialization audits. Widening a grid requires fresh
initialization/panel/midpoint audits and replay, not extending the old endpoints.
Canard/tandem attempts remain **unperformed** because their required neutral foil
lacks a passing fresh audit. Full-aircraft trim/equilibrium and converged-state
qualification remain unperformed for all families.

## Native timeout and replay limitation

The explicit-zero deflected canard/tandem audit completed four initialization
queries at Re 33,000, then its 240-panel ascending query hit the unchanged
20-second cap. Only four completed query reports are present; the raw failed
invocation's input, log and partial polar output are retained with identities.
This audit is failed, with midpoint and upper-Re checks unperformed. Independent
audit replay repeated the preceding completed queries but could not read the
missing refinement `query.json`; complete audit reconstruction therefore failed.
It must not be labelled a matched failure replay. The existing auditor supports
faithful incomplete-row replay, but reconstruction of interrupted native queries
needs a separately qualified implementation. No timeout or missing report was
waived, and no native-failure report was promoted.

## Sampled audit outcomes

| Foil (SHA256 prefix) | Re endpoints | Alpha grid (degrees) | Native default | Explicit zero |
| --- | --- | --- | --- | --- |
| `d8e3b2bda8` | [49000.0, 64000.0] | [4.0, 4.75, 5.5] | passed (replay matched) | passed (replay matched) |
| `e9426ae7ef` | [33000.0, 73000.0] | [4.0, 6.0, 8.0] | failed (replay matched) | failed (replay matched) |
| `e974ec3cea` | [49000.0, 73000.0] | [4.0, 5.0, 6.0] | passed (replay matched) | passed (replay matched) |
| `f925aa8eb7` | [33000.0, 61000.0] | [5.0, 6.5, 8.0] | failed (replay matched) | failed (timeout; replay unmatched) |

All seven completed audits made 11 queries each. The timed-out audit completed four queries before refinement stopped: 81 completed generation queries total. All retain failed/unsupported outcomes separately. See [the current receipt](../testing/cem-vaccel-initialization-results.json) for comparison diagnostics and identities. Zero acceleration did not establish a new passing canard/tandem window.

## Final current-instance validation

The exact timed-out 240-panel query was independently repeated **after** the
combined suite, with no competing native job. It again timed out at 20 seconds.
The foil bytes, commands, executable identity, failed log and partial polar
hashes all match the original failed invocation. This reproduces that specific
failure; it does **not** repair or promote the unmatched full-audit replay.
No timeout setting was changed. The zero-acceleration experiment is therefore
not a demonstrated coverage repair for this window.

The required combined regression passed **560 tests, no failures or skips**, with
two dependency deprecation warnings, in **770.83 seconds**. It ran outside the
restricted API socket namespace and after the experiment jobs ended. The earlier
focused suite passed 32 tests; the final combined run also includes the six
additional explicit/default audit parameter cases. No tests were disabled.

The API was started, stopped and restarted using the saved launch instructions.
Both final cycles returned metadata and computed feasibility with HTTP 200 and
`continue_search=true`. Each unqualified-family search returned HTTP 422 with
`Selected calibration, family or process is unavailable`. The final smoke checks
retain the response fields and exact rejection messages. Test-owned processes
were stopped; no live process is assumed to survive publication.

The frozen install repeat and compatibility check passed for 125 packages.
`install_script` and `start_skill` were saved in the configuration draft. The
final checkout revision/path is registered separately after commit delivery.
The saved install script verifies its retained setup manifest before using it.
No network-domain or credential requirement was added for this offline workflow.

Raw audits, solver traces, logs, frozen dependencies, signed-package/build
provenance, manual, native binaries and tested setup/smoke helpers are retained
in `/workspace/mirum-onboarding/vaccel-evidence.tar.gz`. The current receipt records
its digest; archive members are checked against the local files. This is local
workspace evidence, not remote publication. A Git bundle is retained separately
for the continuation; its integrity is not new-task restoration validation.

Next independent numerical work is to qualify the exact rejected swept trial
conditions recorded above, and repair low-Re canard/tandem missing-row and
interpolation coverage under explicitly justified settings. Full replay of
interrupted native audits also needs implementation and adversarial tests; an
independently reproduced single timeout does not supply that feature. Keep all
full-aircraft, construction and production gates closed pending their separate
model, measurement, load-path and control requirements. Environment publication,
new-task restoration, real provider generation and interactive browser rendering
remain **unperformed**.
