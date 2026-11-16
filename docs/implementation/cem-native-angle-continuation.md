# Native-angle coverage continuation

> **Historical record — superseded scope (2026-11-05).** The results here are
> unchanged and remain valid evidence of what was run. The
> [low-Re method decision](cem-low-re-method-decision.md) now governs section
> work: no section below Re 50,000 is admissible, and further XFOIL tuning below
> that floor (panel distribution, VACCEL, initialization) is closed. Use
> [cem-remaining-section-work.md](cem-remaining-section-work.md) for current tasks.

Follow-up: [explicit panel-distribution qualification](cem-panel-distribution-continuation.md).
Related machinery: [aircraft physics](cem-aircraft-physics.md). Neither follow-up
qualifies the missing aircraft or construction evidence. The native-angle
results below remain the historical run.

Continues `127c8cfaa6cab5208e5eb167471154fdcb31b0fc` on
`cem-integration-verification`, using the existing isolated checkout. No worktree
was created. Production admission/search/comparison/archive/worker/API/UI gates
remain closed. The prior [interrupted-audit handoff](cem-interrupted-audit-continuation.md)
and its receipt remain historical records, including **567 passed, 1 failed**.

## Published inputs and runtime

A remains at `6b4df7a38a5e5eaf700686ff21fde9acd62683d3`; its only unmerged
commit is acceptance documentation already incorporated in the receiving handoff.
B remains at `5f5fc120163e18d37daf765e5956635a63bb1911`, an ancestor. No new
published implementation was available and neither branch was modified.

The incoming checkout was main's `36b996f` on local branch `work`. The requested
branch was fetched and selected at the exact starting commit. No applicable
AGENTS.md, prior activation file, runtime, interrupted-evidence archive or bundle
was present. No historical raw-audit replay or retained-receipt verification is
claimed for those missing files.

The workspace-local Python 3.12.14 runtime now has 118 frozen compatible packages.
Signed official Debian indexes and verified downloads supplied local Fortran,
CMake and X11 build dependencies. The repository's checksum-pinned native sources,
headless patch, compiler flags, native 20-second limit and 1 MiB artifact caps
were preserved. Missing parent directories and the relocated compiler's linker
plugin lookup were corrected before successful linking; existing compilation
outputs were reused. Both new native executable hashes differ from the historical
receipt. Their outputs require fresh evidence.

All six freshly reconstructed installed-state hashes and all corresponding foil
hashes match `cem-coordinate-window-results.json` exactly in this runtime. The
strict historical identity assertion was not changed. This establishes matching
regenerated payload identities, not recovery of absent historical raw outputs.
The previous runtime's byte drift remains unexplained: it has the same recorded
numerical package versions, but lacks sufficient retained binary/runtime artifacts
for a causal comparison. Current package lock, platform, NumPy shared-object
identity and native build manifest are retained separately.

## Native angle representation

Previously `CoordinateBank` interpolated using requested angles even though
XFOIL rounds the actual sampled angles. A higher-precision request could relabel
coefficients and let `np.interp` clamp outside the actual sampled endpoint.
The bank now interpolates at the native row angles and requires membership in
both the declared and sampled domains. Neither domain is extended. Duplicate,
reversed or inconsistent native row grids are rejected. Positive-slope and
Reynolds coverage rules and all numerical tolerances remain unchanged.

Four new tests first failed against the old behavior and passed after the fix.
The focused software run passed 21 tests, with three native cases deferred to
the complete combined regression. The required complete combined run then passed
**572 tests, no failures or skips**, with two dependency warnings in **731.84 s**.
The historical identity assertion passed unchanged on this runtime. Analytic fixtures test interpolation and
rejection only; they do not qualify native or physical performance.

`scripts/cem/qualify_coordinate_rounding.py` freezes fresh installed states and
writes its complete experiment declaration before native execution. It audits
separate coarse and refined deflected grids, independently replays each whole
audit, and checks real high-precision endpoint requests against rounded native
rows. Failed grids are never spliced or admitted. Follow the current testing
receipt for actual results; successful driver execution alone is not qualification.

## Aircraft and construction limits

A section audit or circulation root does not establish full force/moment
equilibrium. Revision-specific body/junction OML and interference/separation
models, nonlinear interaction and stall sequencing remain unsupported. Full
assembly mass/CG/inertia including products of inertia, actual hinge/directional
hardware and maneuver/disturbance authority requirements remain missing.

B's V2 flat unswept/untwisted NACA 0012 prototype with 20 mm root cutbacks and a
local keel does not represent these families. Aerodynamic/material geometry
correspondence, complete attachment/fuselage load paths, all strip force/couple
components and load cases, revision-bound sizing/export/measurement and
fixed-hardware trim remain required. Core/bond/contact behavior, material/process
coupons and bench measurements cannot be supplied by software fixtures or generic
literature values. All production and verified-gain flags remain false.

## Reproduce

Use this existing isolated checkout; do not create a worktree. Activate
`source /workspace/mirum-env.sh` in each shell. Run native jobs sequentially,
without a competing regression or qualification process:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python -m pytest backend/tests/test_cem* backend/tests/test_glider* \
  frontend/test_cem_ui.py analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
