# Whole-aircraft aerodynamic qualification: continuation status

> **Historical record — superseded scope (2026-11-05).** The results here are
> unchanged and remain valid evidence of what was run. The
> [low-Re method decision](cem-low-re-method-decision.md) now governs section
> work: no section below Re 50,000 is admissible, and further XFOIL tuning below
> that floor (panel distribution, VACCEL, initialization) is closed. Use
> [cem-remaining-section-work.md](cem-remaining-section-work.md) for current tasks.

This continuation extends the native development workflow after the
[control-response handoff](cem-task-c-control-response.md). **Whole-aircraft
qualification is incomplete.** It does not enable a production family.

Subsequent shared integration: [A into C handoff](cem-a-c-integration.md).
A's covered solver, reference cases and audits are now available on C; adapting
them to the coordinate-family path remains explicit follow-up work.

## Implemented and exercised

`families/coordinate_polars.py` supplies bounded native XFOIL queries for exact
installed airfoil coordinates. Reflex and deflected controls are not replaced
by NACA names. Reports retain coordinates, transition, Reynolds number, explicit
requested angles, converged rows, missing angles, panel count, binary identity
and input/output/log hashes. Incomplete rows are preserved as incomplete results.
They cannot enter a `CoordinateBank`; interpolation cannot bridge missing rows,
extrapolate in angle/Reynolds number, or use nonpositive-slope polar cells.

Validation checks the complete raw artifact set and repeats the native query.
Changing scientific values or omitting a raw artifact fails validation. Native
log timing text can vary between executions; retained bytes are hash checked,
and repeated scientific output is compared separately.

`families/coupled_aero.py` implements a separate development-only nonlinear
horseshoe lifting-line calculation. It retains both physical halves, actual
section polars, incidence, twist, controls and fixed screening CG. Every installed
lifting member participates in one circulation solution. It reports local
effective angle, velocity, Reynolds number, section coefficients, forces,
quarter-chord section couples, moments about CG and profile/induced drag.
Source-resolved induced velocities sum back to the total. For canard and tandem,
both ordered interaction directions are explicitly retained at every strip.

`static_derivatives` perturbs aircraft alpha at fixed speed, hardware and controls.
It reports lifting-only CL-alpha, Cm-alpha and their static-margin ratio per
radian. The full lower/upper calculations are retained. No hardware is relocated
and no control is retrimmed to rescue a design. This is not a dynamic mode model.
The upper-angle solve starts from the lower-angle converged circulation. This
avoids unsupported zero-circulation initialization at an otherwise covered
coupled solution; every nonlinear residual still rejects uncovered polar queries.

The new path does not alter production `mirum-aero-1`, the existing solver's
family restriction, or `admission.Evidence`'s fixture-only source restriction.
Every report keeps production and qualification capabilities false.

## Reproduction

Use the existing `/workspace/Mirum` checkout; do not create a worktree implicitly.
Activate `/workspace/mirum-env.sh`. No new Python dependencies are needed.
Native XFOIL from that activation is required for the new tests:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python -m pytest backend/tests/test_cem_family_coordinate_polars.py \
  backend/tests/test_cem_family_coupled_aero.py -q
