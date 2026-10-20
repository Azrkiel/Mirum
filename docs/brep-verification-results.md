# Native verification results and delivery scope

## Actual final test run

`native-final-reviewed-tests.log` records the combined native selection of the new demonstrator suite and the preserved local `backend/tests/test_geometry_ocp_backend.py`:

```text
41 passed, 57 warnings in 13.18s
exit code 0
```

Earlier runs are historical, overlapping evidence; do not add their counts. Warnings include upstream CadQuery/pyparsing deprecations and pytest-asyncio configuration. This is **not a rerun of the entire application suite**, a live-provider evaluation, or an end-to-end browser/API qualification. No paid model calls were made. Existing checker tests passing does not contradict its demonstrated false acceptance; those tests enforce narrower requirements.

The native runtime was the existing local `mirum-integration-verify` Docker image, CadQuery 2.6.1/OCP 7.8.1.1, no network, read-only root, init, dropped capabilities, no-new-privileges, 2 GiB memory/2 CPUs, tmpfs scratch and read-only source mounts.

## Independent review and fixes

`docs/reviews/demonstrator-review-r1.md` preserves the actual **CHANGES_REQUESTED** review. The reviewer independently exercised real native CAD and found:

1. **Near-kernel difference hidden by Boolean comparison.** Exporting a 6.0000004-mm bore instead of 6 mm returned Boolean difference zero, while absolute volume change exceeded the stated volume budget. Parent reproduced this with an expected assertion failure in `plate-red-near-kernel.log`. The export gate now independently compares mass and measured feature parameters as well as Boolean difference. Exported topology tolerances cannot inflate the source-derived feature comparison floor.
2. **Partial publication after I/O failure.** Sequential STEP/evidence renames could leave STEP behind when the second failed. Parent reproduced it in `plate-red-publication.log`. STEP/evidence now stage together and publish with one same-filesystem directory rename to an absent destination. Handled validation/publication failure leaves no accepted directory. This is not concurrency control or power-loss durability.

The final native run above includes both regressions passing. A narrowly scoped final source re-review was requested separately; no independent approval is inferred merely from the passing tests. Any subsequent verdict must be reconciled against the exact source hash below. The delivered module remains a labelled nonproduction demonstrator, not a live acceptance service.

Two other independent source audits covered engineering/assembly and API/evaluation/training risks; findings were reconciled into the main report. A third broad kernel worker hit a provider usage limit; parent-run native STEP probes supplied the kernel findings directly.

## Final generated artifact

Use **`C:/Users/jiang/Mirum-brep-audit/artifacts/release-plate/`**. Older `nominal-plate/` and `verified-plate/` runs are historical, not the final source-matching delivery.

| Measurement | Actual result |
|---|---|
| Source and STEP validation | Both accepted within bounded demo scope |
| Solid / bore counts | 1 solid / 4 bores |
| Measured dimensions | 80.0 × 50.0 × 6.0 mm |
| Bore centers | (−30, −15), (+30, −15), (−30, +15), (+30, +15) mm |
| Bore diameters | 6.0 mm each |
| Reimported volume | 23321.41598682459 mm³ |
| Maximum reimported topology tolerance | 1e−7 mm |
| Same-kernel Boolean difference | 0.0 mm³; **not exact-equivalence proof** |
| Independent absolute-volume difference | 7.275957614183426e−12 mm³ |
| Maximum measured feature change | 0.0 mm |
| Source-derived feature comparison budget | 2.0000000177635683e−7 mm |
| Volume comparison budget | 2.33214159868246e−5 mm³ |
| STEP SHA-256 | `836dd2bf5d8726de489e6dda3211c886874d21bd3fa831fa3fd2a1dffaff913f` |
| Specification SHA-256 | `804e8f04a3a6711189b406405cc9c64e92b1aec3bd9dbf60e5385ae72cebc9ef` |
| Validator source SHA-256 | `7760df3c7dd66f7e7e15de3ab8d12b7f4d3cd848d538603d40d38a96e5848ac3` |

`validation.json` explicitly records `engineering_certified: false`. Hashes bind bytes, not authority or safety. The 0.01-mm scalar dimensional comparison budget and 0.001-mm topology ceiling are example numerical policies—not standards-qualified manufacturing tolerances or physical inspection uncertainty.

## Visualization and static checks

`preview.png` is a software depth-buffer rendering of 2044 triangles from the actual reimported STEP. Rendering asserts nonempty actual geometry. The same geometry's software-rendered preview was visually inspected. The preview is not an acceptance input.

Real OpenGL attempts failed: missing `OSMesaCreateContextAttribs` and then unavailable EGL library. No synthetic substitute was used. Final renderer uses actual native triangles with NumPy/Pillow, avoiding unavailable OpenGL drivers.

All four example Python files passed AST syntax parsing. The original local checkout's tracked diff summary remained 18 changed files / 428 insertions / 258 deletions, matching initial inspection. This task made no edits there. Detached audit HEAD remained `4501ff067e5e89556210611c910be0463e222d7b`; additions are uncommitted new files. Host rechecks and the archive manifest bind final file contents.

## Deliverables and boundaries

- `docs/brep-engineering-audit.md`: codebase-wide critical-path audit, source references, published-vs-local distinctions, architecture, standards boundaries and phased exit gates.
- `examples/verified_plate/`: actual builder, measurement/export gate, tests and reproduction commands.
- `artifacts/release-plate/plate.step`, `validation.json`, `preview.png`: final nominal specimen, measured evidence and actual-geometry preview.
- `existing-gate-probes.log`: actual published/current-local certificate probes, with adapter semantics clearly labelled.
- `plate-red*.log`, `plate-green*.log`, `native-final-reviewed-tests.log`, `release-plate.log`: actual executed evidence, including rejected review candidates.

No arbitrary-B-rep validation, GD&T conformity, FEA, manufacturing capability, hostile-code isolation, regulatory approval or production readiness is claimed. The live application gaps remain documented. This task delivers an audit, roadmap and working bounded proof—not completion of all proposed architecture phases. Nothing was committed, pushed or merged.
