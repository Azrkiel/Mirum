# Mirum CEM verification

Branch: engineering/cem-glider, based on origin/main 2e6a4ee. Changes are uncommitted.

- Full offline source-overlay run in the existing native CAD backend image:
  979 passed, 27 failed because Node was absent, 1 live test deselected.
- Recheck with Node mounted, including all new CEM tests and browser/canonical geometry tests:
  70 passed. This includes 2 added headwind regressions.
- Across the full run and recheck: all 1,008 selected checks have a passing result.
- Existing workspace glider suite: 76 passed.
- Actual localhost HTTP flow: feasibility, all-five-process discovery, selected build,
  independent mesh/mass re-trim, corrected rebuild, parent evidence validation and ZIP download passed.
- Synthetic HTTP flight update and old-version identity preservation passed; the synthetic
  calibration was removed from the API calibration store after the test.
- Headless Chromium/Streamlit feasibility flow: passed, no browser errors.
- Dependency check and git diff whitespace check passed.
- Security review found no unresolved critical/high findings. Bandit, pip-audit and Ruff
  were unavailable; no automated vulnerability-scan result is claimed.

The verification Dockerfile now includes the Node runtime needed by the browser geometry
regressions. The native run used an existing image plus read-only source/Node mounts,
network disabled, unprivileged UID 1000, a 4 GiB container cap and two CPUs.
This is not a fresh dependency-image build, production deployment, live-provider test,
independent XFOIL/AVL validation or physical flight/manufacturing qualification.

Example output ID: 62aa4aaf9641
CEM version: mirum-cem-glider-0.1.0+2a60707013b2
Calibration: b1fa2cf84e57e195ef6ec77a66612aefb6d2a60dc7e1f2dc1de6b73576fa87b7
The bundle is screened analysis OML/templates and stock spar geometry; it is not accepted
for fabrication. Spar joints, installation/load path and physical shell/rib/cavity geometry
remain unqualified. See implementation-status.md for the remaining roadmap.
