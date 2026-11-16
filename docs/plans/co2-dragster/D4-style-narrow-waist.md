# D4 — Narrow-waist style with raised rear housing

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 and § 6 override this file.
Agent: general-purpose (CAD, **native CadQuery required**). Wave 2 (parallel with D3, D5, D6).
Depends on: D1 and D2 merged. Effort: 1–1.5 days. Branch: `feat/co2-dragster-d4-narrow-waist`.

## Goal

The body closest to the reference photo: tapered nose, a front section around the front axle, a
pinched waist, then a blend up and out into a taller, wider rear housing that holds the cartridge.
Built only from the D1 law and the D2 kernel; valid across its accepted range; fast; convincing.

## Read first

- D1 subplan § 1 and § 3 (NarrowWaistV2 fields, stations incl. `hold`, containment rules)
- `backend/dragsters/profiles.py`, `spec_v2.py`, `styles/__init__.py`, `kernel.py`, `tolerances.py`
- D2 evidence doc (pay attention to the abrupt waist→housing cases)

## Owns

- `backend/dragsters/styles/narrow_waist.py` (replace the D1 stub body)
- `backend/dragsters/presets/narrow_waist.json` (may adjust values; must stay valid)
- `backend/tests/test_dragster_style_narrow_waist.py`
- `docs/plans/co2-dragster/evidence/D4-narrow-waist.md` + screenshots `evidence/d4-*.png`

May edit (narrowing only, with evidence): numeric bounds of `NarrowWaistV2` in `spec_v2.py`, and
add **one** extra NarrowWaistV2 relationship rule if the range audit needs a slope limit (e.g.
`housing_w/2 − waist_w/2 ≤ k·(L − hl − wf·L)`); record it in the PR and in D1's rule list via a
comment in `spec_v2.py`. Must not touch kernel, validation, API, UI, other styles.

## Build function (exact)

Same shape as D3's `build`, using `profiles.narrow_waist_stations(spec)` and
`y_extent = max(st.a for st in stations)`. No extra geometry. Law changes → stop and propose to D1.

## Tasks

1. **Tests first** (`test_dragster_style_narrow_waist.py`, real CAD):
   - preset: one valid solid; bounds `x∈[0,L]`, `|y| ≤ max(front_w, housing_w)/2`, `z∈[g, g+housing_h]` within `LOFT_BOUNDS_TOL_MM`; volume within `LOFT_VOLUME_REL_TOL` of `law_volume − π rp² D − Σ π ra²·2a(x_axle)`.
   - shape assertions from the law (measured on the solid by planar sections with `cq` `section()` at X): width at `wf·L` < width at `ff·L` and < width at `L − hl/2`; height at `L − hl/2` > height at `wf·L` by ≥ 3.9 mm; section at `L − hl/2` equals section at `L − 1` (hold region) within 0.05 mm in width and height.
   - void probes confirm pocket and bores open; determinism as in D3.
   - boundary matrix (≥ 12 specs): every D2 narrow-waist boundary case + `length_mm` 160/300, `wheels.*_diameter_mm` 16/45, `housing_length_mm` 40 (with `pocket_depth_mm` ≤ 38) and 110. Each: one valid solid, STEP round trip valid.
2. **Implement** `build`.
3. **Range audit** — 200 random valid specs (seed `20261109`), 20 s cap each, in the verify image. Narrow bounds or add the single slope rule until 200/200; record everything.
4. **Timing** — as D3 (10 runs; ≤ 6 s build + GLB target; ≤ 12 s / 1.0 GiB hard).
5. **Visual review** — side/top/iso PNGs; cues: tapered nose, visible waist pinch in top view, raised rear housing in side view, cartridge opening on rear face, bore holes. Tune preset values if needed; record before/after.

## Exit criteria

- [ ] All tests pass in the verify image, zero skips; full dragster suite green.
- [ ] Range audit 200/200; any narrowing/slope rule listed in PR.
- [ ] Timing table meets budget.
- [ ] Three PNGs committed; visual verdict in PR.
- [ ] Status cell updated.
