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
