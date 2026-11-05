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
- ``dynamics``: finite-difference derivatives and full-inertia rigid-body modes.
- ``interfaces``: body/junction/control-hardware/authority input contracts.
- ``assessment``: integration with actual family archives and evidence states.
"""

SCOPE = 'aircraft_physics_machinery_not_qualification'
