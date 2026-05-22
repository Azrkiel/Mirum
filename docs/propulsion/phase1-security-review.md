# Propulsion Domain F — Security Review

Date: 2026-05-18
Branch: `worktree-mirum+propulsion-domain-f`
Skill: `mirum-security-review` (Mirum-specific checklist)

## Trigger conditions

The worktree-merge-gate rule + the `mirum-security-review` skill trigger when
a branch touches:

* `backend/schemas.py`               -- YES (added optional `propulsion_spec` field)
* `backend/cad_utils.py`             -- YES (added 5 propulsion helpers)
* `backend/compiler.py` / allowlist  -- NO (untouched)

## The compiler.py sandbox -- regression check

The skill's "do not regress" table was walked mechanically. All defences
intact:

| Defence | Value at HEAD | Verdict |
| --- | --- | --- |
| `_ALLOWED_MODULES` | `{"cadquery", "cq", "math", "cad_utils"}` | UNCHANGED |
| `_BANNED_BUILTINS` | identical to baseline | UNCHANGED |
| `_BANNED_DUNDERS`  | identical to baseline | UNCHANGED |
| `_BANNED_BARE_NAMES` | identical to baseline | UNCHANGED |
| `RLIMIT_AS = 1.5 GB` | unchanged | UNCHANGED |
| Wall-clock timeout (30 s) | unchanged | UNCHANGED |
| Process-group SIGKILL | unchanged | UNCHANGED |
| Minimal subprocess env  | unchanged | UNCHANGED |
| Stdin delivery (no temp file) | unchanged | UNCHANGED |
| Tmpfs cwd (`/tmp/mirum/work`) | unchanged | UNCHANGED |

## Generated-script attack surface

Generated CadQuery scripts can `from cad_utils import ...` -- so anything
`cad_utils` exposes is reachable from a script. Verified:

* `cad_utils.py` imports: `math` + `cadquery as cq` only. No `os`, `sys`,
  `subprocess`, `pathlib`, `importlib`, `ctypes`, `shutil`, `pickle`,
  `socket`.
* `cad_utils.py` calls: no `eval`, `exec`, `open`, `getattr`, `setattr`,
  `delattr`, `compile`, `__import__`. (`grep -nE` confirmed clean.)
* No `__class__`, `__bases__`, `__mro__`, `__subclasses__` reflection tricks
  in `cad_utils.py` or any `propulsion/` module.
* The pre-flight verdict on helix sweeps (see `propulsion/__init__.py`)
  permits `cq.Wire.makeHelix(...).sweep(...)` **only inside**
  `make_helical_cooling_channels`. Generated scripts call the helper -- they
  never see the raw sweep, satisfying the principle that direct-OCP geometry
  stays inside helpers.

## New `propulsion/` package -- import audit

| File | Imports | Risk |
| --- | --- | --- |
| `__init__.py` | `.specs`, `.rao_table`, `.validation` | none |
| `specs.py` | `typing`, `pydantic` | none |
| `rao_table.py` | `typing` | none |
| `validation.py` | `math`, `typing`, `.specs` | none |
| `rea_schema.py` | `typing` | none |

No external module is added to any allowlist. The package is a self-contained
data + validator module; it does not perform I/O or import dangerous stdlib.

## Pydantic input validation

* `PropulsionSpec` and child models are `pydantic.BaseModel` instances.
* `NozzleSpec` has `model_validator(mode="after")` enforcing per-type
  required fields (throat_radius always; for conical: exit_radius OR
  expansion_ratio, half_angle_deg, converging_angle_deg, chamber_radius; for
  bell: expansion_ratio, chamber_radius, length_fraction in [0.5, 1.0]).
* `PropulsionSpec` requires at least one of `nozzle`, `chamber`, `injector`,
  `cooling_channels`; `cooling_channels` requires a `nozzle`.
* `Optional[PropulsionSpec]` defaults to `None` on `DesignRequirements` --
  matching the existing `airfoil_spec` / `flight_env` pattern.
* `max_length` constraints on user-facing string fields elsewhere in
  `schemas.py` (200 / 2000 chars) are preserved.

## No new endpoints / no new env vars / no new rate limits

* `backend/main.py` change is one local-variable addition (`_prop_spec`) that
  reads `requirements.propulsion_spec` and threads it into the existing
  `certify_geometry` call. No new FastAPI routes, no new middleware, no new
  CORS origins, no new env vars, no rate-limit changes.

## Traceback / log handling

* New `logger.exception` / `logger.warning` calls in `agents.run_rea`
  propulsion-retry path follow the existing pattern (Python logging). No raw
  traceback string is yielded to any SSE event or HTTP response.

## Findings

**No security findings.** The Domain F implementation satisfies every gate the
`mirum-security-review` checklist enforces and does not weaken any existing
defence. Safe to merge.
