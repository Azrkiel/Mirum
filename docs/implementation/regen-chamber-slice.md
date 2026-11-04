# RegenChamberSpec vertical slice (PicoGK)

Status: geometry slice with bounded whole-part verification implemented; small-suite validation only for the current milestone. Not wired into the application pipeline.

**Safety notice:** Geometry checks only. No gas dynamics, thermal, structural, or manufacturing analysis was done. Do not manufacture or fire hardware based on this output.

## What it is

A deterministic, no-LLM path from a typed spec to a verified mesh of a regeneratively cooled chamber
shape: a revolved wall with helical coolant channels, two manifold rings and two radial ports.

- `backend/engineering/regen_spec.py`: strict frozen pydantic `RegenChamberSpec`, `inner_radius`, `canonical_spec`, `specification_digest`. Pure Python, no cadquery or compiler import.
- `picogk-builder/`: fixed C# builder (`builder <spec.json> <out.stl>`) that renders a validated spec with PicoGK 2.3.0. No spec string is ever turned into code.
- `backend/engineering/regen_mesh.py`: numpy/scipy verifier (clean, manifold, volume, bbox, plane slices and bounded whole-part checks).
- `backend/engineering/regen_geometry.py`: spec-derived reference geometry and shared allocation/work accounting.
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

Valid example (tests and historical E2E with the earlier verifier): chamber R 35, length 40; convergent 50; throat R 20; divergent 80; exit R 45; wall 7; N 32; rc 1.0; twist 0.012 rad/mm; manifold r 1.5; end margin 8; voxel 0.4.
The spike parameters (N=48, rc=1.1, throat 15, wall 5) are rejected (tests): their throat rib is about 0.04-0.09 mm by hand against a 1.6 mm minimum (0.09 without the helix factor).

## Build and run

```
cp spike-picogk/avast-ca.pem picogk-builder/avast-ca.pem     # machine-local, gitignored
docker build -f picogk-builder/Dockerfile -t picogk-regen:slice .   # needs picogk-runtime:26.2
docker run --rm --name picogk-regen-x --network none -v <dir with spec.json>:/in:ro picogk-regen:slice /in/spec.json /out/o
```

Exit 0 accepted (writes `cleaned.stl`, `report.json`, `spec.canonical.json`); 2 rejected (invalid spec, or mesh checks failed; writes only `rejected_report.json` for mesh failures); 1 unavailable (builder crash, 600 s timeout, malformed output, missing runtime `.so`; writes nothing); 3 a `--prebuilt-stl` mesh passed the checks but is not accepted because the builder did not make it (writes only `prebuilt_report.json`). `report.json` is written last. `LD_LIBRARY_PATH` is set only in the builder subprocess environment, never globally. `--prebuilt-stl PATH` verifies an existing STL (test hook).

The Dockerfile COPY list now includes `regen_geometry.py` alongside the existing regen modules. No Docker build or run was performed in the bounded-verifier milestone; integration of this COPY change remains unvalidated. Use the registered small-suite command below for this milestone's host tests.

## Checks

components (extra components fail; only |signed volume| < 1e-3 mm3 specks are removed and recorded), edge-manifold (every edge exactly twice), winding (no repeated directed edge, signed volume > 0), bbox within 2 voxels of the spec, volume within **2 percent** of an analytic estimate (wall revolve integral minus helical channels, manifold tori and ports; overlaps and end caps neglected; tolerance fixed, not tuned), and 7 z-plane slices (6 evenly spaced strictly between the manifolds, plus the throat): exactly N channel loops, min hot wall, min cold wall, min rib each >= m - 1 voxel.
**Limitation:** slices are in z planes, so hot/cold wall values are in-plane distances, not surface-normal thickness; rib is also in-plane. Point-set distances are sampled (5 points per slice segment).

### Whole-part checks (`circuit_fidelity`, `circuit_coverage`, `wall_thickness`)

All existing checks stay active with unchanged thresholds (2 percent volume tolerance, seven stations). Three more run on the cleaned mesh against the reference model in `regen_geometry.py` (shell minus helical capsule chains, two ring chains, two radial ports, as the builder constructs it). Geometry only; `engineering_certified` stays false, physics and standards stay `not_evaluated`.

