"""Propulsion (Domain F) — axisymmetric bodies of revolution.

After the rocket → aerospace re-scope (commits c522e9c..e23de28, May 2026),
Domain F covers converging-diverging nozzles, combustion chambers, nacelle /
inlet cowls, and afterburner ducts. Geometry is produced by the deterministic
revolve-first helpers in ``cad_utils`` (``make_nozzle_profile`` /
``make_combustion_chamber`` / ``make_nacelle_cowl``); this package owns the
shared *spec* surface used across the pipeline:

* ``specs.PropulsionSpec``              — canonical Pydantic spec (imported by ``schemas``)
* ``rea_schema.PROPULSION_SPEC_SCHEMA`` — Gemini ``response_schema`` for the REA branch
* ``rea_schema.mentions_propulsion``    — deterministic Domain-F routing for the classifier
* ``validation.validate_all``           — physics checks run live in the REA path

Helix policy: generated scripts must NOT roll their own helix/thread sweeps on
propulsion bodies — OCC cannot reliably sweep a helix around a non-cylindrical
nozzle/nacelle surface. Fuel-inlet ports are modelled as an axial ``polarArray``
of bores. See ``strategies.rule_domain_f``.
"""

from . import specs       # noqa: F401  (re-exported for backend.propulsion.specs.*)
from . import rea_schema   # noqa: F401
from . import validation   # noqa: F401
