# M3 reviewed thermal bar

The [thermal bar MEL fixture](../../../../../backend/engineering/fixtures/thermal/bar.mir) reviews the named cold, hot and insulated faces, conductivity and expansion coefficient, both absolute temperatures, the manufactured temperature interval, the one-way field and registration, the axial restraint, and the opposing gap and allowance.

`mirum_thermal_units_v1` converts absolute degrees Celsius to Kelvin with the 273.15 offset. Celsius temperature intervals pass through to Kelvin intervals. The solver boundary records reviewed millimetres and the equivalent SI metres. The base STEP uses the undeformed bar in the common +X frame. The product rereads its six faces and cold and hot sections, reruns the numerical checks against the reviewed inputs, and requires the complete report to match the leased bytes.

## Recorded verification

| Item | Value |
| --- | --- |
| mirum-kernel main base | `764015be5536e1c30651bc293d146e06c74af59b` |
| Mirum main base | `a2dff5e481d47b183373c08d99f66b37c8de7a6e` |
| Vendored kernel revision | `35b158ff7a6d97231f87d1a25f404f19d31bd9bb` |
| Mirum implementation tested | `e1462ffbdf46a27f11861e9587bb3010ee01c9ca` |
| Nominal published revision | `14c654cda7294d889778520f1eb0ff82` |
| Reviewed brief SHA-256 | `d157bf4ef05465920731b5932eb8c4668202a3c35ad990990bb5e665ec5b852d` |
| Native lock SHA-256 | `a939f5fb7db0be46db315f3eded899f4206deef49da76c3a3efb0c008eb60e4e` |
| Delivered check plan SHA-256 | `597e226182301c8ee63e01f8c1b8ca2fe60788c16d672c7885f5798ddb41a720` |
| Bundle manifest SHA-256 | `346b8fc070ab77416e0e659f51b2bef801d51ad4dc2ac7c1e4258b1359af07c4` |
| Generator report SHA-256 | `3f85d2ea182a9a7f244442c7bba4de9db8d3bfa5da022e81e306886a0037b0a5` |
| STEP SHA-256 | `2a3f1038dfc6e8dd9a24146d387beb27cec306bb1aff56ab202c68c250e180e0` |
| Capture ZIP SHA-256 | `75d04a9841a91a7e648e4f6d422f8c2344e4b757722133bd237bf5e8448e7a48` |
| Delivered verdict | Verified |

The 16-element source and 17-element target recovered 294.15 K at the hot end, 2.00 W leaving the sink, 1.15 µm of expansion and a 3.85 µm gap. The allowance is 0.02 µm and the remaining margin is 3.83 µm. A 0.50 µm cold gap failed at −0.65 µm remaining. The stale field from the earlier heat-input case failed delivered verification with `report_mismatch`. Missing and ambiguous references were refused before review. The original accepted head survived these cases, and its report and STEP bytes still matched after the later valid promotion. [Machine-readable receipt](evidence/m3-thermal-receipt.json)

## Reproduce offline

Use CPython 3.12.13 and the hashed release lock. This run used Ubuntu under WSL2, CadQuery 2.6.1, cadquery-ocp 7.8.1.1.post1 and NumPy 2.2.6. From the Mirum root, install the lock once, then run the conformance and structural tests offline.

```sh
python -m pip install --require-hashes --no-deps -r requirements-release.lock
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MIRUM_THERMAL_EVIDENCE_DIR="$PWD/thermal-evidence" \
  python -m pytest -q backend/tests/test_program_thermal_units.py \
    backend/tests/test_program_thermal_native.py backend/tests/test_program_structural_native.py
cd backend
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  python -m engineering.program.replay verify ../thermal-evidence/mirum-thermal-replay.zip --measure
python -m engineering.program.schema_export --check
```

The thermal test group passed 10 tests. The structural and shared contract group passed 90 tests, including the structural native revision, evidence and schema records, operation registry, execution lock and beam golden cases. The archive CLI returned `native_reread=consistent`. The scope is analytical verification of this benchmark. Physical validation and M4 model comparisons retain their own milestones.
