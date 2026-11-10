# D2 — Lofted-body kernel, loft spike, wedge v2 builder

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 and § 6 override this file.
Agent: general-purpose (implementation, **native CadQuery required**). Wave 1.
Depends on: D0, and D1's first commit (`profiles.py`). If D1 has not pushed it yet, start the
spike (task 1) with the formulas from master § 6.2 inlined in your spike script, then switch to
`from dragsters.profiles import ...` and rebase on D1's branch. Effort: 1.5–2 days.
Branch: `feat/co2-dragster-d2-kernel`.

## Goal

Provide the one CAD kernel that turns a profile law into a valid, symmetric, closed solid with
cartridge pocket and axle bores, prove by measurement that it is robust and fast enough across
the D1 ranges, and freeze the numerical tolerances that verification (D5) will use. Also
implement the v2 wedge builder.

## Read first

- `backend/dragsters/geometry.py` (v1 cut construction to mirror)
- `backend/dragsters/local.py` (`export_preview`, STEP export/reimport pattern)
- `docs/plans/co2-dragster/D1-spec-v2.md` § 1–3 (profile laws and ranges)
- CadQuery docs for `cq.Solid.makeLoft(listOfWire, ruled)`, `cq.Wire.makePolygon`, `cq.Edge.makeSpline`
- [D0 evidence](evidence/D0-baseline.md) § 2 (local verify-image CA workaround) and § 4 F1: on a 16-CPU host the worker's VmPeak (~1.55-1.6 GiB) already exceeds the 1536 MiB `RLIMIT_AS`, so service-level dragster tests flake locally (TIMEOUT/SIGSEGV). Record VmPeak as well as RSS in the spike; do not raise limits (invariant 7)

## Owns (create)

- `backend/dragsters/kernel.py` (imports cadquery at module level is allowed here)
- `backend/dragsters/tolerances.py` (pure constants; imported by D5 validation)
- `backend/dragsters/styles/wedge.py` (replace D1 stub body)
- `backend/tests/test_dragster_kernel.py`, `backend/tests/test_dragster_style_wedge_v2.py`
- `docs/plans/co2-dragster/evidence/D2-loft-spike.md` + `tools/d2_loft_spike.py` under `docs/plans/co2-dragster/evidence/` (spike script kept for re-runs)

Must not touch: `geometry.py`, `validation.py`, `spec*.py`, `profiles.py` (propose changes to D1 instead), API/UI.

## Kernel API (freeze)

```python
def section_wire(x, a, s, c, n, ground, crown_points=CROWN_POINTS) -> cq.Wire
    # closed wire in plane X=x: bottom edge (y=-a..a, z=ground), right side (z=ground..ground+s),
    # crown from y=+a to y=-a with z = ground + crown_height_at(a,s,c,n,y), left side down.
    # Same edge count and same start vertex for every section (loft needs matching topology).
    # Sample the crown with cosine spacing in y so points cluster near y=±a (vertical tangent).
def loft_body(stations, n, ground, length, *, step_mm=STATION_STEP_MM, ruled=LOFT_RULED) -> cq.Solid
    # sections at the union of station x's and a uniform grid of spacing <= step_mm over [0, length];
    # rejects (ValueError) if the result is not one valid solid with positive finite volume.
def cut_pocket_and_bores(body, *, length, pocket_diameter, pocket_depth, pocket_center_z,
                         bores: list[tuple[float, float, float]], y_extent) -> cq.Solid
    # pocket: cylinder r=d/2 from (L-D, 0, zp) along +X, length D+1 (exits rear face)
    # each bore (x, z, d): cylinder from (x, -y_extent-1, z) along +Y, length 2*y_extent+2
    # rejects if result is not exactly one valid solid
```

`tolerances.py` (values fixed by the spike, documented with the measurement they came from):