python scripts/cem/qualify_coordinate_rounding.py --directory /workspace/new-angle-audits
```

The retained setup refreshes an installed snapshot, not a bare checkout. Its
manifest, frozen lock, activation and native binaries must pass integrity checks
before reuse. The provider-free smoke helper starts/stops/restarts the actual API,
checks metadata and computed feasibility, and verifies HTTP 422 for all three
unqualified-family searches. Its process-local sentinel is not a provider
credential; real provider generation and interactive rendering remain unperformed.
Publication and validation in a genuinely new task remain separate, unperformed
release actions. See the current receipt for saved configuration and evidence.

A's low-Re reference interpolation failure and the real paired-arm spiral
rejection remain qualification limits. Passing their regression countercases
preserves rejection; it does not establish a stable real paired comparison.

## Fresh numerical results

The coarse deflected Re 49,000/64,000, alpha 2.5/4/5.5 audit failed and replayed:
Cl error 0.0508 and Cm error 0.009325 at Re 56,000, alpha 3.25 exceed 0.025
and 0.005. A fresh Re 49,000/56,000/64,000 grid with alpha 2.5 through 5.5
by 0.75 reduced Cl error to 0.0232 but still failed Cm at 0.005125. Its whole
failed audit replay matched. Neither supplied a bank.

A subsequent five-Re, 0.375-degree attempt executed 29 queries but exceeded the
unchanged 150-artifact inventory cap (174 files, including native auxiliary BL
files). It aborted before producing a full audit receipt or replay. Its partial
outputs supplied no bank. The cap was not raised and files were not deleted to
force admission. A **new** bounded three-Re grid, 49,000/56,000/64,000, alpha
2.5 through 5.5 by 0.375, then passed and independently replayed. Maximum midpoint
errors were Cl 0.0142783, Cd 0.00029536 and Cm 0.00261653. Its 17 queries and
all settings are retained; it is not a splice of the earlier grids.

Fresh neutral Re 49,000/74,000, alpha 3/4.5/6 also passed and replayed. Using
only these two complete passed/replayed banks, the +3-degree pitch-channel state
at 6 m/s and 4 degrees reached a circulation root in three iterations, residual
7.46865e-12, with 1,848 traced requests and no rejected domain trials. Final
strips span Re 49,037.60–73,027.89 and local alpha 3.18954–4.55969 degrees.
A fresh standard-bank report was independently replayed in full. Its scientific
payload matches the instrumented trace report; the latter has a distinct,
explicit instrumentation-source fingerprint. No source identity was rewritten.

Lifting-only force is approximately [-0.01939, 0, 1.90072] N and pitch moment
is **-0.0433757 Nm**. Required body/other contributions are absent. This is
neither aircraft equilibrium nor a native qualification at every converged strip.
No dynamic, stall, control-authority or physical-construction admission follows.

The direct high-precision endpoint experiment requested alpha
3.02376103536733; XFOIL emitted 3.024. Both native queries independently replayed.
The bank correctly rejected the requested lower endpoint outside native samples.
This does not promote earlier failed narrow audits.

## Low-Re bubble diagnostics

The checksum-verified manual is SHA256
`c36482e92f6cab424e965ccb48d0d81ebc030204ebf44a4205b366b9d7db3bea`.
Its panel-density guidance recommends post-transition Hk drops no greater than
1 per point and Ue/Vinf changes no greater than 0.05. Verified `xoper.f` shows
that BLDUMP's column labelled H actually writes HK. Retained helpers bind that
source, manual, exact foil, commands, native executable, dumps and independent
repeat identities. No new scientific bank was constructed from these diagnostics.

Neutral canard/tandem foil queries at Re 33,000, alpha 6/8 degrees, 160/240
panels, default acceleration, Ncrit 9 and trips 1/1 were explicitly declared.
Each used ALFA 0 then the target; all four returned target polar rows and repeated
exactly, including their dumps. This differs from the historical ascending sweep
through intermediate angles and does **not** repair its missing alpha-8 row.

Default maximum upper-surface post-transition Hk drops at 160/240 panels were
5.011/3.7999 at alpha 6 and 2.9611/2.2578 at alpha 8, exceeding the guideline.
The violating intervals lay inside x/c 0.41148–0.75980. A separately declared
experiment therefore used `PPAR R 1` and `XT 0.40 0.77` at both panel counts,
keeping the foil, transition, acceleration, targets and limits fixed. All four
new queries and dumps independently repeated. Alpha-8 Hk drops fell to
0.7978/0.5603, but alpha-6 drops remained 2.6621/1.6512. Ue changes were below
0.05 in these eligible upper-surface pairs. There were no eligible lower-surface
post-transition pairs; that is unassessed, not a passing lower-surface check.

This is a justified panel-distribution diagnostic, **not** a numerical or physical
coverage repair. Full initialization/panel/midpoint audits with explicitly
integrated/bound panel settings remain required before such rows can supply a
bank. Historical low-Re interpolation and missing-row failures remain failed.

## Retention and environment configuration

The new testing receipt records the exact local evidence archive, verified member
count, helper/source/runtime identities and API restart results. Retention is
local workspace only. Both `install_script` and `start_skill` were saved; the
exact saved installation script was read back and executed successfully. The
repository selector is updated to the delivered commit after committing. Review
and save environment settings, then publish to snapshot the prepared instance.
Neither draft saving, Git delivery nor local archive verification establishes
publication or successful restoration in a genuinely new task.
