# F03 PicoGK adapter fixtures (offline snapshot tests)

No recorded native PicoGK run exists in the repository (the builder is a Linux-only binary). These
fixtures are derived by hand from existing repo data, never from a native run. The native call
(RegenAdapter.evaluate, probe, _ready, regen_provenance) is stubbed in test_program_kernel_picogk.py.

- Candidate: built in the test with the same helpers test_engineering_regen_adapter.py uses
  (brief_for, content, regen_spec from test_engineering_generator_contracts.py: the 'small' preset of
  engineering.regen_presets.EXAMPLES, digest via RegenAdapter.candidates). Nothing is copied here.
- Accepted/rejected evaluations: built offline by engineering.regen_adapter.build_evaluation from the
  fake accepted_report() in test_engineering_regen_adapter.py (rejected: one check flipped to fail).
- cube.json: a unit cube (corners plus six quads, scale 10 mm) taken from the box in
  test_engineering_regen_adapter.py (_box_stl). The test builds a tiny synthetic binary STL from it
  with engineering.regen_mesh.stl_bytes and numpy. Its bbox is 0..10 mm and volume 1000 mm3.
- reread-report.json: only the report checks the inspect comparison reads: bbox measured_min and
  measured_max and volume measured_mm3, set to the analytic values of that 10 mm cube. The test
  injects stl_sha256 from the synthetic STL bytes.
