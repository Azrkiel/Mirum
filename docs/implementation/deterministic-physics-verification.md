# Verification handoff — deterministic physics candidate

## Scope and state

Implementation worktree: `C:/Users/jiang/Mirum-physics`; branch `engineering/verified-physics`; base `de07fdccc577099fbb65cf2469f84abb1d3a0b84` (`origin/engineering/brep-acceptance`). Changes are uncommitted. No merge, push, deployment or modification of the existing main/phase1 checkouts was performed.

See [the architecture/comparison and capability contract](deterministic-physics.md) and [`examples/engineering/cantilever.json`](../../examples/engineering/cantilever.json).

## Executed verification

| Check | Actual result |
| --- | --- |
| Initial typed-spec missing-behavior test | Failed as expected before implementation; canonical source/digest test then passed |
| Beam FE missing-behavior test | Failed as expected before solver implementation; then matched independently computed closed-form displacement, stress, reactions and energy |
| Native design-intent/physics verifier test | Failed as expected before implementation; then real STEP measurements/analysis passed |
| Deterministic service and feedback tests | Failed before implementation; then accepted only the first approved passing height |
| New API tests | Initial 404 failures; real native API routes subsequently passed |
| Numeric-tolerance budget adversary | Initially false-accepted through a labelled reader-report seam; regression now rejects it |
| Legacy wing false positives | Missing span/no root moment and CL above CLmax initially passed; regressions now fail closed |
| Focused candidate suites | 43 passed before the uncertainty-budget test; wider final focused gate 67 passed, 18 dependency warnings |
| Full offline suite in native Linux/OCP runtime | **643 passed, 1 deselected, 274 warnings; exit 0; 339.32 seconds** |
| Exact source/image comparison | All 15 new/modified production and test files match the exercised image byte-for-byte |
| Real loopback Uvicorn HTTP | Auth checked; accepted example, identical repeat, rejected-allowable example with no STEP/GLB all exercised |
| Artifact check | Host-side STEP/GLB hashes recomputed from actual response bytes and matched report |
| Actual preview | Rendered actual GLB triangles; image visually inspected; no synthetic geometry used |
| Security lint | Ruff S rules passed on new production; passed on new tests with S101 ignored for pytest assertions |
| Patch whitespace | `git diff --check` passed |

The full suite includes unit/contract/UI-state tests and native CAD tests, with mocked LLM seams. This is **not** 643 engineering-qualified parts, a live-language-model success rate, full manual Streamlit QA, or certification. Live-provider tests were excluded. Warnings include an unsupported Python 3.10 Google SDK runtime warning and dependency deprecations.

## Build and environment limitations

- Clean `Dockerfile.verify` dependency rebuild failed at PyPI certificate-chain validation. TLS verification was not bypassed. This remains an unresolved clean-build/environment blocker.
- A local source-overlay image reused the installed native runtime, copied current sources, normalized trusted fixture scripts, and ran as UID 10001. Image: `mirum-verified-physics:candidate`, ID `sha256:34a971994cab825b23a5dcb86773f2942b1e1399dd18c68519de0ff7ce697742`. It is a source-bound behavior check, not a clean dependency/release build.
- Initial bind-mount baseline attempt: 587 passed, 2 failed, 7 errors because trusted mock binaries were copied to Docker's `noexec` `/tmp` mount. The mount flag was independently confirmed. The failure log is retained rather than relabelled a code regression or clean baseline.
- Bandit and pip-audit were not installed/cached; offline attempts failed. No dependency-vulnerability audit pass is claimed.

## Retained evidence

`C:/Users/jiang/Mirum-physics-evidence` contains actual accepted STEP/GLB, GLB-derived preview, accepted/rejected reports, tested-source manifest and capture/snapshot verification helpers. Logs are retained under `C:/Users/jiang/Mirum-physics-*.log`.

The live accepted sizing example measured 200 × 10 × 15 mm, 30000 mm³, section area 150 mm², section second moment 2812.5 mm⁴. Under 50 N, the beam FE computed 0.6772486772477769 mm tip deflection and 26.66666666659267 MPa root bending stress. Heights 5 and 10 mm failed before 15 mm passed. The 120 MPa allowable and 70000 MPa modulus are explicit illustrative user-approved inputs, not independently certified material values.

## Independent review

A fresh, read-only reviewer inspected the current production changes, regression tests, authentication wiring and process cleanup contract. It independently confirmed all 15 candidate source/test hashes match the native-tested image and ran additional direct finite-element equilibrium, energy and closed-form checks. Its recorded final verdict was `passed: true`, with empty blocking security/logic finding lists.

Nonblocking suggestions included a non-square-section native inertia regression and engineering-specific cancellation/semaphore cleanup tests. The real HTTP example already exercises a non-square 10 × 15 mm section; existing generic ProcessRunner tests cover owned-process cancellation, but do not replace the suggested integration regressions. This review concerns changed behavior within the declared envelope, not qualification of all legacy features or production deployment.

Two earlier Claude CLI piped-prompt review attempts produced no usable verdict; a separate explicit-prompt CLI review hit its 300-second deadline. None is counted as approval. The approval above is from the fresh isolated reviewer whose completion log is retained with the evidence. No merge/deployment authorization follows from it.

## Remaining qualification gaps

- V1 new endpoint is API-only and supports only the declared idealized rectangular cantilever. It does not make legacy free-form generation deterministic or geometry-bound FEA-qualified.
- No 3D solid FEA, real meshing, fatigue/buckling/contact/nonlinear analysis, physical material verification, manufactured tolerance propagation, semantic GD&T or standards-specific conformity.
- Existing optional wing analysis remains analytic and advisory for general model delivery; its two false-success bugs are fixed without relabelling its fidelity.
- Native validity alone still does not prove every legacy assembly joint, feature, clearance, domain helper or natural-language request correct.
- Nimbalyst comparison is source-grounded. Its full desktop/Replicad extension build and runtime were not exercised in this task.