```

From Python, reconstruct a family archive and explicit controls, then obtain
the exact foils from `installed.validate(installed.freeze(archive, controls))`.
For each distinct foil, call `coordinate_polars.query` at two or more bracketing
Reynolds numbers in separate empty directories. Only complete compatible grids
may be passed to `CoordinateBank`. Call `coupled_aero.solve` with that bank,
the original archive and controls; run two or more resolutions. Validate the
retained polar artifacts with `coordinate_polars.validate`, then recompute the
coupled calculation with `coupled_aero.replay`. Both validators return `False`
for production admission even when the development evidence reconstructs.

Raw current-instance data is at `/workspace/mirum-coupled-development/<family>/`.
It includes exact archives, controls, native polar identities and artifacts,
two-mesh loads and fixed-alpha derivative calculations. The computational
summary is tracked in
[`../testing/cem-family-coupled-development.json`](../testing/cem-family-coupled-development.json).
These are computational examples, not measured reference aircraft. The retained
runner is `/workspace/mirum-run-coupled-development.py`; when running that file
from the checkout, use `PYTHONPATH=backend:.` so both backend and analysis modules
resolve. Its output root must not already exist.

## Gates that remain open

| Requested capability | Present evidence | Still required for qualification |
| --- | --- | --- |
| Drag | Native section profile drag plus coupled induced drag | Installed body OML, body/junction/separation effects, total-aircraft trim and reference drag validation |
| Interaction | Source-resolved attached-flow velocities and angle increments in both directions | Family-scoped validation and nonlinear interaction across the required envelope |
| Stall | Missing rows and nonpositive-slope queries reject; no stall onset is invented | Qualified root/tip local envelopes, nonlinear aircraft stall sequence and retained control response |
| Stability | Fixed-alpha lifting-only derivatives; existing inviscid response diagnostics | Body/directional derivatives, full inertia, full-aircraft equilibrium, dynamic eigenmodes and stable/unstable references |
| Control authority | Exact deflected foil coordinates and native response calculations | Required moments and envelope, actual yaw strategy, saturation/stall interaction and independent authority reference cases |

`whole_aircraft_drag_n` and `body_drag_n` are explicitly `None`; no body is
invented from the existing lumped body mass. A positive polar slope is only a
restriction on the attached-flow calculation. It does not establish a stall
boundary, front-first stall, static/dynamic aircraft stability or usable control
authority. No new performance gain or physical construction claim is made.

## Reference search and access blocker

Firecrawl is not installed as a tool, and neither configured bindings nor the
running machine provide `FIRECRAWL_API_KEY`. A direct NASA NTRS request through
the configured proxy was denied with HTTP CONNECT 403. Draft settings now request
`api.firecrawl.dev`, `ntrs.nasa.gov` and `api.openalex.org`, plus secure entry of
`FIRECRAWL_API_KEY` for `api.firecrawl.dev`. Existing presets/settings are
preserved. Saving the draft does not apply network access or supply the key.
Review/save in environment settings and publish if prompted. Credentials must
be entered there, never in chat. Firecrawl research has not been executed.

Public GitHub repository search was available and returned these inspected leads:

| Source | Inspection finding |
| --- | --- |
| [Canard thesis appendix](https://github.com/ohernando12-lab/Bachelor-Thesis) | README describes sanitized templates and removal of case-specific raw values; insufficient to reproduce a validation aircraft |
| [Tandem state-space model](https://github.com/elanchezhian/TandemWIngStateSpaceModel) | README identifies XFLR5 coefficients and MATLAB linearization; possible numerical comparison lead, not measured qualification |
| [Weissinger examples](https://github.com/lucapiombo/3D-CFD-with-Weissinger-method) | README describes an educational panel implementation compared with XFLR5; not measured aircraft evidence |
| [TandemWings manuscript](https://github.com/garranz/TandemWings) | The inspected main.tex has empty methodology/results/conclusions; not a usable validation package |

Search queries, result counts, inspected source hashes and dispositions are in
[`../testing/cem-family-reference-search.json`](../testing/cem-family-reference-search.json).
This limited search does not establish that suitable datasets do not exist.
Do not treat repository descriptions, templates or solver-generated state-space
matrices as independently validated physical references. Resume primary-source
research once access is applied; establish geometry, Reynolds/Mach/control
envelope, mass/CG/full inertia, raw results, uncertainties and reuse permission
before accepting a dataset.

## Validation and computational outcomes

- Combined CEM/glider/UI/AVL/XFOIL regression: **499 passed, no skips**, two
  dependency deprecation warnings, 356.49 seconds. This preceded the final
  adjacent-angle warm-start correction; the final affected tests cover that
  correction and its native canard regression separately.
- Final affected coordinate-polar/coupled-model tests: **10 passed, no skips**.
- Native coordinate polars, exact-coordinate coupled loads, fixed-alpha
  derivatives and parent replay executed for all three families. All requested
  neutral grids in these runs completed, using Re 30,000/300,000, alpha
  -4/-2/0/2/4/6/8 degrees, meshes 2/4, speed 6 m/s and aircraft alpha 4 degrees.
- The fixed-state lifting-only static-margin ratios were approximately
  0.132/0.150 (swept), 0.080/0.082 (canard), and **-0.355/-0.449 (tandem)**.
  The negative tandem result is retained; no CG or hardware was changed to make
  it positive. Positive ratios do not establish full-aircraft trim or stability.
- Independent native polar reconstruction and coupled replay passed for all
  three computational cases after the final source changes. Replaying a report
  made with an older model source requires regeneration; it is not silently
  accepted as current evidence.
- `git diff --check` passed. Validation was recorded before commit and push;
  see Git history for subsequent repository publication. No deployment or
  environment publication was performed. Earlier continuation work is preserved.
