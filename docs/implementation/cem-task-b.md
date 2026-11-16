# Task B — Shaped-wing physical structure and delivered-part closure

Task brief only; this branch does not implement the capability.
Implementation starting point: `cem-shape-comparisons` at `168dbae55390cf0ba449a7c278688dcd02817b97`.
See [the existing implementation notes](cem-shape-discovery.md).

**Relative effort:** Very high; most demanding physical engineering and measurement work.

**Goal:** qualify one explicitly named physical construction variant for the
multi-station planform, from installed load path to measured exported geometry.


**Work:**

- Consume the shared load-case and installed-material/support contracts. Use
  analytical load fixtures while A's validated aerodynamic outputs are pending.
- Model actual wing-to-spar, joint and fuselage-attachment load transfer rather
  than assuming ideal elliptical total lift. Declare supported bending, shear,
  torsion, stiffness and failure checks; unsupported mechanisms remain explicit.
- Build the chosen construction's stock, spar channels, joints and attachments.
  Define an exact material inventory and structural interfaces for the shaped wing.
- Re-import delivered exports and independently check material laws, connectivity,
  enclosure, mass, CG and inertia. Reject missing/disconnected parts, invalid
  channels and mismatched revisions.
- Integrate validated shape-specific loads, measured properties and structural
  analysis with sizing and delivered-geometry closure. Re-run trim/stability for
  the delivered revision. Geometry corrections require new exports and measurements.
- Admit only the named construction mode through requirements, discovery, parent
  validation and UI. Each additional construction variant needs its own evidence
  cycle within this task's scope; one passing variant cannot enable all modes.

**Acceptance:** analytical beam/joint references, reaction equilibrium and
convergence pass. Real installed geometry and delivered measurements agree
within declared tolerances. Overloads, missing supports, disconnected load paths
and incomplete physical closure remain rejected. Final qualification uses A's
validated loads for the actual shape and operating conditions. Software screening
alone does not establish fabrication acceptance or engineering certification.

**Ownership/handoff:** structural analysis and construction modules, measured
inventory/interfaces, closure integration, one qualified construction envelope
and end-to-end evidence including rejected cases.

**Dependency:** implementation and fixture-based mechanics/geometry tests can run
in parallel with A. Final aerodynamic-load and delivered-trim qualification cannot.

## Related task branches

- [A: Independent whole-aircraft aerodynamic verification](https://github.com/Azrkiel/Mirum/blob/cem-task-a-aerodynamic-verification/docs/implementation/cem-task-a.md)
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
