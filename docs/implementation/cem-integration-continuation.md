# Integration continuation from dcb1592

This run starts at `dcb15927542cc28c4262b018df1a70ce74c403cf` on
`cem-integration-verification`, using the existing `/workspace/Mirum` checkout.
No worktree was created. Production gates remain closed.

## Upstream inspection and integration

Fetched both branches before integrating. A remains at
`6b4df7a38a5e5eaf700686ff21fde9acd62683d3`; its sole unintegrated commit
adds acceptance documentation. That documentation is incorporated here, preserving
the receiving-branch integration note. It adds no solver or qualification evidence.
B remains at `5f5fc120163e18d37daf765e5956635a63bb1911`, already an ancestor.
No newer B implementation is available through its remote branch. Neither
upstream branch was modified; unpublished work in other tasks remains unknown.

## Flight-condition coverage diagnosis

`scripts/cem/diagnose_window_coverage.py` reconstructs every installed archive
and exact neutral/+3 degree control state in the historical window receipt.
It rejects changed installed identities. It uses the same panel geometry,
atmosphere, span projection and local-angle definition as the coupled solver,
at its default speed 6 m/s, alpha 4 degrees, altitude 0 m and spanwise mesh 4.
It evaluates **zero-circulation initialization only**, without synthetic polar
coefficients, extrapolation or a purported converged aircraft solution.

| Family (both retained pitch states) | Initial strips outside historical passing bounds | Local Re | Local alpha, degrees | Re-only common-speed lower / upper, m/s |
| --- | --- | --- | --- | --- |
| Swept | 24 / 24 | 49,029–72,802 | 4.069–5.898 | 12.238 / 9.890 |
| Canard | 56 / 56 | 33,168–72,803 | 4.069–7.989 | 36.180 / 18.131 |
| Tandem | 56 / 56 | 40,857–64,714 | 4.069–6.902 | 29.371 / 20.398 |

Every lower speed bound exceeds its upper bound: at this alpha, there is no
single speed whose **initial** strip Reynolds numbers all fit those historical
windows. These intersections are evaluated only when every strip has exactly
one matching historical passing window; disjoint windows are never bridged.
Changing speed alone therefore does not repair this initialization-domain gap.
Changing alpha requires another calculation. A covered warm start could change
the initialization, but would itself require demonstrated coverage; it is not
supplied here. The solver defaults are a diagnostic target, not a declared flight
mission or required control-authority envelope.

### Part A and published-reference follow-up

Part A was searched directly at the fetched revision, including its four frozen
reference JSON files, body adapter, requirements, AVL adapter and technical record.
`backend/cem/data/aero-conventional-reference-v1.json` supplies a usable numerical
development target: 10 m drop, still air, sea level, 15 C, maximum speed 15 m/s,
span 1.4 m, length 0.95 m and mass 0.5 kg. Its static-margin limits are 0.05–0.20,
minimum Dutch-roll damping 0.05 and minimum spiral doubling time 8 s.
The extracted target is retained in
`docs/testing/cem-family-development-envelope.json`. These are reference-case
requirements, not family flight validation or maneuver/disturbance authority
requirements. Family geometry and hardware must remain their own fixed revisions.

At A's reference initial guess of **14 m/s and alpha 0 degrees**, the diagnostic
still finds 20/24 swept strips and 56/56 canard/tandem strips outside the historical
passing bounds, for each retained pitch state. Local Re ranges are respectively
114,401–169,876, 77,393–169,876 and 95,334–151,001. Local alpha ranges are
0.075–1.908, 0.075–3.994 and 0.075–2.908 degrees. Four swept strips being inside
historical bounds does not supply fresh replay or a covered aircraft solution.
This checks another initialization point, not the benchmark's converged flight
state, which belongs to a different conventional aircraft.

A's reference body stations, 0.312 kg mass and diagonal inertias are explicitly
prescribed rigid numerical inputs. They are not measured construction/inertia
records for the three families. Its existing body adapter supports slender
circular bodies only, and explicitly excludes junction flow, shielding and
separation; its drag allowance is not a flight confidence interval. No revision-
bound family body/junction OML, delivered construction measurements or required
control-moment envelope was found on A.

