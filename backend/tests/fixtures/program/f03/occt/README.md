# F03 OCCT adapter fixtures (offline snapshot tests)

No recorded native beam run exists in the repository: every native test needs cadquery and a Linux
process group. These fixtures are therefore derived by hand from existing repo data, never from a
native run. The native call is stubbed in test_program_kernel_occt.py.

- Candidate: backend/tests/fixtures/engineering/candidate-15mm.json (length 200 mm, width 10 mm,
  height 15 mm, dimensional_tolerance_mm 0.01). Reused unchanged, not copied.
- evaluation-accepted.json: shape of the dict returned by CantileverAdapter.evaluate (outcome,
  report.measurements, report_sha256, artifacts). Measurement values are the analytic values of that
  candidate: volume 200*10*15 = 30000; surface area 2*(200*10 + 200*15 + 10*15) = 10300; section
  area 10*15 = 150; section_iy 10*15^3/12 = 2812.5 (the same formula asserted in
  backend/tests/test_engineering_native.py line 17). Artifact bytes are placeholder ASCII text; the
  report_sha256 is a placeholder 64-hex string.
- evaluation-rejected.json: a rejected evaluation: no artifacts and no measurements (the worker
  reports measurements only after the prism checks pass, see engineering/verify.py).
- reread-report.json: the measurements block a worker.py reread of the accepted STEP would return
  (same values as above). The test injects step_sha256 from the placeholder STEP bytes.
