# PicoGK on linux-x64: spike results (2026-11-01)

Stack: runtime `leap71/PicoGKRuntime@0f26321`, NuGet `PicoGK` 2.3.0, gcc:15 (GCC 15.3.0, Debian 13.7), .NET 9, `--network none`, no X server. Test part: regen nozzle in `Program.cs`. Hash = first 16 hex of the STL's SHA-256.

## Build

| Item | Result |
|---|---|
| `picogk.26.2.so` | 15,472,592 B, sha256 `44aa0a96a4f25b09...`, no missing libs in `ldd` |
| Runtime build | 1155 s at 3 jobs (includes re-clone); no-cache rebuild 821 s |
| Rebuild reproducibility | 0 differing bytes in the `.so`; same STL hash |
| Images | builder `picogk-runtime:26.2` 3.03 GB; run image `picogk-spike:26.2` 547 MB |
| 6 compile jobs | Docker engine died on the 8 GB VM. OOM is inferred: at 3 jobs available memory fell to 316 MB; no kernel log was captured |

## Determinism (Linux)

| Voxel | Hash | Volume mm3 | Tris | Runs | Time, 16 / 4 / 1 cores | Peak, 16 cores |
|---|---|---|---|---|---|---|
| 0.4 mm | `66988c7e96c2deab` | 138073.453 | 2,448,760 | 7 in matrix and rebuild, all identical | 12-14 / 24 / 97 s | ~570 MB |
| 0.2 mm | `dce8fe9270b0f8d7` | 137087.906 | 9,836,092 | 6, all identical | 57-159 / 85 / 169 s | ~2.4 GB |

Timings are noisy (57-159 s for the same 0.2 mm run; cause not investigated), so don't use them as benchmarks.

## Linux vs Windows

Fresh Windows runs reproduced the reference hashes (`ffd4ef797b3f2578`, `ce883e11e606021e`).

| Voxel | Volume, tris, STL size, header | Facets differing in file order | Max vertex delta |
|---|---|---|---|
| 0.4 mm | identical | 33,551 of 2,448,760 (1.37%) | 3.8e-5 mm |
| 0.2 mm | identical | 124,155 of 9,836,092 (1.26%) | 5.3e-5 mm |

The hashes differ, so any digest must be pinned per build image. Cause of the differences not investigated. Ignore `max_abs_vertex_delta_sorted_mm` from `compare_stl.py`: sorting misaligns near-equal facets, so it is meaningless once facets differ.

## Mesh validity (Linux, same counts as Windows)

| Voxel | Degenerate | Edge use after dropping degenerates | Winding conflicts | Components |
|---|---|---|---|---|
| 0.4 mm | 88 | all 3,673,008 used twice | 0 | 33 (1 body, 32 zero-volume specks of 12+ tris) |
| 0.2 mm | 2,112 | all 14,750,970 used twice | 0 | 69 (1 body, 68 zero-volume specks) |

Raw output needs degenerate and speck removal before use.

## Crash soak (0.4 mm, 8 containers at once, 16 threads each)

| Build | Runs | Crashes | Hashes of successful runs | Peak min/med/max MB |
|---|---|---|---|---|
| Memory timer on | 200 | 2, SIGSEGV (rc=139), no output | 198 identical | 548 / 571 / 608 |
| Timer stopped (`PICOGK_STOP_MEM_TIMER=1`) | 400 | 0 | 400 identical | 548 / 571 / 611 |

The crash cause is inferred, not proven: no stack was captured. It matches PicoGKRuntime #31's rate and trigger. The two arms alone give one-sided Fisher p = 0.11. If the timer-on rate were the 1% seen here, 0 of 400 would have about a 2% chance.

## Changes needed

- `CMakeLists.txt`: one line, same as PR #30, for `picogk.26.2.so`.
- `spike.csproj`: `linux-x64`, self-contained.
- `Program.cs`: optional reflection stop of `m_oTimerMemCheck`. This relies on a private field of NuGet 2.3.0.
- Run image: gcc:15's `libstdc++` and `libgcc_s` copied in, on `LD_LIBRARY_PATH`.
- `avast-ca.pem`: this machine only, gitignored. Copy from `C:\ProgramData\Avast Software\Avast\wscert.pem` before building.

## Not tested

- Base images are floating tags (`gcc:15`, `debian:trixie-slim`, `dotnet/sdk:9.0`), and apt versions are unpinned. Reproducibility was shown only with the same cached base images on one machine.
- Soak at 0.2 mm, soak on Windows (same timer code), other CPUs, Fly.
- Wall thickness on the mesh.
- PR #115 bool marshalling: the spike doesn't call the affected API and the fix is not applied.
- Whether `libgl1` and `libx11-6` can be dropped: `ldd` shows the `.so` doesn't link them.
