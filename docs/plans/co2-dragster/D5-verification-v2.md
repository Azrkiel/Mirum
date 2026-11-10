# D5 — Verification v2 (style-agnostic STEP checks)

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 (esp. invariants 4 and 5) and § 6 override this file.
Agent: general-purpose (CAD verification, **native CadQuery/OCP required**). Wave 2 (parallel with D3, D4, D6).
Depends on: D1 and D2 merged. Effort: 1.5–2 days. Branch: `feat/co2-dragster-d5-verify-v2`.

## Goal

`verify_step_v2(path, spec) -> report` measures a **reimported STEP** against a v2 spec for any
style, independent of the builder, and fails closed. It replaces nothing: v1 `verify_step` stays
for v1 specs.

## Read first

- `backend/dragsters/validation.py` (v1 checks, report keys — keep all of them in v2)
- `backend/dragsters/profiles.py`, `spec_v2.py`, `tolerances.py`, `hardware.py` (stub signatures)
- Master § 6.4 (report v2)
- OCP: `BRepExtrema_DistShapeShape`, `BRepBuilderAPI_MakeEdge`, `BRepAdaptor_Surface`, `GeomAbs_Cylinder`, `GeomAbs_Plane`, `TopExp_Explorer`

## Owns (create)

- `backend/dragsters/validation_v2.py`
- `backend/tests/test_dragster_validation_v2.py`

Allowed imports in `validation_v2.py`: `cadquery`, `OCP`, `dragsters.spec_v2`, `dragsters.profiles`,
`dragsters.tolerances`, `dragsters.hardware`. **Forbidden:** `kernel`, `styles.*`, `geometry` (a test
enforces this by AST-scanning the module's imports). Tests may import builders to make inputs.

## Checks (ids are a contract; D7 UI and D9 matrix use them)

Let `L, g, t, rp, D, zp, ra` from the spec; `za_f, za_r = wheel diameters / 2`;
`a(x), s(x)` from `profiles` (wedge: `a = W/2`, top from v1 law).

| id | Method | Pass rule |
| --- | --- | --- |
| `single_valid_solid` | reimport STEP, `Solids()`, `isValid()` | exactly 1 valid solid (else stop: raise with this id) |
| `absolute_bounds` | `BoundingBox()` | `x∈[0,L]`, `y∈[±Ymax]`, `z∈[g, g+Hmax]` where `Ymax = max a`, `Hmax = max(s+c)` sampled every 0.25 mm (wedge analytic); tol `WEDGE_BOUNDS_TOL_MM` / `LOFT_BOUNDS_TOL_MM` |
| `symmetry` | bbox and `Center()` | `|ymin + ymax| ≤ bounds tol` and `|centroid.y| ≤ 0.01` |
| `reference_volume` | `Volume()` | `|V − (V_outer − π rp² D − Σ π ra²·2a(x_axle))| ≤ tol`; `V_outer` = analytic wedge or `law_volume`; tol = `max(0.1, 1e-6 V)` wedge, `LOFT_VOLUME_REL_TOL·V` lofted |
| `pocket_void` | v1 probe: intersect cylinder `(rp, D)` from `(L−D,0,zp)` along X | residual ≤ `VOID_TOLERANCE_MM3` |
| `front_axle_void`, `rear_axle_void` | probe cylinder radius `ra` along Y over `|y| ≤ a_min(x±ra) − 0.05` | residual ≤ `VOID_TOLERANCE_MM3` |
| `pocket_wall` | min distance from the pocket axis segment `[(L−D,0,zp),(L,0,zp)]` to every face that is **not** a pocket face and **not** the rear face, minus `rp` | `≥ t − WALL_DISTANCE_TOL_MM` |
| `front_bore_wall`, `rear_bore_wall` | min distance from the bore axis segment `y∈[−y_in, y_in]` (`y_in = a_min(x ± (ra+t)) − ra − t`) to every face not belonging to that bore, minus `ra` | `≥ t − WALL_DISTANCE_TOL_MM` (covers top/bottom walls and bore↔pocket separation) |
| `spec_revalidation` | re-parse `canonical_spec_v2(spec)` | no error |
| hardware ids | `hardware.cad_checks(body, spec)` (D6) | merged into `checks`; any `passed: false` fails |

Face classification (write helpers + unit tests):
- **pocket face**: cylindrical surface with radius within 1e-4 of `rp` whose axis is parallel to X and passes within 1e-3 of `(·, 0, zp)`; or planar face with normal ±X at `x = L−D` (±1e-4) whose vertices are all within `rp + 1e-3` of the axis.
- **bore face**: cylindrical, radius within 1e-4 of `ra`, axis parallel to Y through `(x_axle, ·, za)` within 1e-3.
- **rear face**: planar, normal +X, at `x = L` (±1e-4).

Each check's evidence records the measured value, the threshold, and for the wall checks the
`method_limits` text: "min distance from feature axis to non-feature faces; sampled nowhere;
excludes rear face; does not certify whole-body thickness away from the pocket and bores".

Report = v1 keys (`schema_version` becomes 2, `generator_version` from the registry entry) plus
master § 6.4 keys; `hardware`/`mass` from `hardware.summary(spec, V, bounds)`. Any failure ⇒
`ValueError('STEP checks failed: <ids>')` exactly like v1 (callers rely on raising).

## Tasks

1. **Tests first** (`test_dragster_validation_v2.py`, real CAD). Positive: the three presets (build → export STEP to tmp → verify) pass every check; report keys ⊇ v1 keys ∪ § 6.4 keys; JSON-serialisable with `allow_nan=False`. If D3/D4 are not merged yet, use `kernel.loft_body` + `profiles.*_stations` directly in the test helper and switch to `resolve_build` when they land (note in PR).
2. **Negative mutants** — each built in the test by calling kernel functions with **deliberately wrong numbers** (bypassing spec validation) and verified against the *correct* spec; each must fail with the listed id and only plausibly-related ids:
   | Mutant | Expected failing id |
   | --- | --- |
   | pocket centre raised so top wall = t/2 | `pocket_wall` |
   | pocket depth +5 mm | `reference_volume` (and `pocket_wall` if it breaches) |
   | pocket missing | `pocket_void`, `reference_volume` |
   | rear bore missing | `rear_axle_void`, `reference_volume` |
   | rear bore z raised into pocket (separation < t) | `rear_bore_wall` |
   | front bore z lowered so bottom wall < t | `front_bore_wall` |
   | body shifted +0.5 mm in Y | `symmetry`, `absolute_bounds` |
   | body scaled 1.03 in X | `absolute_bounds`, `reference_volume` |
   | extra disconnected 1 mm cube | `single_valid_solid` |
   | equal-volume misplaced pocket (moved −Y by 2 mm, same size) | `pocket_void` or `pocket_wall` |
3. **Implement** `validation_v2.py` and the face helpers.
4. **Timing**: verification alone on each preset ≤ 4 s median in the verify image (record); if over, optimise distance queries (restrict face sets by bounding boxes) — do not drop checks.
5. Import-boundary test (AST) as described above.

## Exit criteria

- [ ] All positive and all mutant tests pass in the verify image, zero skips.
- [ ] v1 `validation.py` untouched; full dragster suite green.
- [ ] Verification timing per preset recorded and ≤ 4 s median.
- [ ] Status cell updated.