- **circuit_fidelity (mesh to reference).** Every triangle is split (4-way, adaptive) until all of its points lie within a stated radius of a checked centroid (`sample_radius_max_mm` <= half the surface tolerance). Each centroid must lie within `surface_tolerance_mm` (one voxel, from `regen_geometry`) minus its patch radius of a surface the spec defines: hot wall, cold wall, an end cap, or the surface of the coolant union. A mesh surface inside a specified void or covering a port mouth, a surface in solid material, or a coolant surface outside the part (blocked or joined channels, detours, wrong or missing ports, stray cavities) is a failure. Failing patches are refined further; patches that stay unresolved still fail.
- **circuit_coverage (reference to mesh).** The reference centerlines of every channel, ring and port are sampled at a gap derived from the smallest passage radius. Each sample must be in the mesh's void (nearest-surface normal test) with a certified clearance >= radius minus min(tolerance, radius/2); the clearance is the nearest surface-patch distance minus the maximum patch radius, so it is a lower bound. Certified empty balls around consecutive samples must chain into exactly one void component, so a broken ring, a plugged branch or a missing port fails whatever the loop count, topology or volume say.
- **wall_thickness (3D).** Leaf-triangle pairs from different surface classes are bounded with sphere bounds in a KD-tree-accelerated branch and bound, down to a leaf tolerance of a quarter of the allowance. Pairs with a proven distance >= threshold are done; pairs closer than the threshold that face each other across material (both normals point away from the segment between them) are violations with the 3D point and distance; pairs that stay between bound and threshold, or exceed the split or pair budget, reject as unresolved. Classes: hot and cold wall vs channels, manifold clearance (rings and ports vs walls), end regions (end caps vs all coolant surfaces), and ribs (adjacent channels). Thresholds come from the spec: `min(min_feature_mm, nominal)` minus a half-voxel allowance, where nominal is the reference's own lower bound for that class (wall `wall/2/sqrt(1+slope^2) - rc`, local-slope manifold clearance, `end_margin - manifold_radius`, numerically measured adjacent-channel gap), so a spec-valid part is never judged against more than its geometry provides. The only exemption is the port opening: port- or overlapping-ring-side patches within `port_rim_exemption_zone_mm` of the analytic rim where the port cylinder meets the outer wall. Reports list the thresholds, allowance, numerical error and violation locations.

Failure messages and per-check diagnostics (counts, tolerances, error bounds, locations in mm) are in the report; runs are deterministic (no randomness, fixed ordering).

**Limits of the new checks.** Surface placement is accepted within one voxel of the reference; thickness within half a voxel of the threshold. Facing is judged against surface normals with a 0.2 cosine margin, so a knife-edge wedge more oblique than about 78 degrees is not a thickness pair. Rib pairing uses the nearest nominal helix, so a channel displaced by more than half the pitch is caught by `circuit_fidelity`, not by the rib class. Junction overlaps inside the union are measured with the union's lower-bound distance. Very large meshes may exhaust the leaf or pair budgets; that rejects with a diagnostic rather than passing.

### Bounded allocation and work milestone

The verifier's whole-part working-memory guard is **1.5 GiB**. Large-mesh verification runs alone under a separate **3 GiB OS process-tree cap**; the **1.5 GiB controller-plus-worker cap** is used for the small development suite. Working-memory accounting is an allocation estimate, not a process-RSS ceiling. It includes a 16 MiB bookkeeping/diagnostic allowance. STL loading, legacy cleaning/slicing and report serialization are outside the whole-part accounting boundary and remain covered by the OS cap. The earlier 1 GiB working guard rejected valid meshes' retained leaf data and tree construction even after reference-work optimization.

The shared budget checks prospective allocations while charging retained input vertices/faces, leaf arrays, KD-tree storage, pending work and temporary copies that remain live together. Generator reservations remain charged while consumers process yielded batches. Parent scratch and pending siblings remain charged during child creation. Leaf finalization replaces one field at a time, so only that field's old blocks and final copy overlap; all retained storage stays accounted for. Phase-local reservations are released after the phase; cumulative work counters never reset between phases.

Default limits in `regen_mesh.py` and `regen_geometry.py` are:

| Budget name | Limit and meaning |
|---|---|
| `memory_budget` | 1.5 GiB of accounted whole-part storage; standalone reference queries default to 1 GiB |
| `candidate_budget`, `coverage_budget`, `refinement_budget` | 60,000,000 work items each; candidates/coverage count all radius-query neighbors, refinement counts input and generated child pairs |
| `candidate_batch_budget`, `coverage_batch_budget` | 20,000 neighbors per radius-query row |
| `queue_budget` | 100,000 pending leaf patches or triangle pairs |
| `scratch_budget` | 32 MiB for pair-refinement scratch; parent and child estimates are checked together |
| `leaf_budget`, `leaf_batch_budget` | 40,000,000 visited patches including internal nodes; at most 2,048 patches per leaf batch |
| `reference_budget`, `reference_batch_budget` | 60,000,000 cumulative reference work items; at most 150,000 candidate elements per reference batch |
| `sample_budget`, `sample_batch_budget` | 4,000,000 centerline samples cumulatively; at most 20,000 samples per segment grid |

These are resource limits, not geometric acceptance thresholds. The memory guard can reject before a work limit is reached. `regen_spec.py` is unchanged; spec-derived geometric thresholds, numerical allowances and the **2 percent volume tolerance** remain unchanged.

- **Count before materialization.** `_candidates` calls `query_ball_point(..., return_length=True)` in bounded blocks of 256 query points before requesting sorted neighbor lists for nonempty rows. It checks each complete row's work, batch and memory requirements, then yields bounded owned batches in fixed query/neighbor order. An oversized dense row rejects before the list is allocated. There is no fixed-k truncation or silently skipped pair.
- **Complete coverage connectivity.** The former all-at-once `query_pairs` allocation is replaced by the same counted radius queries and streaming union-find. The search radius is twice the largest certified ball radius; every unordered candidate edge is considered once after removing self/duplicate pairs, and actual variable-radius overlap decides union. All eligible edges are processed or verification fails.
- **Bounded refinement.** Pair refinement uses bounded depth-first batches, not a queue of all input pairs. Queue size, cumulative work, scratch and overlapping live memory are checked before indexing or four-way child allocation. The 16-split pair depth limit produces `refinement_depth_budget` failure on unresolved work; marginal bounds also fail. Leaf construction checks selections, retained arrays and complete child expansions before allocation, using six regular split levels and up to three additional levels for nonconforming patches. Unresolved or oversized terminal patches cannot pass fidelity.
- **Bounded reference and diagnostics.** Reference queries count broadcast elements before building arrays, including rejection of a single oversized item. Capsule nodes and per-segment centerline grids have work, batch and memory preflights; sample positions do not change with batch size. Diagnostics retain at most eight deterministically ordered locations per findings collection while keeping exact failure counts and minima, rather than retaining all offending arrays. Coverage sample/clearance arrays and selected copies are still retained where needed, with explicit guards and overlap charges.

The reference-work follow-up builds the fixed channel capsules and their spatial index once per model. A radius search around capsule midpoints uses the query reach plus the largest capsule half-length, with floating-point padding, so it includes every segment that could be close enough. Neighbor counts are checked before lists are materialized; exact point-to-segment distances decide the result. This replaces repeated padded point/channel/step grids without increasing the 60,000,000 reference-work limit. Triangles that must split are classified only after reaching a terminal patch; adaptive refinement and oversized terminal rejection remain active.

Sparse neighbor rows are packed into bounded owned batches while preserving the complete query/neighbor order. Scratch batches reserve space for a parent and its four children, and child blocks obey the same bound. Coverage uses balanced KD-trees with 64-point leaves and shares contiguous float64 centers; its storage estimate includes native indices, nodes and build overlap rather than a 256-byte allowance for every center. Thickness keeps the rim tree plus only the current target tree, releases completed phase selections, and computes channel assignments in bounded batches. Geometric thresholds and the 2 percent volume tolerance are unchanged.

At the `whole_part_checks` boundary, any `BudgetExceeded` returns normal failed entries for all three whole-part checks, with the exhausted name in `detail` and `failures` (for example, `unresolved: coverage_batch_budget exhausted (...)`). A final `MemoryError` safeguard at that boundary returns `unresolved: memory_budget exhausted`. It also catches failures during context construction. Earlier partial passes do not survive an incomplete whole-part run: `passed` is false and `design_intent.state` is `geometry_rejected`. Reports retain `engineering_certified=false`, `physics.state=not_evaluated` and `standards.state=not_evaluated`; CLI reports retain the safety notice above.

