# D2 evidence — loft spike, chosen tolerances, F1 impact

Date: 2026-11-09/10. Branch `feat/co2-dragster-d2-kernel` (base `main` f0b3f98, which already contains
D1 / PR #22, so `profiles.py` was imported directly; no inlined formulas and no rebase were needed).

## Summary

- **Every build in the matrix is valid** (14 cases × 12 settings = 168 lofted builds + 1 v1 yardstick):
  one valid solid for the uncut loft after a STEP round trip, and one valid solid for the cut body
  after a STEP round trip. 0 errors, 0 timeouts.
- **Chosen constants** (`backend/dragsters/tolerances.py`): `LOFT_RULED = True`, `STATION_STEP_MM = 10.0`,
  `CROWN_POINTS = 33`, `LOFT_VOLUME_REL_TOL = 0.010`, `LOFT_BOUNDS_TOL_MM = 0.05`. Both are within the
  hard maxima (0.020 and 0.25). v1 values are unchanged.
- **Deviation from the D2 selection rule (approved by the user).** By rule (c), the best volume error
  comes from *smooth* lofts. But smooth lofts overshoot the narrow-waist law bounds by up to 0.54 mm
  at the housing `hold` step, which needs `LOFT_BOUNDS_TOL_MM` 0.81 (at 10 mm) or 0.34 (at 5 mm), both
  over the 0.25 mm hard max. Per the subplan I stopped and reported. The user chose **ruled, 10 mm,
  33 points**. Its bounds error is zero and its worst volume error is 0.225 %.
- **Crown is a polyline, not a spline** (measured, § 2). With spline crowns, OCC's default `Volume()`
  is off by up to +3.98 %, and adaptive integration hangs on the narrow-waist preset.
- **Preset build + GLB** (final kernel, 5 fresh runs each): teardrop median **4.69 s** / max 4.75 s;
  narrow waist median **5.30 s** / max 6.10 s; overall median **4.72 s** (≤ 6 s).
  Peak RSS 586–667 MiB (≤ 1.0 GiB).
- **F1 gets clearly worse with lofted bodies.** VmPeak rises from 1563–1627 MiB (v1 wedge) to
  2132–2469 MiB (lofts), while RSS rises only ~70–90 MiB. Under the real 1536 MiB `RLIMIT_AS`
  wrapper, **0/9** runs survive without an arena cap and **9/9** pass with `MALLOC_ARENA_MAX=2`
  (VmPeak 1243–1262 MiB). The lofted styles (D3/D4 through the worker) need the F1 fix before they
  go through `ProcessRunner`. § 5.

## 1. Setup

- Host: Windows 11 laptop, 16 logical CPUs, on AC power (`BatteryStatus` 2, checked before each
  timed run). Docker Desktop, one container at a time, `--memory 3g`, `--network none`,
  `--tmpfs /tmp:rw,exec,size=1g,mode=1777`.
- Image `mirum-verify:d2` = `Dockerfile.verify` at this branch, built with the D0 § 2 CA workaround
  (scratch Dockerfile + `--build-context certs=`; no repo change). Spike runs bind-mounted
  `backend/` and `docs/` read-only into that image. The final tests ran on a rebuilt image with
  no mounts.
- Script `tools/d2_loft_spike.py`. Every build runs in a **fresh interpreter** with `OMP_NUM_THREADS=1`
  (the worker env). Peak RSS = `VmHWM` and peak virtual = `VmPeak`, both from `/proc/self/status`
  at the end of the child, after the GLB export, as in the worker.
- Per build:
  1. Loft.
  2. STEP export and reimport of the **uncut** loft. Volume (default `Shape.Volume()`) is compared
     with `profiles.law_volume` (4001-sample Simpson). The optimal bounding box is compared with the
     law bounds: x ∈ [0, L], y = ±max a, z ∈ [g, g + max(s + c)], sampled every 0.25 mm plus
     every station.
  3. Worker-equivalent path: `cut_pocket_and_bores` → STEP export → reimport (validity) →
     `local.export_preview` (STEP-derived GLB).
  - "build s" = loft + cuts + STEP export. "GLB s" = `export_preview`. "total" = their sum.
- Raw data: `d2-spike-data/matrix.jsonl` (final run), `f1-diag.jsonl`, `preset-timing.jsonl`.

### Boundary set and nudges

Each case changes only the named fields from the D1 preset. Three cases broke D1 rule 5
("bores stay below the side height") and needed one *other* field nudged (spike `--check` mode):

| Case | Change | Nudge (other field) | Why |
| --- | --- | --- | --- |
| td-side0.3 | `side_height_frac` 0.3 | front wheel 30 → 16 mm, rear 34 → 26 mm | lower side height; both bores must stay under it |
| td-maxx0.6 | `max_section_x_frac` 0.6 | front wheel 30 → 28 mm | side height rises more slowly to x = 132 |
| nw-housingh60-body18 | `housing_height_mm` 60, `body_height_mm` 18 | front wheel 30 → 24 mm | 18 mm body: side height 12.6 mm at the front axle |

All other cases (`td-n2.0`, `td-n4.0`, `td-side0.8`, `td-nose8-max64`, `td-tail=max` (tail 56),
`td-maxx0.25`, `nw-waist14-housing64`, `nw-housingl110`, `nw-front0.30-waist0.35`) validated unchanged.

## 2. Crown representation (why polyline)

`tools/d2_crown_probe.py`, 17 points, 5 mm step. Error vs law volume:

| Case | Crown | Loft | default `Volume()` | adaptive `computeMass(1e-6)` | mesh (0.01 mm) |
| --- | --- | --- | --- | --- | --- |
| constant section | spline | smooth | **+3.983 %** | +0.044 % | −0.024 % |
| constant section | spline | ruled | **+3.983 %** | +0.044 % | +0.025 % |
| constant section | polyline | smooth / ruled | −0.265 % | −0.265 % | −0.265 % |
| teardrop preset | spline | smooth | +0.020 % | +0.020 % | −0.047 % |
| teardrop preset | spline | ruled | **−0.727 %** | −0.009 % | −0.014 % |
| teardrop preset | polyline | smooth | −0.311 % | −0.312 % | −0.308 % |
| teardrop preset | polyline | ruled | −0.341 % | −0.341 % | −0.341 % |
| narrow-waist preset | polyline | smooth | −0.174 % | −0.183 % | −0.173 % |
| narrow-waist preset | polyline | ruled | −0.230 % | −0.230 % | −0.230 % |
| narrow-waist preset | spline | smooth | **no result: volume integration hung > 500 s** (loft itself built in 0.88 s) | | |

Also seen: the adaptive integration on a plain *extrusion* of the spline section returned −8.9 %.
With spline crowns, D5's STEP volume check would be unreliable. With polyline crowns, all three
methods agree to within 0.01 %. The odd point count puts the apex `y = 0` on a vertex, so the top
bound is exact.

## 3. Matrix (final kernel)

Per setting, over all 14 lofted cases:

| ruled/smooth, step mm, crown pts | all valid | worst \|vol err\| % | worst bounds mm | median total s | max total s | max RSS MiB | max VmPeak MiB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| smooth / 10 / 17 | True (14/14) | 0.448 | 0.5523 | 2.09 | 2.91 | 586 | 2164 |
| smooth / 10 / 33 | True (14/14) | 0.110 | 0.5376 | 2.73 | 4.50 | 629 | 2090 |
| smooth / 7.5 / 17 | True (14/14) | 0.449 | 0.4039 | 2.06 | 3.04 | 599 | 2229 |
| smooth / 7.5 / 33 | True (14/14) | 0.112 | 0.3973 | 2.89 | 6.76 | 635 | 2363 |
| smooth / 5 / 17 | True (14/14) | 0.448 | 0.2319 | 2.47 | 3.97 | 626 | 2356 |
| smooth / 5 / 33 | True (14/14) | 0.116 | 0.2291 | 3.40 | 5.21 | 675 | 2107 |
| ruled / 10 / 17 | True (14/14) | 0.562 | 0.0000 | 3.11 | 4.01 | 598 | 2129 |
| **ruled / 10 / 33 (chosen)** | **True (14/14)** | **0.225** | **0.0000** | **4.57** | **5.16** | **612** | **2303** |
| ruled / 7.5 / 17 | True (14/14) | 0.509 | 0.0000 | 3.84 | 4.27 | 625 | 2141 |
| ruled / 7.5 / 33 | True (14/14) | 0.171 | 0.0000 | 5.81 | 7.24 | 676 | 2193 |
| ruled / 5 / 17 | True (14/14) | 0.477 | 0.0000 | 4.83 | 5.58 | 677 | 2307 |
| ruled / 5 / 33 | True (14/14) | 0.139 | 0.0000 | 8.06 | 10.95 | 812 | 2313 |

Smooth-loft bounds error comes from the narrow-waist cases. At 10 mm / 33 points it is
nw-housingl110 0.538, nw-housingh60-body18 0.296, nw-waist14-housing64 0.175, nw-preset 0.137 mm.
The smooth B-spline overshoots where the smoothstep meets the constant housing. Teardrop smooth
lofts stay within 0.03 mm.

### Selection

1. (a) Every setting is valid in every case, before and after the STEP round trip.
2. (b) Median ≤ 6 s and max ≤ 9 s: excludes ruled / 5 / 33 (8.06 / 10.95 s).
3. (c) Smallest volume error: smooth / 10 / 33 (0.110 %). Its bounds error of 0.538 mm needs
   `LOFT_BOUNDS_TOL_MM = 0.81`, **over the 0.25 hard max**, as does every smooth setting (min 0.34).
   → Stop and report.
4. User decision: **ruled / 10 / 33**.
   - In the first matrix run (sequential cuts, § 6), ruled / 7.5 / 33 failed (b) with max 9.72 s.
   - With the final single-boolean cut, ruled / 7.5 / 33 now passes (b) with median 5.81 s and a
     smaller volume error (0.171 %). It would give the **same** tolerance values (0.010 / 0.05).
   - It was not switched to: the user chose 10 / 33, and 7.5 / 33 leaves only 0.19 s of median
     margin against the 6 s preset budget. Preset timings vary by ±1–6 s run to run on this host
     (§ 4, § 5).
5. Tolerances: `LOFT_VOLUME_REL_TOL = max(0.010, 1.5 × 0.00225) = 0.010` (worst case nw-housingl110,
   −0.225 %); `LOFT_BOUNDS_TOL_MM = max(0.05, 1.5 × 1e-7) = 0.05`.

The chosen setting uses 24 sections for teardrop (L = 220) and 25 for narrow waist (L = 230), with
811 and 846 faces after the cuts.

### Full matrix rows

| case | ruled/smooth, step mm, crown pts | sections | valid (loft RT, body RT) | vol err % | bounds err mm | build s | GLB s | total s | RSS MiB | VmPeak MiB |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| wedge-v1 | v1 builder | | True | | | 0.04 | 0.74 | 0.78 | 520 | 1563 |
| td-preset | smooth / 10 / 17 | 24 | True, True | -0.312 | 0.0255 | 0.73 | 1.12 | 1.85 | 571 | 2081 |
| td-preset | smooth / 10 / 33 | 24 | True, True | -0.076 | 0.0255 | 1.33 | 1.21 | 2.53 | 602 | 2083 |
| td-preset | smooth / 7.5 / 17 | 32 | True, True | -0.313 | 0.0140 | 1.29 | 1.41 | 2.69 | 584 | 2081 |
| td-preset | smooth / 7.5 / 33 | 32 | True, True | -0.078 | 0.0140 | 1.69 | 1.68 | 3.37 | 625 | 2085 |
| td-preset | smooth / 5 / 17 | 46 | True, True | -0.311 | 0.0066 | 0.93 | 1.19 | 2.11 | 608 | 2081 |
| td-preset | smooth / 5 / 33 | 46 | True, True | -0.076 | 0.0066 | 1.68 | 1.39 | 3.07 | 647 | 2094 |
| td-preset | ruled / 10 / 17 | 24 | True, True | -0.426 | 0.0000 | 1.09 | 2.06 | 3.15 | 568 | 2115 |
| td-preset | ruled / 10 / 33 | 24 | True, True | -0.191 | 0.0000 | 1.81 | 2.65 | 4.46 | 612 | 2158 |
| td-preset | ruled / 7.5 / 17 | 32 | True, True | -0.373 | 0.0000 | 1.35 | 2.37 | 3.73 | 625 | 2133 |
| td-preset | ruled / 7.5 / 33 | 32 | True, True | -0.138 | 0.0000 | 2.43 | 3.49 | 5.93 | 644 | 2192 |
| td-preset | ruled / 5 / 17 | 46 | True, True | -0.341 | 0.0000 | 1.90 | 2.70 | 4.60 | 617 | 2164 |
| td-preset | ruled / 5 / 33 | 46 | True, True | -0.106 | 0.0000 | 3.41 | 4.70 | 8.11 | 700 | 2249 |
| td-n2.0 | smooth / 10 / 17 | 24 | True, True | -0.282 | 0.0255 | 0.79 | 1.07 | 1.86 | 573 | 2100 |
| td-n2.0 | smooth / 10 / 33 | 24 | True, True | -0.070 | 0.0255 | 1.34 | 1.24 | 2.57 | 607 | 2083 |
| td-n2.0 | smooth / 7.5 / 17 | 32 | True, True | -0.283 | 0.0140 | 0.83 | 1.12 | 1.96 | 589 | 2081 |
| td-n2.0 | smooth / 7.5 / 33 | 32 | True, True | -0.072 | 0.0140 | 1.59 | 1.28 | 2.87 | 625 | 2363 |
| td-n2.0 | smooth / 5 / 17 | 46 | True, True | -0.282 | 0.0066 | 0.90 | 1.22 | 2.12 | 613 | 2100 |
| td-n2.0 | smooth / 5 / 33 | 46 | True, True | -0.070 | 0.0066 | 2.10 | 1.34 | 3.44 | 664 | 2107 |
| td-n2.0 | ruled / 10 / 17 | 24 | True, True | -0.396 | 0.0000 | 1.28 | 2.03 | 3.30 | 568 | 2116 |
| td-n2.0 | ruled / 10 / 33 | 24 | True, True | -0.185 | 0.0000 | 1.89 | 2.70 | 4.60 | 611 | 2158 |
| td-n2.0 | ruled / 7.5 / 17 | 32 | True, True | -0.343 | 0.0000 | 1.36 | 2.35 | 3.72 | 584 | 2133 |
| td-n2.0 | ruled / 7.5 / 33 | 32 | True, True | -0.131 | 0.0000 | 2.40 | 3.75 | 6.15 | 644 | 2193 |
| td-n2.0 | ruled / 5 / 17 | 46 | True, True | -0.311 | 0.0000 | 2.68 | 2.91 | 5.58 | 616 | 2307 |
| td-n2.0 | ruled / 5 / 33 | 46 | True, True | -0.099 | 0.0000 | 3.43 | 4.86 | 8.29 | 700 | 2249 |
| td-n4.0 | smooth / 10 / 17 | 24 | True, True | -0.335 | 0.0255 | 0.83 | 1.28 | 2.11 | 570 | 2081 |
| td-n4.0 | smooth / 10 / 33 | 24 | True, True | -0.081 | 0.0255 | 1.34 | 1.36 | 2.69 | 598 | 2083 |
| td-n4.0 | smooth / 7.5 / 17 | 32 | True, True | -0.336 | 0.0140 | 0.82 | 1.16 | 1.98 | 582 | 2081 |
| td-n4.0 | smooth / 7.5 / 33 | 32 | True, True | -0.083 | 0.0140 | 1.39 | 1.36 | 2.74 | 621 | 2084 |
| td-n4.0 | smooth / 5 / 17 | 46 | True, True | -0.334 | 0.0066 | 1.23 | 1.28 | 2.51 | 605 | 2081 |
| td-n4.0 | smooth / 5 / 33 | 46 | True, True | -0.081 | 0.0066 | 1.78 | 1.58 | 3.36 | 651 | 2094 |
| td-n4.0 | ruled / 10 / 17 | 24 | True, True | -0.449 | 0.0000 | 1.54 | 2.47 | 4.01 | 598 | 2115 |
| td-n4.0 | ruled / 10 / 33 | 24 | True, True | -0.196 | 0.0000 | 1.90 | 2.87 | 4.77 | 611 | 2158 |
| td-n4.0 | ruled / 7.5 / 17 | 32 | True, True | -0.396 | 0.0000 | 1.47 | 2.56 | 4.03 | 585 | 2132 |
| td-n4.0 | ruled / 7.5 / 33 | 32 | True, True | -0.143 | 0.0000 | 2.39 | 3.30 | 5.69 | 645 | 2193 |
| td-n4.0 | ruled / 5 / 17 | 46 | True, True | -0.364 | 0.0000 | 2.05 | 2.69 | 4.74 | 617 | 2164 |
| td-n4.0 | ruled / 5 / 33 | 46 | True, True | -0.111 | 0.0000 | 3.53 | 4.24 | 7.77 | 701 | 2249 |
| td-side0.3 | smooth / 10 / 17 | 24 | True, True | -0.448 | 0.0255 | 0.76 | 1.09 | 1.85 | 556 | 2164 |
| td-side0.3 | smooth / 10 / 33 | 24 | True, True | -0.110 | 0.0255 | 1.26 | 1.16 | 2.42 | 573 | 2082 |
| td-side0.3 | smooth / 7.5 / 17 | 32 | True, True | -0.449 | 0.0140 | 0.79 | 1.14 | 1.94 | 562 | 2081 |
| td-side0.3 | smooth / 7.5 / 33 | 32 | True, True | -0.112 | 0.0140 | 1.33 | 1.25 | 2.58 | 586 | 2082 |
| td-side0.3 | smooth / 5 / 17 | 46 | True, True | -0.448 | 0.0066 | 0.89 | 1.29 | 2.18 | 577 | 2081 |
| td-side0.3 | smooth / 5 / 33 | 46 | True, True | -0.110 | 0.0066 | 1.46 | 1.26 | 2.72 | 611 | 2085 |
| td-side0.3 | ruled / 10 / 17 | 24 | True, True | -0.562 | 0.0000 | 1.06 | 2.04 | 3.10 | 567 | 2117 |
| td-side0.3 | ruled / 10 / 33 | 24 | True, True | -0.225 | 0.0000 | 1.78 | 2.69 | 4.47 | 610 | 2239 |
| td-side0.3 | ruled / 7.5 / 17 | 32 | True, True | -0.509 | 0.0000 | 1.40 | 2.46 | 3.86 | 584 | 2134 |
| td-side0.3 | ruled / 7.5 / 33 | 32 | True, True | -0.171 | 0.0000 | 2.45 | 3.33 | 5.78 | 645 | 2193 |
| td-side0.3 | ruled / 5 / 17 | 46 | True, True | -0.477 | 0.0000 | 2.00 | 2.77 | 4.76 | 615 | 2164 |
| td-side0.3 | ruled / 5 / 33 | 46 | True, True | -0.139 | 0.0000 | 6.17 | 4.78 | 10.95 | 811 | 2312 |
| td-side0.8 | smooth / 10 / 17 | 24 | True, True | -0.120 | 0.0255 | 0.83 | 1.26 | 2.09 | 571 | 2081 |
| td-side0.8 | smooth / 10 / 33 | 24 | True, True | -0.029 | 0.0256 | 1.38 | 1.37 | 2.75 | 600 | 2083 |
| td-side0.8 | smooth / 7.5 / 17 | 32 | True, True | -0.121 | 0.0140 | 0.80 | 1.23 | 2.03 | 581 | 2082 |
| td-side0.8 | smooth / 7.5 / 33 | 32 | True, True | -0.030 | 0.0140 | 1.40 | 1.38 | 2.78 | 618 | 2083 |
| td-side0.8 | smooth / 5 / 17 | 46 | True, True | -0.119 | 0.0066 | 1.01 | 1.35 | 2.36 | 604 | 2082 |
| td-side0.8 | smooth / 5 / 33 | 46 | True, True | -0.029 | 0.0066 | 1.57 | 1.45 | 3.01 | 659 | 2092 |
| td-side0.8 | ruled / 10 / 17 | 24 | True, True | -0.235 | 0.0000 | 1.15 | 1.98 | 3.13 | 567 | 2116 |
| td-side0.8 | ruled / 10 / 33 | 24 | True, True | -0.144 | 0.0000 | 1.82 | 2.73 | 4.54 | 610 | 2303 |
| td-side0.8 | ruled / 7.5 / 17 | 32 | True, True | -0.181 | 0.0000 | 1.43 | 2.54 | 3.97 | 585 | 2133 |
| td-side0.8 | ruled / 7.5 / 33 | 32 | True, True | -0.090 | 0.0000 | 2.43 | 3.16 | 5.59 | 643 | 2192 |
| td-side0.8 | ruled / 5 / 17 | 46 | True, True | -0.149 | 0.0000 | 1.89 | 2.65 | 4.54 | 616 | 2243 |
| td-side0.8 | ruled / 5 / 33 | 46 | True, True | -0.058 | 0.0000 | 3.47 | 4.33 | 7.80 | 702 | 2248 |
| td-nose8-max64 | smooth / 10 / 17 | 24 | True, True | -0.312 | 0.0256 | 0.82 | 1.28 | 2.10 | 572 | 2082 |
| td-nose8-max64 | smooth / 10 / 33 | 24 | True, True | -0.076 | 0.0256 | 1.36 | 1.50 | 2.86 | 604 | 2083 |
| td-nose8-max64 | smooth / 7.5 / 17 | 32 | True, True | -0.313 | 0.0140 | 0.78 | 1.31 | 2.09 | 584 | 2082 |
| td-nose8-max64 | smooth / 7.5 / 33 | 32 | True, True | -0.078 | 0.0140 | 1.41 | 1.47 | 2.89 | 621 | 2108 |
| td-nose8-max64 | smooth / 5 / 17 | 46 | True, True | -0.311 | 0.0066 | 0.95 | 1.48 | 2.43 | 610 | 2100 |
| td-nose8-max64 | smooth / 5 / 33 | 46 | True, True | -0.076 | 0.0066 | 1.57 | 1.55 | 3.13 | 643 | 2091 |
| td-nose8-max64 | ruled / 10 / 17 | 24 | True, True | -0.444 | 0.0000 | 1.15 | 2.15 | 3.30 | 570 | 2118 |
| td-nose8-max64 | ruled / 10 / 33 | 24 | True, True | -0.209 | 0.0000 | 2.14 | 3.02 | 5.16 | 610 | 2175 |
| td-nose8-max64 | ruled / 7.5 / 17 | 32 | True, True | -0.382 | 0.0000 | 1.45 | 2.70 | 4.15 | 625 | 2133 |
| td-nose8-max64 | ruled / 7.5 / 33 | 32 | True, True | -0.147 | 0.0000 | 2.40 | 3.48 | 5.87 | 643 | 2193 |
| td-nose8-max64 | ruled / 5 / 17 | 46 | True, True | -0.345 | 0.0000 | 1.94 | 2.98 | 4.93 | 616 | 2179 |
| td-nose8-max64 | ruled / 5 / 33 | 46 | True, True | -0.110 | 0.0000 | 3.65 | 4.67 | 8.32 | 812 | 2313 |
| td-tail=max | smooth / 10 / 17 | 24 | True, True | -0.312 | 0.0255 | 0.91 | 1.14 | 2.05 | 570 | 2081 |
| td-tail=max | smooth / 10 / 33 | 24 | True, True | -0.076 | 0.0255 | 1.42 | 1.29 | 2.71 | 606 | 2085 |
| td-tail=max | smooth / 7.5 / 17 | 32 | True, True | -0.313 | 0.0140 | 0.81 | 1.22 | 2.03 | 585 | 2081 |
| td-tail=max | smooth / 7.5 / 33 | 32 | True, True | -0.078 | 0.0140 | 1.53 | 1.29 | 2.82 | 630 | 2086 |
| td-tail=max | smooth / 5 / 17 | 46 | True, True | -0.311 | 0.0066 | 0.95 | 1.40 | 2.35 | 607 | 2081 |
| td-tail=max | smooth / 5 / 33 | 46 | True, True | -0.076 | 0.0066 | 1.89 | 1.33 | 3.22 | 652 | 2082 |
| td-tail=max | ruled / 10 / 17 | 24 | True, True | -0.416 | 0.0000 | 1.14 | 1.90 | 3.04 | 569 | 2118 |
| td-tail=max | ruled / 10 / 33 | 24 | True, True | -0.181 | 0.0000 | 2.05 | 2.80 | 4.84 | 611 | 2157 |
| td-tail=max | ruled / 7.5 / 17 | 32 | True, True | -0.367 | 0.0000 | 1.43 | 2.45 | 3.88 | 584 | 2131 |
| td-tail=max | ruled / 7.5 / 33 | 32 | True, True | -0.132 | 0.0000 | 2.40 | 3.26 | 5.66 | 643 | 2190 |
| td-tail=max | ruled / 5 / 17 | 46 | True, True | -0.338 | 0.0000 | 2.01 | 2.95 | 4.96 | 614 | 2305 |
| td-tail=max | ruled / 5 / 33 | 46 | True, True | -0.103 | 0.0000 | 3.75 | 4.34 | 8.09 | 700 | 2246 |
| td-maxx0.25 | smooth / 10 / 17 | 24 | True, True | -0.307 | 0.0612 | 0.73 | 1.19 | 1.92 | 570 | 2081 |
| td-maxx0.25 | smooth / 10 / 33 | 24 | True, True | -0.072 | 0.0613 | 1.29 | 1.24 | 2.53 | 605 | 2084 |
| td-maxx0.25 | smooth / 7.5 / 17 | 31 | True, True | -0.311 | 0.0353 | 0.81 | 1.17 | 1.98 | 582 | 2081 |
| td-maxx0.25 | smooth / 7.5 / 33 | 31 | True, True | -0.074 | 0.0353 | 1.38 | 1.28 | 2.66 | 624 | 2236 |
| td-maxx0.25 | smooth / 5 / 17 | 45 | True, True | -0.355 | 0.0138 | 1.94 | 2.02 | 3.97 | 576 | 2085 |
| td-maxx0.25 | smooth / 5 / 33 | 45 | True, True | -0.116 | 0.0138 | 3.20 | 2.01 | 5.21 | 599 | 2084 |
| td-maxx0.25 | ruled / 10 / 17 | 24 | True, True | -0.452 | 0.0000 | 1.06 | 2.18 | 3.24 | 566 | 2117 |
| td-maxx0.25 | ruled / 10 / 33 | 24 | True, True | -0.217 | 0.0000 | 1.87 | 2.67 | 4.54 | 610 | 2158 |
| td-maxx0.25 | ruled / 7.5 / 17 | 31 | True, True | -0.391 | 0.0000 | 1.46 | 2.37 | 3.83 | 582 | 2130 |
| td-maxx0.25 | ruled / 7.5 / 33 | 31 | True, True | -0.156 | 0.0000 | 2.33 | 3.54 | 5.87 | 640 | 2188 |
| td-maxx0.25 | ruled / 5 / 17 | 45 | True, True | -0.354 | 0.0000 | 2.13 | 2.81 | 4.94 | 623 | 2162 |
| td-maxx0.25 | ruled / 5 / 33 | 45 | True, True | -0.119 | 0.0000 | 3.49 | 4.23 | 7.71 | 706 | 2244 |
| td-maxx0.6 | smooth / 10 / 17 | 24 | True, True | -0.312 | 0.0010 | 0.81 | 1.12 | 1.93 | 569 | 2081 |
| td-maxx0.6 | smooth / 10 / 33 | 24 | True, True | -0.077 | 0.0010 | 1.32 | 1.33 | 2.65 | 599 | 2083 |
| td-maxx0.6 | smooth / 7.5 / 17 | 31 | True, True | -0.312 | 0.0006 | 0.82 | 1.19 | 2.01 | 580 | 2081 |
| td-maxx0.6 | smooth / 7.5 / 33 | 31 | True, True | -0.077 | 0.0006 | 1.51 | 1.38 | 2.90 | 621 | 2085 |
| td-maxx0.6 | smooth / 5 / 17 | 46 | True, True | -0.312 | 0.0002 | 0.91 | 1.23 | 2.14 | 606 | 2356 |
| td-maxx0.6 | smooth / 5 / 33 | 46 | True, True | -0.077 | 0.0002 | 1.66 | 1.33 | 2.99 | 643 | 2091 |
| td-maxx0.6 | ruled / 10 / 17 | 24 | True, True | -0.384 | 0.0000 | 1.11 | 1.96 | 3.07 | 566 | 2116 |
| td-maxx0.6 | ruled / 10 / 33 | 24 | True, True | -0.149 | 0.0000 | 1.91 | 2.70 | 4.61 | 610 | 2158 |
| td-maxx0.6 | ruled / 7.5 / 17 | 31 | True, True | -0.356 | 0.0000 | 1.44 | 2.41 | 3.84 | 582 | 2130 |
| td-maxx0.6 | ruled / 7.5 / 33 | 31 | True, True | -0.120 | 0.0000 | 2.31 | 3.18 | 5.49 | 639 | 2188 |
| td-maxx0.6 | ruled / 5 / 17 | 46 | True, True | -0.332 | 0.0000 | 2.05 | 2.78 | 4.83 | 677 | 2164 |
| td-maxx0.6 | ruled / 5 / 33 | 46 | True, True | -0.096 | 0.0000 | 3.71 | 5.46 | 9.17 | 702 | 2248 |
| nw-preset | smooth / 10 / 17 | 25 | True, True | -0.154 | 0.1371 | 0.82 | 1.44 | 2.25 | 580 | 2083 |
| nw-preset | smooth / 10 / 33 | 25 | True, True | -0.016 | 0.1374 | 1.53 | 1.68 | 3.21 | 612 | 2085 |
| nw-preset | smooth / 7.5 / 17 | 34 | True, True | -0.194 | 0.0713 | 1.28 | 1.77 | 3.04 | 597 | 2229 |
| nw-preset | smooth / 7.5 / 33 | 34 | True, True | -0.056 | 0.0715 | 1.76 | 1.68 | 3.44 | 621 | 2090 |
| nw-preset | smooth / 5 / 17 | 48 | True, True | -0.174 | 0.0336 | 0.97 | 1.55 | 2.52 | 623 | 2083 |
| nw-preset | smooth / 5 / 33 | 48 | True, True | -0.037 | 0.0336 | 1.92 | 1.67 | 3.59 | 675 | 2090 |
| nw-preset | ruled / 10 / 17 | 25 | True, True | -0.337 | 0.0000 | 0.91 | 1.88 | 2.80 | 554 | 2129 |
| nw-preset | ruled / 10 / 33 | 25 | True, True | -0.199 | 0.0000 | 1.54 | 2.88 | 4.42 | 587 | 2140 |
| nw-preset | ruled / 7.5 / 17 | 34 | True, True | -0.275 | 0.0000 | 1.14 | 2.22 | 3.36 | 598 | 2124 |
| nw-preset | ruled / 7.5 / 33 | 34 | True, True | -0.138 | 0.0000 | 2.11 | 3.25 | 5.36 | 617 | 2169 |
| nw-preset | ruled / 5 / 17 | 48 | True, True | -0.230 | 0.0000 | 1.64 | 2.86 | 4.50 | 590 | 2143 |
| nw-preset | ruled / 5 / 33 | 48 | True, True | -0.092 | 0.0000 | 3.47 | 4.34 | 7.81 | 659 | 2212 |
| nw-waist14-housing64 | smooth / 10 / 17 | 25 | True, True | -0.164 | 0.1747 | 0.88 | 1.77 | 2.65 | 581 | 2085 |
| nw-waist14-housing64 | smooth / 10 / 33 | 25 | True, True | -0.025 | 0.1747 | 1.54 | 1.92 | 3.46 | 609 | 2087 |
| nw-waist14-housing64 | smooth / 7.5 / 17 | 34 | True, True | -0.178 | 0.0912 | 0.95 | 1.69 | 2.64 | 598 | 2086 |
| nw-waist14-housing64 | smooth / 7.5 / 33 | 34 | True, True | -0.040 | 0.0912 | 1.67 | 1.85 | 3.52 | 635 | 2091 |
| nw-waist14-housing64 | smooth / 5 / 17 | 48 | True, True | -0.182 | 0.0427 | 0.98 | 1.64 | 2.62 | 622 | 2085 |
| nw-waist14-housing64 | smooth / 5 / 33 | 48 | True, True | -0.044 | 0.0427 | 1.78 | 1.87 | 3.65 | 671 | 2095 |
| nw-waist14-housing64 | ruled / 10 / 17 | 25 | True, True | -0.357 | 0.0000 | 0.86 | 2.22 | 3.09 | 561 | 2114 |
| nw-waist14-housing64 | ruled / 10 / 33 | 25 | True, True | -0.220 | 0.0000 | 1.52 | 3.14 | 4.66 | 587 | 2139 |
| nw-waist14-housing64 | ruled / 7.5 / 17 | 34 | True, True | -0.283 | 0.0000 | 1.12 | 2.68 | 3.80 | 573 | 2127 |
| nw-waist14-housing64 | ruled / 7.5 / 33 | 34 | True, True | -0.146 | 0.0000 | 2.41 | 3.64 | 6.06 | 618 | 2169 |
| nw-waist14-housing64 | ruled / 5 / 17 | 48 | True, True | -0.233 | 0.0000 | 1.67 | 3.27 | 4.94 | 590 | 2143 |
| nw-waist14-housing64 | ruled / 5 / 33 | 48 | True, True | -0.096 | 0.0000 | 3.36 | 4.64 | 8.00 | 658 | 2212 |
| nw-housingh60-body18 | smooth / 10 / 17 | 25 | True, True | -0.178 | 0.2962 | 0.81 | 1.47 | 2.28 | 571 | 2084 |
| nw-housingh60-body18 | smooth / 10 / 33 | 25 | True, True | -0.040 | 0.2959 | 1.32 | 1.73 | 3.06 | 599 | 2086 |
| nw-housingh60-body18 | smooth / 7.5 / 17 | 34 | True, True | -0.170 | 0.1580 | 0.86 | 1.62 | 2.48 | 586 | 2228 |
| nw-housingh60-body18 | smooth / 7.5 / 33 | 34 | True, True | -0.035 | 0.1585 | 1.50 | 1.83 | 3.33 | 626 | 2085 |
| nw-housingh60-body18 | smooth / 5 / 17 | 48 | True, True | -0.198 | 0.0740 | 0.89 | 1.62 | 2.51 | 604 | 2084 |
| nw-housingh60-body18 | smooth / 5 / 33 | 48 | True, True | -0.058 | 0.0743 | 1.67 | 1.87 | 3.55 | 655 | 2094 |
| nw-housingh60-body18 | ruled / 10 / 17 | 25 | True, True | -0.336 | 0.0000 | 0.92 | 1.97 | 2.88 | 555 | 2109 |
| nw-housingh60-body18 | ruled / 10 / 33 | 25 | True, True | -0.199 | 0.0000 | 1.69 | 2.73 | 4.42 | 587 | 2140 |
| nw-housingh60-body18 | ruled / 7.5 / 17 | 34 | True, True | -0.269 | 0.0000 | 1.16 | 2.25 | 3.41 | 568 | 2141 |
| nw-housingh60-body18 | ruled / 7.5 / 33 | 34 | True, True | -0.132 | 0.0000 | 2.36 | 3.47 | 5.83 | 617 | 2169 |
| nw-housingh60-body18 | ruled / 5 / 17 | 48 | True, True | -0.226 | 0.0000 | 1.68 | 3.16 | 4.84 | 591 | 2143 |
| nw-housingh60-body18 | ruled / 5 / 33 | 48 | True, True | -0.089 | 0.0000 | 3.45 | 4.58 | 8.04 | 660 | 2212 |
| nw-housingl110 | smooth / 10 / 17 | 25 | True, True | -0.126 | 0.5523 | 0.91 | 2.00 | 2.91 | 586 | 2086 |
| nw-housingl110 | smooth / 10 / 33 | 25 | True, True | +0.009 | 0.5376 | 1.81 | 2.68 | 4.50 | 629 | 2090 |
| nw-housingl110 | smooth / 7.5 / 17 | 34 | True, True | -0.160 | 0.4039 | 1.05 | 1.73 | 2.77 | 599 | 2085 |
| nw-housingl110 | smooth / 7.5 / 33 | 34 | True, True | -0.021 | 0.3973 | 1.86 | 4.90 | 6.76 | 632 | 2087 |
| nw-housingl110 | smooth / 5 / 17 | 48 | True, True | -0.190 | 0.2319 | 1.03 | 2.00 | 3.03 | 623 | 2086 |
| nw-housingl110 | smooth / 5 / 33 | 48 | True, True | -0.057 | 0.2291 | 2.07 | 2.38 | 4.45 | 668 | 2091 |
| nw-housingl110 | ruled / 10 / 17 | 25 | True, True | -0.363 | 0.0000 | 0.86 | 1.96 | 2.82 | 553 | 2108 |
| nw-housingl110 | ruled / 10 / 33 | 25 | True, True | -0.225 | 0.0000 | 1.58 | 2.83 | 4.40 | 577 | 2132 |
| nw-housingl110 | ruled / 7.5 / 17 | 34 | True, True | -0.288 | 0.0000 | 1.19 | 2.39 | 3.59 | 565 | 2118 |
| nw-housingl110 | ruled / 7.5 / 33 | 34 | True, True | -0.150 | 0.0000 | 2.40 | 3.36 | 5.76 | 606 | 2159 |
| nw-housingl110 | ruled / 5 / 17 | 48 | True, True | -0.240 | 0.0000 | 1.84 | 2.84 | 4.68 | 583 | 2137 |
| nw-housingl110 | ruled / 5 / 33 | 48 | True, True | -0.102 | 0.0000 | 3.30 | 4.48 | 7.78 | 643 | 2196 |
| nw-front0.30-waist0.35 | smooth / 10 / 17 | 25 | True, True | -0.203 | 0.0618 | 0.85 | 1.68 | 2.53 | 581 | 2085 |
| nw-front0.30-waist0.35 | smooth / 10 / 33 | 25 | True, True | -0.067 | 0.0619 | 1.68 | 1.98 | 3.66 | 619 | 2086 |
| nw-front0.30-waist0.35 | smooth / 7.5 / 17 | 34 | True, True | -0.195 | 0.0319 | 1.13 | 1.79 | 2.91 | 598 | 2085 |
| nw-front0.30-waist0.35 | smooth / 7.5 / 33 | 34 | True, True | -0.057 | 0.0319 | 1.78 | 2.19 | 3.97 | 633 | 2089 |
| nw-front0.30-waist0.35 | smooth / 5 / 17 | 48 | True, True | -0.206 | 0.0153 | 1.04 | 2.08 | 3.12 | 626 | 2086 |
| nw-front0.30-waist0.35 | smooth / 5 / 33 | 48 | True, True | -0.071 | 0.0153 | 1.85 | 2.05 | 3.91 | 663 | 2096 |
| nw-front0.30-waist0.35 | ruled / 10 / 17 | 25 | True, True | -0.288 | 0.0000 | 1.03 | 2.21 | 3.23 | 561 | 2110 |
| nw-front0.30-waist0.35 | ruled / 10 / 33 | 25 | True, True | -0.151 | 0.0000 | 1.83 | 2.98 | 4.81 | 590 | 2141 |
| nw-front0.30-waist0.35 | ruled / 7.5 / 17 | 34 | True, True | -0.235 | 0.0000 | 1.28 | 2.98 | 4.27 | 573 | 2124 |
| nw-front0.30-waist0.35 | ruled / 7.5 / 33 | 34 | True, True | -0.097 | 0.0000 | 2.76 | 4.49 | 7.24 | 676 | 2170 |
| nw-front0.30-waist0.35 | ruled / 5 / 17 | 48 | True, True | -0.209 | 0.0000 | 1.83 | 3.18 | 5.01 | 597 | 2289 |
| nw-front0.30-waist0.35 | ruled / 5 / 33 | 48 | True, True | -0.072 | 0.0000 | 3.85 | 4.68 | 8.53 | 665 | 2216 |


## 4. Preset build + GLB (final kernel, chosen constants)

Five fresh-interpreter runs per preset (`--one`, `OMP_NUM_THREADS=1`, no RLIMIT, no arena cap):

| Preset | build s | GLB s | total s | RSS MiB | VmPeak MiB | valid |
| --- | --- | --- | --- | --- | --- | --- |
| td-preset | 1.99 | 2.76 | 4.75 | 608 | 2175 | True |
| td-preset | 1.81 | 2.71 | 4.52 | 608 | 2175 | True |
| td-preset | 2.02 | 2.72 | 4.75 | 667 | 2158 | True |
| td-preset | 1.93 | 2.76 | 4.69 | 609 | 2158 | True |
| td-preset | 1.82 | 2.79 | 4.61 | 610 | 2158 | True |
| nw-preset | 1.67 | 2.66 | 4.33 | 586 | 2139 | True |
| nw-preset | 1.73 | 4.37 | 6.10 | 586 | 2139 | True |
| nw-preset | 1.95 | 4.05 | 6.00 | 626 | 2140 | True |
| nw-preset | 2.32 | 2.98 | 5.30 | 586 | 2139 | True |
| nw-preset | 1.49 | 2.67 | 4.16 | 587 | 2139 | True |

td-preset: median 4.69 s, max 4.75 s.
nw-preset: median 5.30 s, max 6.10 s.All 10: median **4.72 s**, max 6.10 s. Invariant 7 budget is build+verify+GLB ≤ 12 s and ≤ 1.0 GiB
peak RSS. Build+GLB takes ≤ 6.1 s and ≤ 667 MiB RSS, leaving room for D5 verification.

Cost split (profiling, teardrop / narrow waist): loft 0.7–0.9 s, cuts 0.3–0.8 s (single boolean),
STEP 0.25 s, GLB 2.1–3.3 s. GLB tessellation (`local.export_preview`, 0.05 mm) is now the largest
cost, and it is outside D2's files.

## 5. F1: lofted bodies and the 1536 MiB `RLIMIT_AS`

`d2_loft_spike.py --diag`. Each run is a fresh child, either unlimited or through the same trusted
wrapper `ProcessRunner` uses (`backend/_memory_entry.py 1610612736 …`), with and without
`MALLOC_ARENA_MAX=2` (diagnostic only; `service.py` is unchanged). The chosen constants are used.
This ran **before** the single-boolean cut change (§ 6). The cut change does not change the
allocator behaviour; the final-kernel matrix shows the same VmPeak range of 2132–2469 MiB.

| RLIMIT_AS | MALLOC_ARENA_MAX | case | runs | outcomes | VmPeak MiB | RSS MiB | build+GLB s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| none | unset | wedge-v1 | 2 | ok, ok | 1563, 1627 | 519 | 1.65, 1.21 |
| none | unset | td-preset | 2 | ok, ok | 2175 | 608 | 11.99, 5.59 |
| none | unset | nw-preset | 2 | ok, ok | 2139 | 585, 587 | 5.54, 5.37 |
| none | 2 | wedge-v1 | 2 | ok, ok | 1179 | 518, 520 | 1.44, 0.95 |
| none | 2 | td-preset | 2 | ok, ok | 1262 | 607, 608 | 6.94, 5.93 |
| none | 2 | nw-preset | 2 | ok, ok | 1243, 1244 | 585, 587 | 5.61, 5.93 |
| 1536 MiB | unset | wedge-v1 | 3 | hang ×3 (killed at 120 s) | — | — | — |
| 1536 MiB | unset | td-preset | 3 | SIGSEGV ×3 | — | — | — |
| 1536 MiB | unset | nw-preset | 3 | hang ×3 (killed at 120 s) | — | — | — |
| 1536 MiB | 2 | wedge-v1 | 3 | ok ×3 | 1179 | 518, 519 | 0.74, 0.70, 0.77 |
| 1536 MiB | 2 | td-preset | 3 | ok ×3 | 1262 | 605–608 | 7.28, 6.45, 5.92 |
| 1536 MiB | 2 | nw-preset | 3 | ok ×3 | 1243, 1244 | 586 | 5.13, 5.11, 6.48 |

Findings:

- **Lofted bodies make F1 worse.** Without an arena cap, VmPeak is **+510 to +900 MiB** over the v1
  wedge (2132–2469 vs 1563–1627 MiB across the matrix) for only +60 to +150 MiB of RSS. Each worker
  thread that allocates during lofts and booleans gets its own glibc arena. On this 16-CPU host,
  lofted builds will fail under `ProcessRunner` every time, not intermittently.
- `MALLOC_ARENA_MAX=2` reduces lofted VmPeak to 1243–1262 MiB (274–293 MiB headroom under 1536)
  and passes 9/9 under the real limit. RSS and timing are unchanged.
- D2 changes no service code. The worker still builds only v1, so the existing 8 F1 tests are
  unaffected by this branch. **Before D3/D4 lofts go through the worker (D7), the F1 fix must land**
  (proposal unchanged from D0: `MALLOC_ARENA_MAX='2'` in the worker env in `service.py`, with a
  regression test; owner still open). The 11.99 s teardrop outlier (first lofted run in that
  container) is recorded as measured. The final 5-run preset timing (§ 4) maxed at 6.10 s.

## 6. Failures and incidents during the spike

- **Spline crown** (first kernel draft): unreliable volumes and a hang in volume integration (§ 2).
  Replaced by the polyline crown before the matrix.
- **First matrix run** (sequential pocket/bore cuts, as in v1 `geometry.py`): same validity,
  volume and bounds results; slower (ruled / 10 / 33: median 5.44 s, max 6.58 s; ruled / 7.5 / 33:
  max 9.72 s). The background shell was stopped by the host's low-memory reaper. The container kept
  running and 168 of 169 rows were recovered from its log.
  Profiling then showed that the three sequential booleans cost 1.3–2.9 s. One boolean with all
  tools gives the same volume to 4 decimals and the same validity in about half the time. The
  kernel now does that, and the full matrix was **re-run** on the final kernel: § 3 (169/169 rows,
  0 errors).
- Kernel test bugs fixed during TDD: `Wire.Edges()` order is not construction order (the crown is
  now checked by vertex height), and an unequal-wheel wedge variant broke D1's rear-bore/pocket
  rule (the test now uses front 22 / rear 18 mm).

## 7. Tests

Commands from the repo root, on the rebuilt image (no mounts):

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name d2-tests --memory 3g --network none \
  --tmpfs /tmp:rw,exec,size=1g,mode=1777 mirum-verify:d2 \
  python -m pytest -q -p no:cacheprovider -rs backend/tests/test_dragster_kernel.py \
  backend/tests/test_dragster_style_wedge_v2.py
# 27 passed, 0 skipped (19 kernel + 8 wedge v2)

MSYS_NO_PATHCONV=1 docker run --rm --name d2-suite --memory 3g --network none \
  --tmpfs /tmp:rw,exec,size=1g,mode=1777 mirum-verify:d2 \
  python -m pytest -q -p no:cacheprovider -rs backend/tests/test_dragster_*.py frontend/test_dragster_*.py
# three runs: 1094 passed / 8 failed, 1095 / 7, 1095 / 7; 0 skipped in each
```

Failure names were listed for the second and third runs, and every one is in the known F1 set
(D0 § 4): `test_dragster_integrated.py::{test_native_delivery_and_binding,
test_required_publication_failure[step|glb|spec|report], test_concurrent_native_attempts_remain_distinct,
test_changed_native_artifact_is_not_published}`. The third run had
`test_dragster_api.py::test_real_api_downloads_and_revision` and not `[report]`. The first run's 8
failures were not listed by name; 8 is the size of the F1 set. All of these go through
`service.generate` (the v1 worker, unchanged by D2), so CI is the authority for them.

TDD record: the kernel tests failed at collection with no `kernel` module before `kernel.py` was
written. The wedge v2 tests run against D1's stub gave 6 failed / 2 passed (the 2 passes are the
registry and lazy-import checks).
