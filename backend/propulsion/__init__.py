"""Propulsion (Domain F) — rocket nozzles, thrust chambers, regen cooling.

Provides parametric primitives and physics validators for assembly-first
generation of conical / Rao bell nozzles, combustion chambers, injector plates,
and helical regenerative cooling channels.

Pre-flight decision: helix-sweep ban (2026-05-18)
-------------------------------------------------
The historical guidance in ``backend/strategies.py`` ("NEVER attempt helical
sweeps — they crash the BRep kernel") was a project-founding heuristic from
commit d3d1189 (Apr 2026). No documented crash repro exists in git history or
``backend/telemetry.py`` records.

Falsification run on 2026-05-18 (see ``backend/propulsion_preflight/``):

* Host stack — cadquery 2.4.0, OCP 7.7.2.1
* Container stack — cadquery 2.7.0 inside ``cadenv`` (production sandbox)

All four documented helix approaches produced valid ``Solid`` results in both
environments:

  - Approach A: ``cq.Wire.makeHelix(...).sweep(...)``        → isValid=True
  - Approach B: ``BRepOffsetAPI_MakePipeShell`` + Frenet     → IsDone=True, TopAbs_SOLID
  - Approach C: 80-segment polyline + ``.sweep(isFrenet=True)`` → isValid=True
  - Approach D: realistic 60-channel regen-cooling geometry  → isValid=True

Decision: lift the blanket ban. Helix sweeps are permitted **inside Domain F
helpers in cad_utils.py** (``make_helical_cooling_channels``). The
``strategies.py`` rule has been re-scoped to a qualified note: generated
scripts still must not write raw ``.sweep`` on ``.makeHelix`` — they must call
the helper.

If a future crash is observed, capture the failing prompt + generated script +
traceback under ``backend/propulsion_preflight/`` and switch
``make_helical_cooling_channels`` to the direct ``BRepOffsetAPI_MakePipeShell``
path with ``SetDiscreteMode()`` + ``SetForceApproxC1()`` (Approach B+).
"""

from . import specs       # noqa: F401  (re-exported for backend.propulsion.specs.*)
from . import rao_table   # noqa: F401
from . import validation  # noqa: F401
