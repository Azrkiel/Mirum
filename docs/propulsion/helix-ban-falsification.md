# Helix-Sweep Ban — Pre-flight Result

Date: 2026-05-18
Branch: `worktree-mirum+propulsion-domain-f`

## Verdict

**Ban is stale.** Lift it for Domain F use inside `cad_utils.py` helpers.

## Evidence

### Investigation

* `git blame backend/strategies.py:206` → blamed to commit `ec8baa6` (May 2 2026,
  *"agents: add PMI schemas and flatten JSON-schema for Gemini structured output"*).
  That commit is a reorganization; the text "helical sweeps … crash the BRep
  kernel" was reflowed there but did **not** originate there.
* `git log -S "helical sweeps"` traces the original text to `d3d1189` (Apr 4
  2026, *"Implement Text-to-CAD Pipeline with FastAPI and Streamlit"*) — the
  founding commit. No crash repro, traceback, or telemetry record is referenced
  in the commit body or in the `architecture.md` history.
* `git log -S "makeHelix"` / `BRepOffsetAPI_MakePipeShell` finds no
  production code that ever attempted these constructions. The
  `make_metric_thread` history shows it was authored as a ribbed-cylinder
  approximation from day one — never a fallback from a failing helix.
* Telemetry: `backend/telemetry.py` writes to `/tmp/mirum/telemetry/critic_loop.jsonl`
  inside the container; that path is empty (no recorded `helix`/`sweep`
  failures). The `backend/telemetry_logs/` path referenced in the handoff was
  retired (see telemetry module docstring) due to a `root:root` volume
  permission bug.

### Reproduction

Script: `backend/propulsion_preflight/test_helix_sweep_ban.py`

Ran in two environments:

| Env | cadquery | OCP |
| --- | --- | --- |
| Host (Windows) | 2.4.0 | 7.7.2.1 |
| Container `cadenv` (production sandbox) | **2.7.0** | importable |

| Approach | Host | Container |
| --- | --- | --- |
| A. `cq.Wire.makeHelix(...).sweep(isFrenet=True)` | isValid=True, vol=754 mm³ | isValid=True, vol=754 mm³ |
| B. `BRepOffsetAPI_MakePipeShell` (Frenet + ApproxC1) | IsDone=True, TopAbs_SOLID | IsDone=True, TopAbs_SOLID |
| C. 80-segment polyline + `.sweep(isFrenet=True)` | isValid=True, vol=679 mm³ | isValid=True, vol=679 mm³ |
| D. Realistic regen single channel (r=27 mm, 2×3 mm rect, 15° helix angle) | isValid=True, vol=80 mm³ | isValid=True, vol=80 mm³ |

## Divergence from handoff

* Handoff stated `cadquery==2.4.0` inside the sandbox; the running container
  actually ships **cadquery 2.7.0** in the `cadenv` conda env. Step 4 implementation
  must therefore validate against 2.7 API surfaces (Spline / Sweep / revolve
  signatures unchanged across 2.4 → 2.7 for the calls we need, but worth a
  spot-check during primitive development).

## Implications for plan

* `make_helical_cooling_channels` can use the **simplest** path:
  `cq.Wire.makeHelix(...).sweep(...)` inside the helper, no direct OCP needed.
  Falls back to Approach B only if scale-up (60 channels × boolean cut)
  triggers a failure under integration tests.
* `strategies.py:206` rule reword (Step 5):
  > "For threaded cylinders, use `make_metric_thread`. For propulsion-domain
  > helical features (regen cooling channels, scrolls), call
  > `make_helical_cooling_channels` — never write `.sweep` on `.makeHelix`
  > directly in generated scripts."
* Decision recorded at top of `backend/propulsion/__init__.py` so future
  agents grep-finding the ban can see the falsification.