The official MIT AVL documentation request was denied by the environment proxy
(CONNECT 403). The published manual was instead retrieved successfully from the
[pinned GitHub mirror](https://github.com/RobotLocomotion/avl/blob/b67837fa737afdf7a56655b044242619246b3ada/avl_doc.txt),
SHA256 `c718b8b099354ce1c66a8d91d18f0cb970ed6a8546ed27c9baaf438a6eb278e0`.
Its configuration guidance describes thin lifting surfaces at small alpha/beta
and warns that body modeling experience is limited. Its mass-file section
requires component positions and inertias for dynamics, including products of
inertia when applicable. These published methods help define the next analysis;
they cannot provide this aircraft's measured assembly properties or establish
its nonlinear/stall envelope. No published generic value was substituted for
missing revision-specific evidence.

The XFOIL manual in the checksum-verified
[published Debian source archive](https://deb.debian.org/debian/pool/main/x/xfoil/xfoil_6.99.dfsg+1.orig.tar.gz)
also identifies low-Re separation-bubble resolution and solver acceleration as
convergence concerns. Its acceleration section recommends investigating lower
`VACCEL` below Re 100,000, and says that parameter does not affect the final
converged solution if attained. This is a concrete future numerical experiment,
not evidence that these missing rows converge: any added control must be explicit
in query inputs, retained commands, source identities and independent replay,
with fresh initialization/panel/midpoint audits and the unchanged 20-second cap.
No `VACCEL`, panel distribution, transition or timeout setting was changed here.

The receipt's four sampled passes and two failed trial windows are historical.
The old `/workspace/mirum-onboarding/coordinate-window-evidence.tar.gz` and prior
environment activation file were absent on arrival. Their loss prevents replay
of those retained raw audits here. The old receipt is unchanged and no new
section-window qualification is claimed. Current source/binary identities and
new diagnostic output are recorded separately. Source-bound evidence must be
regenerated when its recorded identities change.

Reproduce, without another native workload running:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python scripts/cem/diagnose_window_coverage.py \
  docs/testing/cem-coordinate-window-results.json \
  --output /tmp/new-initialization-coverage.json
python -m pytest backend/tests/test_cem_window_coverage.py -q
```

The output path must be new. A successful diagnostic command reports domain
membership only. It neither replays historical audits nor grants admission.

## Missing implementation and external evidence

- **A/section numerics:** qualify exact installed foils at actual local Re/alpha
  and every required control state, initialization/trial point and converged
  state. Include independent initialization, panel and midpoint checks at the
  existing tolerances. Low-Re and near-zero-alpha failures remain unresolved.
  Do not splice grids or reinterpret the high-Re positive-angle window as
  low-Re coverage. A's paired-comparison/body-error acceptance remains open.
- **C/aircraft model:** archives provide lifting OML and screening inventory,
  not revision-bound body/junction OML or justified interaction/error models.
  Apply A's limited body model only where its scope is demonstrated, supply
  junction models and references, then close all force/moment components,
  nonlinear interaction and family-specific stall order over a declared mission.
- **C/dynamics and controls:** obtain delivered mass/CG/full inertia and actual
  directional-control hardware, with maneuver/disturbance authority requirements.
  Fixed-revision trim, full derivatives, dynamic modes and authority envelopes
  remain unsupported by current lifting-only static derivatives.
- **B/construction:** the available V2 prototype is a flat unswept, untwisted
  NACA 0012 wing with a 20 mm root cutback, local keel and unresolved mechanisms.
  It does not match these family geometries. Implement geometry correspondence,
  full attachment/fuselage load paths, all shared strip-force/couple components,
  load-case coverage and structural sizing. Deliver fresh measured mass/CG/full
  inertia and fixed-hardware closure after every sizing change. Core elasticity,
  bond peel/defects, contact compliance and material/process coupon or bench
  evidence cannot be supplied by software fixture tests.
- **Production:** admission/search/comparison/archive/worker/API/UI must continue
  rejecting these families until the corresponding evidence can be independently
  reconstructed. Passing software/native references do not waive these gaps.

## Current-instance and release status

See `docs/testing/cem-integration-continuation-results.json` for current checks,
identities and retained evidence. The rebuilt XFOIL and AVL executable hashes
match the historical receipt exactly. Python's numerical packages match its
recorded versions; other dependencies are frozen in a new retained lock, rather
than being assumed identical. The runtime was reconstructed from scratch here.

The saved installation instructions rely on retained, hash-checked workspace
packages, the frozen dependency lock and native build/source artifacts. Startup
instructions activate the environment and restart the API; live processes are
not restoration evidence. API checks use a process-local non-credential sentinel
only for provider-free routes. Provider generation and interactive browser
rendering remain unperformed.

The configuration draft in this task initially had null install/start fields
and selected `main`, despite the prior handoff's saved draft. Saving this task's
configuration is distinct from publication. Review/save the environment settings
and publish through the product. A genuinely new task must then verify the
checkout/retained artifacts, activation, native hashes, dependency compatibility,
service restart and functional requests. This task cannot publish through the
available tools or validate its own restoration as a new task.

The first combined run was interrupted after its progress stopped at API checks
inside the sandbox namespace. It is an incomplete run, not a test failure or a
pass. The seven API tests then passed outside that namespace. The complete
combined regression was restarted there with no competing native job. Native
timeouts and assertions were unchanged. The current receipt records that run's
actual final outcome separately from the interrupted log.

The complete rerun passed **546 tests, no failures or skips**, with two dependency
deprecation warnings, in **800.14 seconds**. The final diagnostic suite separately
passed five tests. Actual HTTP checks returned 200 for metadata and computed
feasibility, and 422 for each unqualified-family production search, including
after an API restart. Repeat execution of the exact saved install script passed;
all 101 frozen packages are compatible and native hashes match the old receipt.

New local evidence is retained at
`/workspace/mirum-onboarding/continuation-evidence.tar.gz`; its digest and contents'
identities are in the new checked-in receipt. It contains current logs, both
initialization diagnostics, published references, the frozen lock, saved install
script and native binaries/build manifest. It does not contain the missing old
coordinate audit directories and does not replace their replay.

An intermediate remote recheck failed GitHub authentication. The subsequent
normal push succeeded: implementation/evidence commit
`0dc973b929358a6f68427289fd42e65013532188` is on
`cem-integration-verification`. A successful post-push remote check confirmed
that A and B still have the exact tips recorded above. No authentication action
is currently required. This documentation-only follow-up records delivery;
it does not change the tested implementation or raw evidence.

The environment draft saves `install_script`, `start_skill` and the verified
repository commit/path. Review/save those settings and publish the environment
through the product, then perform restoration checks in a genuinely new task.
Neither publication nor fresh-task restoration was performed here. A locally
verified Git bundle at `/workspace/mirum-onboarding/continuation.bundle` also
retains this continuation relative to the starting commit; bundle verification
is not environment restoration validation.