A certified `circuit_fidelity` deviation is a conclusive rejection. The verifier returns that finding immediately, reports the certified count and puts certified examples first. `circuit_coverage` and `wall_thickness` are explicitly `not_evaluated` in that rejected report. An unresolved-only fidelity result does not trigger this shortcut; accepted meshes still require all three checks to pass. The analytic port-opening collar also applies to overlapping ring patches that receive the ring label at the ring/port union; its radius and all thickness thresholds are unchanged. Regression tests retain thickness violations outside the collar.

### Registered small-suite validation

Run from the repository root in the pinned workspace-write sandbox. The registered launcher sets numeric threads to two and supplies a unique writable pytest temp directory. Use this exact command; no project virtualenv installation or runtime discovery is required:

```powershell
C:\Users\jiang\AppData\Local\hermes\bin\uv.exe run --offline --cache-dir C:\Users\jiang\AppData\Local\Temp\mirum-xvant-uv-cache --system-certs --python C:\Users\jiang\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe --with pydantic --with numpy --with scipy --with pytest python C:\Users\jiang\AppData\Local\Temp\mirum-xvant-runtime\check.py backend/tests/test_regen_spec.py backend/tests/test_regen_mesh.py backend/tests/test_regen_cli.py backend/tests/test_regen_budget.py -q -k "not adv" -p no:cacheprovider --tb=short
```

The bounded-allocation milestone returned **131 passed, 43 deselected, 1 warning in 6.62 s**, exit 0. The reference-work follow-up returned **148 passed, 43 deselected, 1 warning in 3.66 s**, exit 0, under a 1.5 GiB OS process-tree cap with a 0.19 GiB measured peak. The warning was `PytestConfigWarning: Unknown config option: asyncio_default_fixture_loop_scope`. No detached processes were used. Small synthetic arrays and tiny meshes cover dense-neighbor preflights, complete pair processing beyond 64 neighbors, leaf/reference and refinement guards, live-memory overlap, capped diagnostics, named-budget failures, `MemoryError` conversion and deterministic results across batch sizes. Follow-up regressions cover cached capsule queries against brute-force distances, terminal-only classification, field-by-field finalization, serial tree lifetimes, sparse-row packing and count batching, opening-label overlap, and certified rejection versus unresolved-only results. The budget tests import helpers via `tests.test_regen_spec`, consistent with the existing suite.

The operator's earlier preflight of the original tests reported 61 passed, 43 deselected and a 0.20 GiB peak job. That measurement excludes the new budget module. Tests originating at `d7a99ff` and all saved `adv` tests remain intact; the 43 names matching `adv` are explicitly excluded by the registered command and deferred to a separate milestone.

### Reference-work follow-up validation (2026-11-03)

Three saved native STL cases were verified serially under a 3 GiB OS process-tree cap. Both valid controls passed all twelve checks; the injected-defect case failed with a certified fidelity deviation rather than a resource-limit error. Subsequent coverage and thickness checks were explicitly `not_evaluated` for that rejected case.

| Case | Input triangles | Native verification time | Peak process-tree memory | Result |
|---|---:|---:|---:|---|
| Valid control 1 | 2,173,664 | 74.453 s | 1.30 GiB | All checks pass |
| Valid control 2 | 1,833,524 | 47.562 s | 1.15 GiB | All checks pass |
| Injected-defect case | 2,174,132 | 33.125 s | 1.32 GiB | Certified geometry rejection |

The two control cleaned-STL hashes match their earlier saved reports. Spec-validator rescoring remains 271 accepted candidates out of 4,000; all 48 previously selected specs remain valid. This is spec validation, not a 48-mesh verification result. The builder, spec validator, original tests and 2 percent volume tolerance are unchanged. These host-native checks do not validate a fresh builder run, Docker integration, the full geometry sweep, the full defect matrix, the 43 `adv` fixtures, repeatability or stress behavior.

## Historical E2E results (earlier builder/verifier, 2026-11-01)

The table below records the original builder and earlier verifier only (Docker `--network none`, image `picogk-regen:slice`, 16-core host), before the current whole-part checks and allocation bounds. Its exit codes, geometry passes, timings, hashes and memory figures are historical evidence for that implementation, not validation of the current bounded whole-part verifier. In particular, the 2.47 GB builder measurement is outside this milestone's controller cap and is not a current memory-budget result. These runs were not repeated in this milestone.