```python
LOFT_RULED: bool
STATION_STEP_MM: float
CROWN_POINTS: int
LOFT_VOLUME_REL_TOL: float     # default 0.010, hard max 0.020
LOFT_BOUNDS_TOL_MM: float      # hard max 0.25
WEDGE_BOUNDS_TOL_MM = 0.01     # v1 value — do not change
WALL_DISTANCE_TOL_MM = 0.01
VOID_TOLERANCE_MM3 = 0.001     # v1 value — do not change
```

## Tasks

1. **Spike (test-free, measured).** Script `evidence/tools/d2_loft_spike.py` builds station lists for: D1 teardrop preset, D1 narrow-waist preset, and the boundary set below, under every combination of `ruled ∈ {False, True}`, `step_mm ∈ {10, 7.5, 5}`, `crown_points ∈ {17, 33}`. For each build: valid single solid? STEP export → reimport still one valid solid? volume vs `law_volume` (rel error), bbox vs law bounds (max |error| mm), build seconds, STEP→GLB seconds (via `local.export_preview`), peak RSS. Run inside the verify image.
   Boundary set (each changes only the named fields from the preset): teardrop `crown_exponent` 2.0 and 4.0; `side_height_frac` 0.3 and 0.8; `nose_width_mm` 8 with `max_width_mm` 64; `tail_width_mm == max_width_mm`; `max_section_x_frac` 0.25 and 0.6. Narrow waist `waist_width_mm` 14 with `housing_width_mm` 64; `housing_height_mm` 60 with `body_height_mm` 18; `housing_length_mm` 110; `front_section_x_frac` 0.30 with `waist_x_frac` 0.35. Where a boundary combination violates D1 rules, nudge the *other* fields minimally and record it.
2. **Pick constants** by this rule, in order: (a) every case valid before and after STEP round trip; (b) build + GLB ≤ 6 s median and ≤ 9 s max per case; (c) smallest volume error; tie-break smooth over ruled (better looking), then fewer sections. Set `LOFT_VOLUME_REL_TOL = max(0.010, 1.5 × worst observed error)` and `LOFT_BOUNDS_TOL_MM = max(0.05, 1.5 × worst observed)`; if either would exceed its hard max, stop and report — do not proceed with a looser value.
3. **Write the evidence doc** with the full table, the chosen constants, and failures seen.
4. **TDD the kernel** (`test_dragster_kernel.py`, real CAD): section wire closed/planar/at X; same edge count across a law; loft of constant section equals prism volume within 1e-6 rel; preset laws produce one valid solid; STEP round trip keeps validity; `cut_pocket_and_bores` removes exactly `π r² D` (+ bores) on a box body within 1e-6 rel; a pocket that breaks out of the body still yields one solid but fails nothing here (kernel doesn't judge walls — that's D5); a bore placed outside the body is a no-op on volume (document; D5 catches it).
5. **Wedge v2 builder** `styles/wedge.py: build(spec: WedgeV2) -> cq.Solid`: v1 polygon extrude (copy the construction, do not import `geometry.py` privates) then `cut_pocket_and_bores` with per-axle z (`front_d/2`, `rear_d/2`), `y_extent = W/2`. Tests: wedge preset builds; bounds equal `[0,L]×[±W/2]×[g,g+H]` within 0.01; volume equals `W·L·(N+3H)/4 − π rp² D − Σ π ra² W` within `max(0.1, 1e-6 V)`; a v1-equivalent spec (equal wheel diameters = 2·axle_z) gives the same volume as v1 `build_body` within 1e-6 rel.
6. Lazy-import rule: `styles/wedge.py` imports cadquery inside `build` (registry import must stay CAD-free).

## Exit criteria

- [ ] Spike evidence doc committed with the full matrix and chosen constants; every case valid.
- [ ] `tolerances.py` constants within hard maxima.
- [ ] `pytest -q backend/tests/test_dragster_kernel.py backend/tests/test_dragster_style_wedge_v2.py` passes in the verify image, zero skips; full dragster suite still green.
- [ ] Preset build + GLB median ≤ 6 s in the verify image (numbers in PR).
- [ ] Status cell updated.
