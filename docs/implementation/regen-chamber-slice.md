# RegenChamberSpec vertical slice (PicoGK)

Status: implemented on branch `engineering/regen-chamber`, uncommitted. Not wired into anything.

## What it is

A deterministic, no-LLM path from a typed spec to a verified mesh of a regeneratively cooled chamber
shape: a revolved wall with helical coolant channels, two manifold rings and two radial ports.

- `backend/engineering/regen_spec.py`: strict frozen pydantic `RegenChamberSpec`, `inner_radius`, `canonical_spec`, `specification_digest`. Pure Python, no cadquery or compiler import.
- `picogk-builder/`: fixed C# builder (`builder <spec.json> <out.stl>`) that renders a validated spec with PicoGK 2.3.0. No spec string is ever turned into code.
- `backend/engineering/regen_mesh.py`: numpy/scipy verifier (clean, manifold, volume, bbox, plane slices).
- `backend/engineering/regen_cli.py`: `regen_cli <spec.json> <outdir>`.

## What it is not

- Geometry only. No gas dynamics, thermal, structural or standards evaluation. `physics` and `standards` are always `not_evaluated`; `engineering_certified` is always `false`.
- The contour is a simplified C1 cosine blend (chamber radius, then a cosine blend to the throat, then a cosine blend to the exit). It is not an optimized nozzle.
- No STEP export (`step_export: not_available`). No HTTP route (hosting decision pending). Not wired into the pipeline, `main.py`, the worker, or Fly.

## Geometry

z runs from 0 to L = chamber_length + convergent_length + divergent_length. Inner radius is the chamber radius up to chamber_length, then `Rt + (Rc-Rt)*0.5*(1+cos(pi t))` over the convergent, then `Rt + (Re-Rt)*0.5*(1-cos(pi t))` over the divergent. Slope is zero at both joins; max slope is `pi/2 * dR/len`.
Wall is inner radius to inner radius + wall_thickness. Channel c is a helix at r = inner(z) + wall/2, theta = c*2pi/N + z*twist, for z in [end_margin, L-end_margin], swept with round beams of channel_radius. Manifolds are rings of manifold_radius at those two z values at mid-wall radius; each has one radial port at theta=0 from mid-wall out to 3 mm past the outer surface.

## Envelope (validator rules)

Rejected with a message: throat >= chamber or exit; max slope > tan 35 deg; channel diameter < 4 voxels; normal wall `(wall/2 - rc)*cos(atan(max slope))` < m; throat rib `2*pi*(Rt+wall/2)/N - 2*rc/cos(beta)` < m (tan beta = twist*(Rt+wall/2)); manifold clearance `wall/2 - rm` < m; end_margin < 2*rm+1; forward ring not fully on the chamber cylinder; aft ring closer than `rm + wall` (beyond its own radius) to the throat plane; voxel outside [0.1, 1.0]; bounding voxels `(2(Rmax+wall+5)/v)^2 (L+4)/v` > 3e8; channel count outside [4, 256]. Here m = max(0.5 mm, 4 voxels).

Interpretations to confirm with the owner:
- "channel radius < 4 voxels" is implemented as channel **diameter** < 4 voxels. Read literally (radius >= 1.6 mm at 0.4 mm voxel) the requested example rc=1.0 at 0.4 mm could not pass.
- The example uses wall 7.0 and manifold radius 1.5: with wall 5-6 mm and a 1.6 mm manifold the 4-voxel clearance rule fails at 0.4 mm voxel.
- The "manifold in the blends near the throat" rule is implemented as the two ring conditions above.
- Rib uses arc length `2*pi*r/N` as specified; the true chord is slightly shorter (about 0.2 percent at N=32).

Valid example (tests and E2E): chamber R 35, length 40; convergent 50; throat R 20; divergent 80; exit R 45; wall 7; N 32; rc 1.0; twist 0.012 rad/mm; manifold r 1.5; end margin 8; voxel 0.4.
The spike parameters (N=48, rc=1.1, throat 15, wall 5) are rejected (tests): their throat rib is about 0.04-0.09 mm by hand against a 1.6 mm minimum (0.09 without the helix factor).

## Build and run

```
cp spike-picogk/avast-ca.pem picogk-builder/avast-ca.pem     # machine-local, gitignored
docker build -f picogk-builder/Dockerfile -t picogk-regen:slice .   # needs picogk-runtime:26.2
docker run --rm --name picogk-regen-x --network none -v <dir with spec.json>:/in:ro picogk-regen:slice /in/spec.json /out/o
```

Exit 0 accepted (writes `cleaned.stl`, `report.json`, `spec.canonical.json`); 2 rejected (invalid spec, or mesh checks failed; writes only `rejected_report.json` for mesh failures); 1 unavailable (builder crash, 600 s timeout, malformed output, missing runtime `.so`; writes nothing); 3 a `--prebuilt-stl` mesh passed the checks but is not accepted because the builder did not make it (writes only `prebuilt_report.json`). `report.json` is written last. `LD_LIBRARY_PATH` is set only in the builder subprocess environment, never globally. `--prebuilt-stl PATH` verifies an existing STL (test hook).

