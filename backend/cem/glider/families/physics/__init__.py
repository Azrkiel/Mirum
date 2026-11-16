"""Revision-bound aircraft-physics machinery for Task C families.

Numerical implementation only. Nothing in this package qualifies an aircraft
family, grants production admission, establishes physical construction or
verified gain. Missing aerodynamic or hardware contributions are represented
as explicit ``unsupported`` records and are never replaced by zero.

Modules:

- ``frames``: axes, units and exact transformations (geometry/body/wind/earth).
- ``wrench``: force/moment contributions, reference-point transfer, ledgers.
- ``mass``: component mass/CG/full inertia tensors and parallel-axis transfer.
- ``trim``: bounded steady equilibrium with frozen geometry/hardware/mass.
- ``dynamics``: full-inertia rigid-body linearization, unsteady terms, modes.
- ``derivatives``: nondimensional stability/control derivatives, step study.
- ``stability``: explicit mode requirements and the unchanged family rule.
- ``authority``: physical control-surface topology, hardware, authority cases.
- ``envelope``: declared-envelope evaluation, provenance and replay.
- ``interfaces``: body/junction/control-hardware/hinge/authority input forms.
- ``assessment``: integration with actual family archives and evidence states.
- ``qualification``: per-revision qualification-input inventory.
- ``fixtures``: analytic and published-reference (747) equation checks.
"""

SCOPE = 'aircraft_physics_machinery_not_qualification'
