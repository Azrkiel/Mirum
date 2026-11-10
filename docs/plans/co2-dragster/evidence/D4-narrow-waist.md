# D4 evidence — narrow-waist style

Date: 2026-11-10. Branch `feat/co2-dragster-d4-narrow-waist`, base D2 head `b8d0174` (PR #23, open, CI green;
contains the D1 merge `f0b3f98`). Builder: `backend/dragsters/styles/narrow_waist.py` (the subplan body plus a
style/version guard, nothing else). Preset unchanged. **No `NarrowWaistV2` bound narrowed and no slope rule added.**

## 1. Setup

Windows 11 laptop, 16 logical CPUs, AC power (`BatteryStatus` 2 checked before the timing run). Docker, one
container at a time, `--memory 3g --network none --tmpfs /tmp:rw,exec,size=1g,mode=1777`. Iteration used
`mirum-verify:d2` with code bind-mounted read-only (deps identical); final suite used rebuilt `mirum-verify:d4`,
no mounts. Loft constants are D2's (ruled / 10 mm / 33 points). Scripts: `tools/d4_range_audit.py`,
`tools/d4_timing.py`, `tools/d4_render.py` (adapted from D3's). Raw data: `d4-data/`.

## 2. TDD

`test_dragster_style_narrow_waist.py` written first against D1's stub: **20 failed, 3 passed** (the 3 passes are the
registry, lazy-import and boundary-count checks). After the builder: **23 passed** in the narrow-waist file
(together with `test_dragster_spec_v2.py`: 859 passed, 0 failed).

Tests: preset (one solid; bounds vs law within `LOFT_BOUNDS_TOL_MM`, including the subplan's
`|y| <= max(front_w, housing_w)/2`, `z in [g, g+housing_h]`; volume vs `law_volume - pi rp^2 D - sum(pi ra^2 * 2a(x_axle))`
within `LOFT_VOLUME_REL_TOL`, before and after STEP round trip); measured shape cues from 0.02 mm slab
intersections of the reimported solid (width at `wf*L` < width at `ff*L` and at `L-hl/2`; height at `L-hl/2` exceeds
that at `wf*L` by >= 3.9 mm; section at `L-hl/2` equals section at `L-1` within 0.05 mm in width and height; waist
width and housing height match the spec within 0.05 mm); void probes; per-axle bore height; determinism; rejection of
non-narrow-waist specs; 14-case boundary matrix.

### Boundary matrix (14 cases; all one valid solid, STEP round trip valid, bounds/volume/voids pass)

D2 cases (same values, same nudge): `waist14-housing64`, `housingh60-body18` (front wheel 24), `housingl110`,
`front0.30-waist0.35`. New cases and the nudges D1's rules needed:

| case | change | nudges |
| --- | --- | --- |
| length160 | `length_mm` 160 | front_x 30, rear_x 125, pocket_depth 36, housing_length 60 (preset axles/pocket do not fit) |
| length300 | `length_mm` 300 | front_x 50, rear_x 250 |
| wheels16 | both wheels 16 | none |
| wheels45 | both wheels 45 | body_height 40, housing_height 60, side_height_frac 0.8, front_x 70, rear_x 160 (tall side for the bores; rear axle axially clear of the pocket) |
| housingl40-depth30 / -depth38 | `housing_length_mm` 40 | pocket_depth 30 / 38 (rule: depth <= hl - t) |
| housingl110-depth80 | `housing_length_mm` 110 | pocket_depth 80 |
| n2.0, n4.0, side0.8 | crown exponent / side fraction extremes | none |

## 3. Range audit

200 random valid `NarrowWaistV2` specs, seed `20261109`. Each field drawn uniformly over its D1 range (common block
and wheels per D1 table, style fields from the model bounds), rounded to 0.1 (0.01 for fractions and exponent),
rejection-sampled through `spec_v2` validation: 31 723 draws for 200 accepted (0.63 %). Each spec: build, STEP export,
reimport, then the same checks as the tests (one valid solid, bounds, volume, three void probes), 20 s alarm per case.

**Result: 200/200 pass, 0 timeouts, 0 failures.** Wall time per case (includes the checks, so slower than the timing
table) median 6.24 s, max 11.04 s. Worst volume error 0.357 %, worst bounds error 1e-7 mm. **No bound narrowed, no
slope rule needed** (before = after = D1's bounds). Raw: `d4-data/range-audit.jsonl`.

## 4. Timing and memory (fresh interpreter per run, `OMP_NUM_THREADS=1`, AC power)

Path: build + STEP export; reimport + validity + 3 void probes (stand-in for D5, which will cost more); STEP-derived
GLB via `local.export_preview`. Preset, 10 runs:

| stage | median s | max s |
| --- | --- | --- |
| build + STEP export | 1.83 | 2.48 |
| reimport + void probes | 1.54 | 2.26 |
| GLB | 2.82 | 4.15 |
| **build + GLB** | **4.65** (1.83 + 2.82) | 6.63 |
| **total** | **6.13** | **8.89** |

Peak RSS (VmHWM): median 587 MiB, max 628 MiB. VmPeak (`/proc/self/status`, uncapped): 2139 MiB for every preset run.
All 14 boundary cases (one run each): total 4.63-7.53 s (max length300), RSS <= 615 MiB, VmPeak 2116-2168 MiB, all
valid. Budget (<= 12 s, <= 1.0 GiB peak RSS): met. Target (build + GLB <= 6 s): met. Raw: `d4-data/timing.jsonl`.

**VmPeak vs F1.** The preset peaks at 2139 MiB uncapped, identical to D2's narrow-waist measurement (2139-2140 MiB);
the builder is the same loft call, so it adds nothing. It is still ~600 MiB over the 1536 MiB `RLIMIT_AS`, so
narrow-waist through `ProcessRunner` needs the F1 fix (`MALLOC_ARENA_MAX=2`; D2 measured 1243 MiB). D4 did not
re-measure the capped case. F1 is unfixed here; D4 does not touch `service.py`.

## 5. Visual review

Rendered from the STEP-derived GLB (`trimesh` + `matplotlib`, `tools/d4_render.py`): `d4-side.png` (-Y), `d4-top.png`
(+Z), `d4-iso.png`. Verdict: pass, preset unchanged. Rounded tapered nose; front section; clear waist pinch in the top
view (22 mm against 40 mm front and 44 mm housing); the housing steps up about 20 mm in the side view; the cartridge
opening is visible on the rear face; both 3 mm bores show on the side. Faint horizontal faceting from the polyline
crown and the ruled loft is visible in the low-poly render. The iso view shows a small notch on the housing side
near the pocket; the pocket rule keeps `rp + t` inside the section and the void probes pass, so this is a
tessellation artefact of the render, not confirmed further (D5 measures walls on the STEP).

## 6. Full dragster suite (rebuilt `mirum-verify:d4`, no mounts)

`python -m pytest -q -p no:cacheprovider -rfEs backend/tests/test_dragster_*.py frontend/test_dragster_*.py`:
**1117 passed, 7 failed, 0 skipped** (270 s). The 7 failures are all in the known F1 set (they go through
`service.generate`, the v1 worker, unchanged by D4): `test_dragster_api.py::test_real_api_downloads_and_revision`,
`test_dragster_integrated.py::{test_native_delivery_and_binding, test_required_publication_failure[glb|spec|report],
test_concurrent_native_attempts_remain_distinct, test_changed_native_artifact_is_not_published}`.
