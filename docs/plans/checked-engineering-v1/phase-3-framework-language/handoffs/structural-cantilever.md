# M3 structural cantilever verification

The [reviewed MEL fixture](../../../../../backend/engineering/fixtures/structural/cantilever.mir) selects the named-regions model on the existing rectangular cantilever capability. Both boundary names appear in the staged review. The worker applies the 1 N resultant at the free-end centroid node in +Z, then reads that node’s +Z displacement. The root section fixes transverse displacement and bending rotation.

The regular native STEP verifier measures the rectangular prism before running the kernel analysis. The published report contains the material, units, resolved sections, mesh identities, solver fingerprint, and canonical geometry revision. It carries the SHA-256 of the exact STEP bytes. The delivered verifier binds independently read geometry to the check plan and verifies that the structural result in the bundle matches the current reviewed candidate, solver dependency digest and delivered STEP.

The conformance test stages, reviews, runs, verifies, publishes and archives the fixture through the native program routes. Missing and ambiguous references are refused with MEL-S023 and MEL-S022. An earlier analysis injected into the delivered report of a 1.5 N revision is rejected during verification. A revision limited to 0.1 mm is rejected by design policy. A revised clamp at X = 0.05 m agrees exactly with a cold solve. Renaming the design preserves the numerical dependency digest. The test retrieves the original revision’s evidence, STEP and report after both failures and a subsequent promotion.

## Reproduce

Use Mirum’s pinned verification dependencies, including the glider lock required when the FastAPI app starts. From the Mirum checkout run:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest -q backend/tests/test_program_structural_native.py
```

The test uses the actual program API, native OCCT/CadQuery workers, SQLite revision service, publisher and delivered verifier. It also captures the nominal revision and remeasures its archived STEP. The run used CPython 3.12.14, CadQuery 2.6.1, cadquery-ocp 7.8.1.1.post1 and NumPy 2.2.6. The vendored source and exact kernel implementation revision are recorded with the [vendor pins](../../../../../backend/vendor/mirum-kernel.md).

## Nominal verification receipt

| Item | Recorded value |
| --- | --- |
| Mirum implementation commit | `ed2f0428d6562a92a17b3fa03cf79f90fbd86064` |
| Kernel solver commit | `79f3517f3d447fceb27927427955390bfdd1a813` |
| Capability and numerical policy | `rectangular_cantilever@1.0.0`, `beam_numerical_v1` |
| Published revision | `c729408e368f46b29dd406c05edaa9da` |
| Reviewed brief SHA-256 | `4cd8af568c987b096d13627ff26210cdb97f71b7f1266a009109c97c35cf0a99` |
| Geometry revision SHA-256 | `612566b45f41da4e3f00d75244c5de042f2c64f55173a5cf2f894b0f9afb52b8` |
| STEP SHA-256 | `98fdd6460d41a055c41333e80427eac9caabc02c3de93caa7f6f471d7074b444` |
| Bundle manifest SHA-256 | `0836eed347bbd8cbca8e22c1e08e9298db7bab469e375c941af328bc5ddf4826` |
| Structural dependencies SHA-256 | `866b0ffcfe3bc4e963dcc15cfb8d09422513adad2d620788b64cfb32aff31450` |
| Delivered verdict | Verified |

The 16-element solve gave 0.357142857142 mm tip displacement and a −0.999999999998 N clamp reaction. A fresh publication gets its own revision ID and exact artifact digests. [Kernel replay and full results](https://github.com/Azrkiel/mirum-kernel/pull/3)
