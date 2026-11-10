# D3 — Teardrop style

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 and § 6 override this file.
Agent: general-purpose (CAD, **native CadQuery required**). Wave 2 (parallel with D4, D5, D6).
Depends on: D1 and D2 merged. Effort: 1 day. Branch: `feat/co2-dragster-d3-teardrop`.

## Goal

A smooth teardrop body: rounded, tapered nose that widens to a maximum section about a third of
the way back, then tapers to a tail housing big enough for the cartridge pocket. Built only from
the D1 law and the D2 kernel. Valid across the whole accepted spec range, fast, and visually
convincing.

## Read first

- D1 subplan § 1 and § 3 (TeardropV2 fields, stations, containment rules)
- `backend/dragsters/profiles.py`, `spec_v2.py`, `styles/__init__.py`, `kernel.py`, `tolerances.py`
- D2 evidence doc (`docs/plans/co2-dragster/evidence/D2-loft-spike.md`)

## Owns

- `backend/dragsters/styles/teardrop.py` (replace the D1 stub body)
- `backend/dragsters/presets/teardrop.json` (may adjust values; must stay valid)
- `backend/tests/test_dragster_style_teardrop.py`
- `docs/plans/co2-dragster/evidence/D3-teardrop.md` + screenshots `evidence/d3-*.png`

May edit (narrowing only, with evidence): the numeric `Field(ge=, le=)` bounds of `TeardropV2`
in `spec_v2.py`. Never widen a bound; never edit common fields or other styles. Must not touch
kernel, validation, API, UI.

## Build function (exact)

```python
def build(spec: TeardropV2) -> cq.Solid:
    import cadquery  # lazy
    from dragsters import kernel, profiles
    stations = profiles.teardrop_stations(spec)
    body = kernel.loft_body(stations, spec.crown_exponent, spec.ground_clearance_mm, spec.length_mm)
    y_extent = max(st.a for st in stations)
    return kernel.cut_pocket_and_bores(
        body, length=spec.length_mm, pocket_diameter=spec.pocket_diameter_mm,
        pocket_depth=spec.pocket_depth_mm, pocket_center_z=spec.pocket_center_z_mm,
        bores=[(spec.front_axle_x_mm, spec.wheels.front_diameter_mm / 2, spec.axle_bore_diameter_mm),
               (spec.rear_axle_x_mm, spec.wheels.rear_diameter_mm / 2, spec.axle_bore_diameter_mm)],
        y_extent=y_extent)
```

No other geometry (no fillets, no extra features). If the shape needs a law change (e.g. nose
rounding), stop and propose a D1 contract change; do not hand-code geometry here.

## Tasks

1. **Tests first** (`test_dragster_style_teardrop.py`, real CAD):
   - preset: one valid solid; bounds `x∈[0,L]`, `|y| ≤ max_w/2`, `z∈[g, g+max_h]` within `LOFT_BOUNDS_TOL_MM`; volume within `LOFT_VOLUME_REL_TOL` of `law_volume − π rp² D − Σ π ra²·2a(x_axle)`.
   - void probes (same method as v1 `validation.py`) confirm pocket and both bores are open (≤ 0.001 mm³ residual).
   - determinism: building the preset twice gives volumes equal to 1e-9 rel and identical bounding boxes.
   - boundary matrix (≥ 12 specs): every D2 boundary case for teardrop + each of `length_mm` 160/300, `wheels.*_diameter_mm` 16/45 (with pocket/axle fields nudged to stay valid), `pocket_depth_mm` 30/80 where valid. Each: valid single solid, STEP round-trip valid.
   - mark slow cases with the existing native marker used by dragster tests so the CI native gate runs them.
2. **Implement** `build`.
3. **Range audit.** Generate 200 random valid TeardropV2 specs (seed `20261109`, rejection-sample with `spec_v2` validation) and build each in the verify image with a 20 s per-case cap. Record failures. If any fail: find the field region responsible and **narrow** that bound in `TeardropV2` (re-run until 200/200). Record before/after bounds and the failing specs in the evidence doc.
4. **Timing.** Preset build + STEP export/reimport + GLB: 10 runs; median and max seconds, peak RSS. Must meet master invariant 7 (≤ 12 s, ≤ 1.0 GiB) — target ≤ 6 s for build + GLB so D5 verification has room.
5. **Visual review.** Render the preset GLB from side (−Y), top (+Z) and iso views to PNG (use `trimesh` + `matplotlib` `plot_trisurf` if available in the image; otherwise the standalone app `backend/dragsters/app.py` + a browser screenshot after D7 lands — note which). Check against cues: tapered rounded nose, smooth widening, max section forward of mid-length, tail housing visibly containing the pocket opening, both bore holes visible from the side. Tune the preset values (not the law) if it looks wrong; record before/after images.

## Exit criteria

- [ ] All tests pass in the verify image, zero skips; full dragster suite green.
- [ ] Range audit 200/200 after any narrowing; narrowing listed in PR.
- [ ] Timing table in evidence doc meets the budget.
- [ ] Three PNGs committed; short visual verdict in the PR.
- [ ] Status cell updated.
