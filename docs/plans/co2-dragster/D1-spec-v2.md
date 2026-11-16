# D1 — Spec v2, profile laws, style registry, v1 compatibility

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 and § 6 override this file.
Agent: general-purpose (implementation, pure Python — **no CadQuery needed**). Wave 1.
Depends on: D0. Effort: 1–1.5 days. Branch: `feat/co2-dragster-d1-spec-v2`.

## Goal

Freeze every data contract the other workstreams build against: the v2 spec models with all
cross-field rules, the pure-Python profile laws for the lofted styles, the style registry with
stubs, v1→v2 upgrade, and candidate presets. Nothing here imports CadQuery.

**Push task 1 (profiles.py) as its own commit early** — D2 starts from it.

## Read first

- `backend/dragsters/spec.py` (v1 model style: strict, frozen, `extra='forbid'`, `allow_inf_nan=False`, integer version check, relationship list with messages)
- `backend/tests/test_dragster_spec.py` (test style)
- `backend/cem/core.py` (`StrictModel`) — reuse if equivalent config; otherwise mirror v1's `ConfigDict`
- Master § 6.1–6.3

## Owns (create)

- `backend/dragsters/profiles.py`
- `backend/dragsters/spec_v2.py`
- `backend/dragsters/styles/__init__.py`, `styles/wedge.py`, `styles/teardrop.py`, `styles/narrow_waist.py` (**stubs only**: `def build(spec): raise NotImplementedError('<style> builder lands in D2/D3/D4')`)
- `backend/dragsters/hardware.py` (**stub only**, D6 replaces the bodies): `def wheel_envelopes(spec): return []`, `def cad_checks(body, spec): return {}`, `def summary(spec, volume_mm3, bounds): return {'hardware': {}, 'mass': {'value_g': None, 'basis': 'body_only', 'reason': 'hardware checks not implemented'}}`. Module must not import cadquery at import time.
- `backend/dragsters/presets/wedge.json`, `teardrop.json`, `narrow_waist.json`
- `backend/tests/test_dragster_profiles.py`, `backend/tests/test_dragster_spec_v2.py`

May edit: `backend/dragsters/__init__.py` (docstring only; must stay import-free). Must not touch: `spec.py` (v1 frozen), anything CAD, API, UI.

## Contract details

### 1. `profiles.py` (stdlib only: `math`, `dataclasses`, `typing`)

```python
@dataclass(frozen=True)
class Station:
    x: float; a: float; s: float; c: float      # mm; a half-width, s side height, c crown height
    kind: Literal['smoothstep', 'ease_out', 'hold'] = 'smoothstep'   # interpolation to the NEXT station
```

