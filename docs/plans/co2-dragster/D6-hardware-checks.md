# D6 — Hardware checks: wheel envelopes, stock containment, body mass

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 and § 6 override this file.
Agent: general-purpose (pure math + small CAD checks). Wave 2 (parallel with D3, D4, D5).
Depends on: D1 and D2 merged. Effort: 1 day. Branch: `feat/co2-dragster-d6-hardware`.

## Goal

Replace the D1 `hardware.py` stub: place wheel **envelopes** (display/check only, never part of the
STEP), check they clear the body, check optional stock-block containment, and compute body-only mass
when a density is supplied. No global material table changes.

## Read first

- D1 subplan § 2 (wheels/stock/material fields, derived axle Z)
- `backend/dragsters/profiles.py` (`max_half_width_over`), `spec_v2.py`, `tolerances.py`
- D5 subplan (how `cad_checks` and `summary` are called and how ids appear in the report)

## Owns

- `backend/dragsters/hardware.py` (replace stub; keep the three signatures)
- `backend/tests/test_dragster_hardware.py`

Must not touch: validation modules, spec, kernel, styles, API, UI. `hardware.py` imports
cadquery/OCP **only inside** `cad_checks` (frontend imports `wheel_envelopes` and `summary`).

## Contract

```python
def wheel_envelopes(spec) -> list[dict]
    # 4 entries: {'axle': 'front'|'rear', 'side': 'left'|'right',
    #             'center_mm': [x, ±y_c, d/2], 'diameter_mm': d, 'width_mm': w, 'axis': [0,1,0]}
    # y_c = a_env + side_clearance + w/2, a_env = max_half_width_over(spec, x - d/2, x + d/2)
    #       with the interval clipped to [0, L]. Left = −Y. Wedge: a_env = W/2.
def cad_checks(body, spec) -> dict[str, dict]
    # ids: wheel_clearance_front, wheel_clearance_rear  (each covers both sides)
    #      body_above_ground
    #      stock_containment   (only when spec.stock is not None)
def summary(spec, volume_mm3, bounds) -> dict
    # {'hardware': {'track_width_front_mm', 'track_width_rear_mm', 'wheelbase_mm',
    #               'wheel_envelopes': [...], 'stock_margin_mm': {length,width,height} | None},
    #  'mass': {'value_g': float|None, 'basis': 'body_only', 'density_g_cm3': float|None,
    #           'material': str|None, 'reason': None | 'no material density supplied'}}
```

Rules:
- `wheel_clearance_*`: build each envelope as a solid cylinder; `BRepExtrema_DistShapeShape(body, wheel)` distance `≥ side_clearance − WALL_DISTANCE_TOL_MM` **and** `body ∩ wheel` volume ≤ `VOID_TOLERANCE_MM3`. Evidence: measured distance per side.
- `body_above_ground`: body `zmin ≥ g − bounds tol` and `> 0` (wheels touch Z=0 by definition).
- `stock_containment`: measured extents `(xmax−xmin, ymax−ymin, zmax−zmin) ≤ stock (length, width, height) + 0.01`. Evidence: margins.
- Track width = `2·y_c` (centre-to-centre) per axle; wheelbase = `rear_x − front_x`.
- Mass: `round(volume_mm3 × density_g_cm3 / 1000, 2)` grams when `material` is set; otherwise `None` with the reason. Never fall back to any default density.

## Tasks

1. **Tests first** (`test_dragster_hardware.py`):
   - pure: envelope positions for each preset hand-checked; left/right mirror; track widths; narrow-waist rear wheel uses the **housing** half-width when the wheel x-interval overlaps the housing; envelopes clipped at nose/rear.
   - pure: mass with and without material; never uses a default.
   - CAD (real kernel): presets pass `wheel_clearance_*` and `body_above_ground`; a wedge spec with `side_clearance_mm=0.5` passes with measured distance ≈ 0.5; a mutant body widened by 2 mm (built in-test with a wider wedge) fails `wheel_clearance_*` against the original spec; stock smaller than the body by 1 mm in each axis fails `stock_containment` with negative margins; `stock=None` omits the check.
   - import test: `import dragsters.hardware` with `sys.modules['cadquery']=None` works and `wheel_envelopes`/`summary` run.
2. **Implement.**
3. Timing: `cad_checks` ≤ 1 s per preset in the verify image (record).

## Exit criteria

- [ ] Tests pass in the verify image, zero skips; full dragster suite green.
- [ ] Frontend-light import proven.
- [ ] Timing recorded.
- [ ] Status cell updated.
