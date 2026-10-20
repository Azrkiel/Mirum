**CHANGES_REQUESTED** — two reproducible issues remain even for the clearly labeled nonproduction demonstrator.

- **Medium — false round-trip equivalence:** `plate.py:200–204`; regression gap at `test_plate.py:131–144`. OCCT Boolean cuts can erase differences below their effective tolerance, so zero Boolean-difference volume does not substantiate the stated volume budget. Using the existing test’s exporter-substitution approach with actual STEP export/import:
  - Exported diameter **6.0000004 mm** instead of **6 mm**.
  - Reimport measured **6.0000004 mm**, yet export was **accepted**, reporting symmetric difference **0.0 mm³**.
  - Actual source/export volume difference: **9.04778644326143e-05 mm³**, exceeding the reported budget **2.33214159868246e-05 mm³**.
  - **Recommendation:** supplement Boolean comparison with independently compared volume and measured feature parameters; establish a kernel-aware comparison floor and explicitly describe equivalence as tolerance-bounded, not exact. Add this near-kernel-tolerance regression. This is not a manufacturing-scale defect, but it concretely contradicts the numerical evidence claim.

- **Medium — failed publication can leave STEP behind:** `plate.py:170–174, 225–226`. Injecting an `OSError` on the second `Path.replace` caused `export_verified_plate` to raise while leaving **`plate.step` present and `validation.json` absent**. This contradicts “Failed runs publish no STEP,” without requiring concurrency or hostile input.
  - **Recommendation:** publish the completed staged directory as one rename into the absent destination, or provide rollback and accurately document remaining crash-consistency limitations. Add a second-publication-failure regression.

**Other observations**
- Plane orientation/datum checks remain relevant at `plate.py:142–149`; understood to be addressed by the parent.
- The explicit fixed-family, noncertification, and same-kernel limitations are appropriately scoped. I found no need to broaden this into a generic CAD validator.

**Verification:** existing native Docker image, networking disabled, source mounted read-only; **28 tests passed**. Both issues above were exercised with real OCCT execution. No repository files modified or created; no commits or paid calls.