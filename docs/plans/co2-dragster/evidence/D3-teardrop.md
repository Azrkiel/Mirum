# D3 evidence — teardrop style

Date: 2026-11-10. Branch `feat/co2-dragster-d3-teardrop`, base D2 head `b8d0174` (PR #23, open, CI green;
contains the D1 merge `f0b3f98`). Builder: `backend/dragsters/styles/teardrop.py` (exact subplan body plus a
style/version guard, nothing else). Preset unchanged. No `TeardropV2` bound was narrowed.

## 1. Setup

Windows 11 laptop, 16 logical CPUs, AC power (`BatteryStatus` 2 checked before the timing run). Docker, one
container at a time, `--memory 3g --network none --tmpfs /tmp:rw,exec,size=1g,mode=1777`. Iteration used
`mirum-verify:d2` with code bind-mounted read-only (deps identical); the final suite used a rebuilt
`mirum-verify:d3` with no mounts. Loft constants are D2's (ruled / 10 mm / 33 points).
Scripts: `tools/d3_range_audit.py`, `tools/d3_timing.py`, `tools/d3_render.py`. Raw data: `d3-data/`.

## 2. TDD

`test_dragster_style_teardrop.py` written first against D1's stub: **19 failed, 3 passed** (the 3 passes are the
registry, lazy-import, and boundary-count checks). After the builder: **22 passed, 0 skipped** (71 s).

Tests: preset (one solid, bounds vs law within `LOFT_BOUNDS_TOL_MM`, volume vs
`law_volume - pi rp^2 D - sum(pi ra^2 * 2a(x_axle))` within `LOFT_VOLUME_REL_TOL`, before and after STEP round
trip); void probes (pocket + both bores, <= `VOID_TOLERANCE_MM3`); per-axle bore height; determinism (volume
1e-9 rel, identical bbox); rejection of non-teardrop specs; 14-case boundary matrix.

### Boundary matrix (14 cases, all valid single solid, STEP round trip valid, bounds/volume/voids pass)

D2 cases: `n2.0, n4.0, side0.3, side0.8, nose8-max64, tail=max, maxx0.25, maxx0.6` (same nudges as D2).
New cases and the other-field nudges the D1 rules required:

| case | change | nudges |
| --- | --- | --- |
| length160 | `length_mm` 160 | front_x 30, rear_x 125, pocket_depth 40 (axles/pocket must fit the shorter body) |
| length300 | `length_mm` 300 | front_x 50, rear_x 250 |
| wheels16 | both wheels 16 | none |
| wheels45 | both wheels 45 | side_height_frac 0.8, front_x 70, rear_x 160 (tall side for the bore; rear axle axially clear of the pocket: x + ra + t <= L - D) |
| depth30, depth80 | `pocket_depth_mm` 30 / 80 | none |

## 3. Range audit

200 random valid `TeardropV2` specs, seed `20261109`. Sampler: each field uniform over its D1 range (common
block and wheels per D1 table, style fields from the model bounds), rounded to 0.1 (0.01 for fractions and the
exponent), rejection-sampled through `spec_v2` validation: 72 782 draws for 200 accepted (0.27 %).
Each spec: build, STEP export, reimport, then the same checks as the tests (one valid solid, bounds, volume,
three void probes), 20 s alarm per case.

**Result: 200/200 pass, 0 timeouts, 0 failures.** Per-case wall time median 4.54 s, max 5.50 s. Worst volume
error 0.360 %, worst bounds error 1e-7 mm. **No bound narrowed** (before = after = D1's bounds).

## 4. Timing, memory (fresh interpreter per run, `OMP_NUM_THREADS=1`, AC power)

Path: build + STEP export; reimport + validity + 3 void probes (a stand-in for D5's verification, which will
cost more); STEP-derived GLB via `local.export_preview`.

Preset, 10 runs:

| stage | median s | max s |
| --- | --- | --- |
| build + STEP export | 1.86 | 2.35 |
| reimport + void probes | 1.95 | 2.28 |
| GLB | 2.68 | 2.94 |
| **build + GLB** | **4.5** (1.86 + 2.68) | 5.3 |
| **total** | **6.58** | **7.13** |

Peak RSS (VmHWM): median 613 MiB, max 671 MiB. VmPeak: 2157-2158 MiB.
All 14 boundary cases (one run each): total 5.32-8.00 s (max length300 = 8.00 s), RSS <= 644 MiB, VmPeak
2133-2190 MiB, all valid. Budget (<= 12 s, <= 1.0 GiB): met. Target (build + GLB <= 6 s): met.

**VmPeak vs F1.** The teardrop preset peaks at 2157 MiB uncapped, the same as D2 measured (2158-2175 MiB);
the builder is the same loft call, so it adds nothing. It is still far above the 1536 MiB `RLIMIT_AS`. As D2
found, teardrop through `ProcessRunner` needs the F1 fix (`MALLOC_ARENA_MAX=2`, D2: 1262 MiB). D3 did not
re-measure the capped case. F1 is unfixed here; D3 does not touch `service.py`.

## 5. Visual review

Rendered from the STEP-derived GLB (`trimesh` + `matplotlib`, `tools/d3_render.py`): `d3-side.png` (-Y),
`d3-top.png` (+Z), `d3-iso.png`.

Verdict: pass, preset unchanged. Rounded, tapered nose; smooth widening to the maximum section at 0.35 L
(77 mm, forward of mid-length); gentle taper to the tail; the pocket opening is clearly inside the tail face;
both 3 mm bores are visible on the side. Flat bottom, domed crown. Faint faceting from the polyline crown is
visible in the low-poly render only.

## 6. Full dragster suite (rebuilt `mirum-verify:d3`, no mounts)

`python -m pytest -q -p no:cacheprovider -rfEs backend/tests/test_dragster_*.py frontend/test_dragster_*.py`:
**1115 passed, 8 failed, 0 skipped** (268 s). The 8 failures are exactly the known F1 set:
`test_dragster_api.py::test_real_api_downloads_and_revision`,
`test_dragster_integrated.py::{test_native_delivery_and_binding, test_required_publication_failure[step|glb|spec|report],
test_concurrent_native_attempts_remain_distinct, test_changed_native_artifact_is_not_published}`.
