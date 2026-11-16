# Task A — Independent whole-aircraft aerodynamic verification

Task brief only; this branch does not implement the capability.
Implementation starting point: `cem-shape-comparisons` at `168dbae55390cf0ba449a7c278688dcd02817b97`.
See [the existing implementation notes](cem-shape-discovery.md).

**Relative effort:** High; foundational and highest immediate priority.

**Goal:** obtain a revision-bound independent numerical performance comparison
for the existing shaped-wing families, with explicit rejection when the evidence
is insufficient.


**Work:**

- First establish versioned shared contracts for installed geometry, station
  polars, lifting-surface loads/downwash, body increments, structural load cases
  and family builders. Specify SI units, geometry/solver axes, moment signs,
  nondimensional rates, symmetry, reference quantities and CG. Include small
  deterministic fixtures so B and C can start without waiting for native solvers.
- Bind requests/results to geometry, hardware/placement, mission, process,
  calibration, operating conditions and implementation revisions. Native evidence
  must retain executable and raw-artifact identities. Reject missing, stale or
  nonfinite evidence.
- Reproduce and diagnose XFOIL's current SIGFPE with the exact native build,
  airfoil, Reynolds number and command sequence. Distinguish runtime defects
  from genuine nonconvergence. Qualify bounded station-polar queries, transition
  settings, interpolation coverage and unsupported conditions.
- Qualify AVL surface export, camber/twist/symmetry, loads, local operating
  conditions, derivatives, axis conversions and numerical convergence. Check
  force/moment conservation. Investigate the existing spiral-stability rejection;
  a valid rejection must remain a rejection.
- Implement independent fuselage/body drag and moment increments with explicit
  applicability and interference limitations. Reusing the primary `_aero`
  calculation does not establish independent confirmation.
- Couple effective station angles, local velocity/Reynolds number, polars,
  downwash and airframe increments. Solve equilibrium and distance/duration with
  geometry, incidence, ballast, payload position and installed rod fixed. Avoid
  double-counting induced, profile and body drag.
- Integrate with paired comparisons, parent evidence validation and API/UI.
  Define the scope and numerical/error criterion of an independent gain claim;
  successful executable diagnostics alone cannot enable `verified_gain`.

**Acceptance:** documented native reference cases, stable/unstable fixtures,
polar coverage, force/moment closure and mesh/solver convergence pass within
stated tolerances. Both frozen-hardware arms must pass admission before a gain
can be considered, and the difference must exceed the declared error criterion.
Missing coverage, unsupported conditions and native failures produce no verified
gain. Existing screening behavior remains available with explicit limitations.
Numerical verification is not empirical flight validation.

**Ownership/handoff:** new aerodynamic adapters, contract and performance modules,
the shared orchestration hooks, validated load outputs, qualified envelopes,
reproducible native setup and end-to-end comparison evidence.

## Related task branches

- [B: Shaped-wing physical structure and delivered-part closure](https://github.com/Azrkiel/Mirum/blob/cem-task-b-physical-construction/docs/implementation/cem-task-b.md)
- [C: New aircraft-family builders and admission](https://github.com/Azrkiel/Mirum/blob/cem-task-c-aircraft-families/docs/implementation/cem-task-c.md)

## Shared coordination and completion


A owns the first shared-contract merge. B and C supply their structural and family
requirements to that contract and may prototype immediately against the written
interfaces. Keep module edits isolated during parallel work. Merge shared-file
changes sequentially, including `discovery.py`, `comparison.py`, `model.py`,
`service.py`, `requirements.py`, `cem/evidence.py`, `cem/api.py`, the worker,
provenance and the UI. Each task owns its integration tests and handoff; there is
no separate fourth integration task.

Prioritize A because its evidence unlocks final qualification for B and C. Expect
C to require the most total implementation/qualification work across three
families, and B to carry the heaviest physical load-path/manufacturing-validation
burden. Reassess effort after the shared interfaces and native solver diagnoses.

Deployment, environment publication and fresh-task restoration remain release
acceptance work for whichever task is selected for delivery. They were not
performed in the current implementation verification and do not constitute a
fourth engineering task in this breakdown.
