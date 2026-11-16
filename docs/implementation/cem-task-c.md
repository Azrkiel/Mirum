# Task C — New aircraft-family builders and admission

Task brief only; this branch does not implement the capability.
Implementation starting point: `cem-shape-comparisons` at `168dbae55390cf0ba449a7c278688dcd02817b97`.
See [the existing implementation notes](cem-shape-discovery.md).

**Relative effort:** Largest total scope when delivering all three initial families.

**Goal:** implement and individually qualify genuine tailless swept, canard and
tandem aircraft families, including their search, reconstruction and export paths.


**Work:**

- Give each family a separate geometry builder, parameterization, mass inventory,
  declared controls, revision-bound family identity and admission policy.
- For tailless swept aircraft, implement the actual tailless planform and relevant
  reflex/control choices, trim and static/dynamic stability checks. Do not invent
  a conventional tail or reuse conventional tail-volume thresholds.
- For canards, model foreplane/main-wing geometry and interaction, trim, stability
  and stall-sequence requirements. Missing interference or stall evidence cannot
  be counted as passing.
- For tandem aircraft, model two lifting wings and their lift sharing,
  interference, trim, stability and stall behavior. Relabelling a wing as a
  conventional tail is not an admission method.
- Use injected solver fixtures during development, then qualify aerodynamic
  methods for each family's geometry and interaction envelope. Extend A's methods
  where its initial existing-family qualification does not apply.
- Integrate each qualified family into registration, search, archive validation,
  exact reconstruction, exports and API/UI. Preserve separate capabilities for
  geometry, independent numerical performance and physical construction.
- Start with screening geometry. Admit physical construction for a family only
  after B's construction/load-path methods are shown applicable to that family,
  with additional family-specific structural work where necessary.

**Acceptance:** each family has real distinct geometry, positive reference cases,
untrimmable/unstable rejection cases, supported interaction/stall evidence,
revision-bound reconstruction and a native end-to-end search/comparison/design
flow. Existing-family regressions pass. Unsupported families remain unavailable.
Flying-plank, box-wing and blended-body families are not implicitly delivered by
these three; adding one requires another explicit family qualification cycle.

**Ownership/handoff:** three isolated family modules and admission policies,
family-specific solver/structural extensions where needed, registration/export
integration and separate qualification evidence for each family.

**Dependency:** builders and fixture-based checks can run alongside A and B.
Final numerical admission needs A's services qualified for that family's scope;
physical admission also needs the relevant B capabilities. Integrate families
individually rather than waiting to enable all three at once.

## Related task branches

- [A: Independent whole-aircraft aerodynamic verification](https://github.com/Azrkiel/Mirum/blob/cem-task-a-aerodynamic-verification/docs/implementation/cem-task-a.md)
- [B: Shaped-wing physical structure and delivered-part closure](https://github.com/Azrkiel/Mirum/blob/cem-task-b-physical-construction/docs/implementation/cem-task-b.md)

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