| Case | Result |
|---|---|
| (a) valid spec, 0.4 mm | exit 0. builder 2,173,664 tris, 10-12 s, peak ~600 MB; 18 degenerates dropped, 0 specks; volume 242,610.7 mm3 vs analytic 241,228.2 (+0.57 percent); bbox error 0.005 mm; 32 loops at all 7 stations; min hot wall 2.41, cold 2.40, rib 2.57 mm (threshold 1.2) |
| (b) 4 runs (3 plain + `--cpuset-cpus 0-3`) plus the (a) run | cleaned `e1ade11bd1b3bfc23be0ca6ecccbf3cd9412ef6413cb987c191db3d6378e0758` and raw `2a8206d8bc5be7b18c09fb02d9217a321bbb70604df0152263333dd048dee6e3` identical in all (a)+4 runs (raw hash of (a) not printed; its cleaned hash matched). Builder 10.3-14.3 s, peak 485-605 MB. runtime .so sha256 `44aa0a96a4f25b09...` |
| (c) spike parameters | exit 2 `Contour max slope 0.785 exceeds tan(35 deg)`, builder not run (also with a nonexistent builder path), no output dir contents |
| (d) fault-injected real mesh (via `--prebuilt-stl`) | 100 triangles removed: exit 2 (40 edges not used twice). All winding flipped: exit 2 (signed volume -242,610). 1000 triangles flipped: exit 2 (144 directed-edge conflicts). 5 mm cube in the air: exit 2 (extra component). Inverted cube inside wall (sealed void): exit 2 (extra component). 0.05 mm tetra speck only: passed, 1 speck removed and recorded (ran as exit 0 before the hook changed to exit 3; this case was not rerun, but an unmodified cleaned mesh through the hook now exits 3) |
| (d2) real builder output with spike parameters, validation bypassed (`model_construct`) | spike (convergent 40): rejected; throat stations show 2 channel loops instead of 48 (channels merged), throat rib 0.625 mm, 76 specks removed, 40 degenerates. Spike with convergent 60: rejected; 4 unexpected components, 20 loops instead of 48 at z=101.7, rib 0.10 mm. The valid spec produced 0 specks. This is consistent with the thin-rib explanation of the spike's specks but is not proof (one valid sample, different contour) |
| (e) valid spec, 0.2 mm | exit 0 (one run only, determinism not tested at 0.2). raw `72a82be8fb5e9e16d3c435162fae7c95475431f55ebefd025defe8c458e41ff9`, cleaned `e600cb0f6e6fcd301d0f019399603508a7266e13fec853a5b7567ce6167b1774`; 8,729,192 tris, 1600 degenerates, builder 47.6 s, peak 2.47 GB; volume error +0.25 percent; whole container run 84 s |

Historical host tests for that earlier implementation: 72 passed (`test_regen_spec`, `test_regen_mesh`, `test_regen_cli`, plus the existing `test_engineering_spec` unchanged). This count predates the current whole-part and budget validation.

## Limitations

- No STEP, no route, no physics, not in the pipeline or Fly.
- Base images (`dotnet/sdk:9.0`, `debian:trixie-slim`) and apt packages are unpinned. Hashes are valid only for the same cached images on one machine; Linux and Windows meshes differ (see `spike-picogk/RESULTS.md`).
- PicoGKRuntime PR #115 (bool marshalling) is not applied; this builder does not call the affected API.
- The builder reflects on the private field `Library.m_oTimerMemCheck` of NuGet PicoGK 2.3.0 to stop the memory timer (PicoGKRuntime #31). It fails loudly if the field is missing, but a PicoGK upgrade may break it.
- Soak (crash rate) was not re-run for this builder; the timer is always stopped as in the spike's 400-run clean arm.
- The seven-station wall checks are in-plane, not surface-normal; the whole-part `wall_thickness` check is 3D. The analytic volume neglects overlaps (about 0.5 percent observed error).
- The historical builder/earlier verifier exercised only the stated envelope (one valid spec at two voxel sizes). The current whole-part verifier has the small-suite and three saved native-STL checks above; fresh builder runs, Docker integration, the full geometry and defect sweeps, full `adv` fixtures and stress testing remain unvalidated or deferred.