Host tests (no PicoGK):
`uv run --system-certs --python 3.11 --with pydantic --with numpy --with scipy --with pytest python -m pytest backend/tests/test_regen_spec.py backend/tests/test_regen_mesh.py backend/tests/test_regen_cli.py`

## Checks

components (extra components fail; only |signed volume| < 1e-3 mm3 specks are removed and recorded), edge-manifold (every edge exactly twice), winding (no repeated directed edge, signed volume > 0), bbox within 2 voxels of the spec, volume within **2 percent** of an analytic estimate (wall revolve integral minus helical channels, manifold tori and ports; overlaps and end caps neglected; tolerance fixed, not tuned), and 7 z-plane slices (6 evenly spaced strictly between the manifolds, plus the throat): exactly N channel loops, min hot wall, min cold wall, min rib each >= m - 1 voxel.
**Limitation:** slices are in z planes, so hot/cold wall values are in-plane distances, not surface-normal thickness; rib is also in-plane. Point-set distances are sampled (5 points per slice segment).

## Measured E2E results (Docker `--network none`, image `picogk-regen:slice`, 16-core host, 2026-11-01)

| Case | Result |
|---|---|
| (a) valid spec, 0.4 mm | exit 0. builder 2,173,664 tris, 10-12 s, peak ~600 MB; 18 degenerates dropped, 0 specks; volume 242,610.7 mm3 vs analytic 241,228.2 (+0.57 percent); bbox error 0.005 mm; 32 loops at all 7 stations; min hot wall 2.41, cold 2.40, rib 2.57 mm (threshold 1.2) |
| (b) 4 runs (3 plain + `--cpuset-cpus 0-3`) plus the (a) run | cleaned `e1ade11bd1b3bfc23be0ca6ecccbf3cd9412ef6413cb987c191db3d6378e0758` and raw `2a8206d8bc5be7b18c09fb02d9217a321bbb70604df0152263333dd048dee6e3` identical in all (a)+4 runs (raw hash of (a) not printed; its cleaned hash matched). Builder 10.3-14.3 s, peak 485-605 MB. runtime .so sha256 `44aa0a96a4f25b09...` |
| (c) spike parameters | exit 2 `Contour max slope 0.785 exceeds tan(35 deg)`, builder not run (also with a nonexistent builder path), no output dir contents |
| (d) fault-injected real mesh (via `--prebuilt-stl`) | 100 triangles removed: exit 2 (40 edges not used twice). All winding flipped: exit 2 (signed volume -242,610). 1000 triangles flipped: exit 2 (144 directed-edge conflicts). 5 mm cube in the air: exit 2 (extra component). Inverted cube inside wall (sealed void): exit 2 (extra component). 0.05 mm tetra speck only: passed, 1 speck removed and recorded (ran as exit 0 before the hook changed to exit 3; this case was not rerun, but an unmodified cleaned mesh through the hook now exits 3) |
| (d2) real builder output with spike parameters, validation bypassed (`model_construct`) | spike (convergent 40): rejected; throat stations show 2 channel loops instead of 48 (channels merged), throat rib 0.625 mm, 76 specks removed, 40 degenerates. Spike with convergent 60: rejected; 4 unexpected components, 20 loops instead of 48 at z=101.7, rib 0.10 mm. The valid spec produced 0 specks. This is consistent with the thin-rib explanation of the spike's specks but is not proof (one valid sample, different contour) |
| (e) valid spec, 0.2 mm | exit 0 (one run only, determinism not tested at 0.2). raw `72a82be8fb5e9e16d3c435162fae7c95475431f55ebefd025defe8c458e41ff9`, cleaned `e600cb0f6e6fcd301d0f019399603508a7266e13fec853a5b7567ce6167b1774`; 8,729,192 tris, 1600 degenerates, builder 47.6 s, peak 2.47 GB; volume error +0.25 percent; whole container run 84 s |

Host tests: 72 passed (`test_regen_spec`, `test_regen_mesh`, `test_regen_cli`, plus the existing `test_engineering_spec` unchanged).

## Limitations

- No STEP, no route, no physics, not in the pipeline or Fly.
- Base images (`dotnet/sdk:9.0`, `debian:trixie-slim`) and apt packages are unpinned. Hashes are valid only for the same cached images on one machine; Linux and Windows meshes differ (see `spike-picogk/RESULTS.md`).
- PicoGKRuntime PR #115 (bool marshalling) is not applied; this builder does not call the affected API.
- The builder reflects on the private field `Library.m_oTimerMemCheck` of NuGet PicoGK 2.3.0 to stop the memory timer (PicoGKRuntime #31). It fails loudly if the field is missing, but a PicoGK upgrade may break it.
- Soak (crash rate) was not re-run for this builder; the timer is always stopped as in the spike's 400-run clean arm.
- Wall checks are in-plane, not surface-normal. The analytic volume neglects overlaps (about 0.5 percent observed error).
- Only the stated envelope was exercised (one valid spec at two voxel sizes).