- `interpolate(stations, x) -> tuple[a, s, c]`: stations strictly increasing in x, first at 0. For `t=(x-x0)/(x1-x0)`: `smoothstep f=3t²-2t³`; `ease_out f=sin(πt/2)`; `hold f=0` (keeps station values until next). `x` outside `[0, last.x]` → `ValueError`.
- `section_area(a, s, c, n) = 2*a*s + 2*a*c*Γ(1+1/n)²/Γ(1+2/n)` (use `math.gamma`).
- `crown_height_at(a, s, c, n, y) = s + c*(1-(|y|/a)**n)**(1/n)` for `|y| ≤ a`, else `ValueError`.
- `disk_margin(a, s, c, n, yc, zc, r) -> float` (z' measured from body bottom): the min of `a-(|yc|+r)`, `zc-r`, `crown_height_at(a,s,c,n,|yc|+r)-(zc+r)`; returns `-inf` if `|yc|+r > a`. ≥ 0 ⇔ the square of half-side `r` (hence the disk of radius `r`) lies in the section.
- `law_volume(stations, n, samples=4001) -> float`: composite Simpson over `[0, last.x]` of `section_area(interpolate(...))`. `samples` odd.
- `teardrop_stations(spec) -> list[Station]` and `narrow_waist_stations(spec) -> list[Station]` per § 3 below.
- `half_width_at(spec, x)`, `side_height_at(spec, x)`, `top_at(spec, x, y)` dispatching on style (wedge uses the v1 law: top `= N + (H-N)*min(2x/L, 1)` above bottom, flat across y; `a = W/2`).
- `max_half_width_over(spec, x0, x1, step=0.25)` (sampled, endpoints included).

### 2. `spec_v2.py` — common block (every style)

Model config identical to v1 (`extra='forbid', strict=True, frozen=True, allow_inf_nan=False`), integer `schema_version` check, `units: Literal['mm']`.

| Field | Range (inclusive) | Meaning |
| --- | --- | --- |
| `schema_version` | `2` | integer only |
| `style` | `'wedge' \| 'teardrop' \| 'narrow_waist'` | discriminator |
| `length_mm` | 160–300 | nose (X=0) to rear face |
| `ground_clearance_mm` | 1–10 | flat body bottom Z (ground = wheel contact, Z=0) |
| `front_axle_x_mm`, `rear_axle_x_mm` | 10–290 | bore X |
| `axle_bore_diameter_mm` | 2–5 | through-bore along Y |
| `pocket_diameter_mm` | 12–24 | rear-open cylinder along −X, centred Y=0 |
| `pocket_depth_mm` | 30–80 | from rear face |
| `pocket_center_z_mm` | 10–60 | absolute Z of pocket axis |
| `min_wall_mm` | 2–5 | analytic wall/separation lower bound |
| `wheels.front_diameter_mm`, `wheels.rear_diameter_mm` | 16–45 | axle Z = diameter/2 (derived) |
| `wheels.width_mm` | 2–12 | envelope width |
| `wheels.side_clearance_mm` | 0.5–5 | gap wheel inner face ↔ body |
| `stock` | `null` or `{length_mm 160–320, width_mm 30–80, height_mm 25–80}` | key required |
| `material` | `null` or `{name: ^[A-Za-z0-9 _-]{1,40}$, density_g_cm3 0.05–2.0}` | key required |

Common relationship rules (message names the fields, like v1):

1. `front_axle_x < rear_axle_x`, and `rear_axle_x - front_axle_x ≥ (front_d + rear_d)/2 + 2`.
2. For each axle (`x`, `za = d/2`, `ra = bore/2`): `x - ra ≥ t`; `x + ra ≤ L - t`; `za - ra ≥ g + t`.
3. Bore/pocket separation for each axle: **either** `x + ra + t ≤ L - D` (axially clear) **or** `za + ra + t ≤ zp - rp` (vertically clear).
4. `zp - rp ≥ g + t`.
5. Style containment (below) must pass.

### 3. Style blocks

**WedgeV2** — `width_mm` 32–60, `height_mm` 28–45, `nose_height_mm` 8–18 (same meaning as v1). Rules: `N < H`; `L - D ≥ L/2 + t`; `W/2 ≥ rp + t`; `zp + rp ≤ g + H - t`; per axle `za + ra + t ≤ g + N + (H-N)*min(2(x-ra)/L, 1)`.

**TeardropV2** — `nose_width_mm` 8–30, `nose_height_mm` 6–25, `max_width_mm` 32–64, `max_height_mm` 26–55, `max_section_x_frac` 0.25–0.6, `tail_width_mm` 26–64, `tail_height_mm` 24–55, `side_height_frac` 0.3–0.8, `crown_exponent` 2.0–4.0.
Stations (`h → s = side_height_frac·h, c = h − s`):
`(0, nose_w/2, nose_h, ease_out) → (frac·L, max_w/2, max_h, smoothstep) → (L, tail_w/2, tail_h)`.
Rules: `nose_w < max_w`, `nose_h < max_h`, `tail_w ≤ max_w`, `tail_h ≤ max_h`.

**NarrowWaistV2** — `nose_width_mm` 8–30, `nose_height_mm` 6–25, `front_section_x_frac` 0.10–0.30, `front_width_mm` 24–56, `waist_x_frac` 0.35–0.60, `waist_width_mm` 14–40, `body_height_mm` 18–40, `housing_width_mm` 30–64, `housing_height_mm` 28–60, `housing_length_mm` 40–110, `side_height_frac` 0.3–0.8, `crown_exponent` 2.0–4.0.
Stations: `(0, nose_w/2, nose_h, ease_out) → (ff·L, front_w/2, body_h, smoothstep) → (wf·L, waist_w/2, body_h, smoothstep) → (L−hl, housing_w/2, housing_h, hold) → (L, housing_w/2, housing_h)`.
Rules: `ff·L < wf·L ≤ L − hl − 10`; `waist_w < front_w`; `waist_w < housing_w`; `nose_w ≤ front_w`; `nose_h ≤ body_h`; `housing_h ≥ body_h + 4` (raised housing); `D ≤ hl − t` (pocket inside the housing hold region).

**Lofted containment** (teardrop and narrow-waist), using `profiles`:
- Pocket: for every x in `[L−D, L]` sampled at step ≤ 0.5 mm with both ends, `disk_margin(a, s, c, n, 0, zp−g, rp + t) ≥ 0`.
- Bores: `za − g + ra + t ≤ min s(x)` over `[x−ra, x+ra]` (sampled, step ≤ 0.25 mm). (Bores run under the crown; the side height is the lowest top along Y.)
- Bores inside length: rule 2 already.

### 4. Helpers

- `parse_any(text: str) -> DragsterSpec | DragsterSpecV2`: strict JSON; reads `schema_version` (must be `int` 1 or 2) then validates with the right model; anything else → `ValueError`. Uses `TypeAdapter(DragsterSpecV2)` for the union.
- `upgrade_v1(v1) -> tuple[WedgeV2, dict[str, str]]`: copies shared fields; `wheels = {front_diameter: 2*axle_z, rear_diameter: 2*axle_z, width: 6.0, side_clearance: 1.5}`; `stock=None`, `material=None`. Provenance: v1 fields → `'migrated_from_v1'`, wheel diameters → `'derived_from_v1_axle_z'`, width/side_clearance → `'preset:wedge'`. If the result violates v2 ranges (e.g. v1 `ground_clearance_mm = 0`, or axle_z < 8), raise `ValueError` explaining that the v1 file still generates as v1.
- `canonical_spec_v2(spec)`, `specification_digest_v2(spec)`: same canonical rule as v1 (`sort_keys`, compact separators, `allow_nan=False`, `model_dump(mode='json')`).

### 5. `styles/__init__.py`

```python
@dataclass(frozen=True)
class StyleDefinition:
    name: str; label: str; generator_version: str
    build: str                      # 'dragsters.styles.<module>:build' — resolved lazily, never at import
    preset_path: str
REGISTRY = {
  'wedge':        StyleDefinition('wedge', 'Low wedge', 'dragster_wedge_v2', 'dragsters.styles.wedge:build', 'presets/wedge.json'),
  'teardrop':     StyleDefinition('teardrop', 'Smooth teardrop', 'dragster_teardrop_v2', ...),
  'narrow_waist': StyleDefinition('narrow_waist', 'Narrow waist + raised rear housing', 'dragster_narrow_waist_v2', ...),
}
def resolve_build(style: str) -> Callable  # importlib, lazily
def load_preset(style: str) -> DragsterSpecV2
```

Pure law functions live in `profiles.py` (not in the registry) so validation can use them without
touching builders (master § 6.3).

### 6. Candidate presets (validate; adjust only if a rule fails, and record why)

- `wedge.json` = `upgrade_v1(examples/wedge.json)` output.
- `teardrop.json`: L 220, g 3, front_x 40, rear_x 185, bore 3, pocket d 20 / depth 52 / zp 30.5, t 2, wheels 30/34/6/1.5, nose 16×14, max 56×50 at 0.35, tail 44×46, side_frac 0.5, n 3.0, stock null, material null.
- `narrow_waist.json`: L 230, g 3, front_x 40, rear_x 195, bore 3, pocket 20/60/zp 31, t 2, wheels 30/34/6/1.5, nose 14×12, front 0.15/40, waist 0.45/22, body_h 26, housing 44×46×80, side_frac 0.7, n 3.0, stock null, material null.

## Tests (write first)

`test_dragster_profiles.py`:
- interpolation hits station values exactly at stations; monotone between stations for each kind; `hold` constant; out-of-range x raises.
- `section_area` with `n=2` equals `2as + πac/2` (half-ellipse) to 1e-9; `c=0` gives rectangle.
- `law_volume` of a constant section equals area·L (1e-9 rel); of a linear ramp matches analytic Simpson within 1e-6 rel.
- `disk_margin` positive/zero/negative cases incl. `|yc|+r > a` → `-inf`.
- teardrop/narrow-waist station lists for the presets match hand-computed values.
- `import backend.dragsters.profiles` works with `sys.modules['cadquery']=None` (prove no CAD import).

`test_dragster_spec_v2.py`:
- all three presets validate; canonical round-trip is byte-stable; digest stable.
- every field: below-min, above-max, bool, string, NaN/inf, missing, extra key → rejected.
- every relationship rule has at least one violating case whose error message names the fields (rules 1–4 per style; style-specific rules; pocket containment breach by raising `zp`; bore breach by shrinking `side_height_frac`; bore/pocket separation where both disjuncts fail).
- `parse_any` dispatches v1/v2, rejects `schema_version` `"2"`, `2.0`, `3`, missing.
- `upgrade_v1(examples/wedge.json)` → valid WedgeV2 with expected provenance; v1 with `ground_clearance_mm=0` → clear error.
- `styles` import does not import cadquery; `resolve_build('teardrop')` returns the stub that raises `NotImplementedError`.
- Existing `test_dragster_spec.py` untouched and passing.

## Exit criteria

- [ ] `python -m pytest -q backend/tests/test_dragster_profiles.py backend/tests/test_dragster_spec_v2.py backend/tests/test_dragster_spec.py` passes with zero skips (plain Python env with pydantic is enough).
- [ ] Full existing dragster suite still passes in the verify image.
- [ ] No module in Owns imports cadquery/numpy/trimesh at import time (test-proven).
- [ ] Presets validate; any change from the candidate values is listed in the PR with the failing rule.
- [ ] Status cell updated.
