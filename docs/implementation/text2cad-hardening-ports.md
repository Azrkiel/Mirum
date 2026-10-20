# Targeted Text-2-CAD hardening ports into Mirum

## Provenance and scope

Mirum baseline: `4501ff067e5e89556210611c910be0463e222d7b`.
The reference is the preserved **local** Text-2-CAD `architecture-hardening`
working tree at `C:/Users/jiang/Text-2-CAD`, based on
`9604df316c7051201be7689382498203906d5ced`. Its hardening implementation is
uncommitted and is not in GitHub main. The source checkout must stay untouched.

This is a targeted compatible integration, not a wholesale backend/UI replacement.
Mirum retains its aerospace/propulsion schemas, geometry helpers, analysis package,
Babylon.js viewer, and existing API request shapes.

## Selected ports and repairs

| Area | Change | Evidence seam |
| --- | --- | --- |
| Script policy | Stronger AST rules from Text-2-CAD: module/attribute/frame reflection, forbidden import members, relative imports, dangerous format strings and malformed-input handling. Preserve `cad_aero` and existing audit hook. | `test_text2cad_policy_ports.py`, existing compiler-security tests |
| POSIX execution | `ProcessRunner` and `_memory_entry.py` own cancellation, bound combined output to 1 MiB and enforce the 1.5 GiB address-space cap before exec. Keep stdin delivery and a secret-free child environment; bound BLAS threads. | `test_execution_lifecycle_ports.py`, native aero sandbox test |
| Retrieval | Positive smoothed TF-IDF, fresh weights after insert, whole-source dedup, per-record UTF-8/JSON handling, correct candidate threshold and nonpositive-k behavior. Preserve failure-aware technique retrieval. | `test_retrieval_hardening_ports.py` |
| Assembly failure handling | Failed solves cannot export/deliver a successful assembly; a trusted `cad_utils.solve_assembly_checked` helper requires actual native solver success and acceptable termination, not just a zero exit or stdout marker. Select constraints using enum values instead of silently defaulting every enum to Plane. Reject unsupported coupling types rather than pretending to enforce them. | `test_assembly_failure_ports.py`, real native constraint-solve smoke |
| Geometry binding | Support CadQuery's OCP without requiring the incompatible `OCC.Core` bindings. Preserve the public certificate and propulsion fields. | `test_geometry_ocp_backend.py` |
| Generation acceptance | `/generate` reports a terminal error without a model payload if its required geometry certificate fails or is unavailable. A passing certificate is still NOT engineering certification. | native API positive/negative certificate tests |
| Subagent contract | Machinist, Critic and both Orchestrator machining paths consume the actual `(code, step_path, certificate)` result and preserve certificate metadata. | `test_subagent_contract_ports.py`, `test_subagent_result_contract.py` |
| Refinement handoff | Generation returns its manifest and per-part source so the existing frontend/refiner receive actual context. | native generation and source-reuse refinement tests |
| PDF export | Fix unsupported Greek alpha/em-dash labels for built-in PDF fonts, preserving meaning as printable equivalents. | existing PDF suite and `test_pdf_font_ports.py` |
| UI state | Send structural-analysis opt-in; retain the previous accepted view on failed, empty or incomplete generations instead of clearing it or reporting Ready. | `frontend/test_streaming_hardening_ports.py` |
| Verification | Pinned Linux CAD test environment from Text-2-CAD, Mirum-specific dependencies, native CI job, explicit separation of live QA from the offline suite. Fix classifier-test global SDK pollution/event-loop ownership and CRLF test shims. | `Dockerfile.verify`, `pytest.ini`, `.github/workflows/ci.yml` |

## Extra broken paths repaired during verification

- Real non-converged IPOPT solves now fail before any success marker or GLB export; the trusted `cad_utils` helper checks native solver statistics without weakening the script policy.
- Multipart certificates require explicit boolean success and full unique part coverage.
- Sibling part jobs are cancelled and drained on early failure, disconnect, and repeated cancellation. Process spawn, timeout, output, detached-descendant, bystander, and close-admission regressions are included.
- Refinement reuses Assembly-valued subsystems correctly; refinement and manual edits invalidate stale certificates, reports, parameters, and incompatible context.
- Manual/SSE envelopes are validated before publishing a result; malformed responses preserve the prior accepted model.
- XFOIL PDF export converts unsupported core-font labels to printable equivalents.
- A real-browser check exposed edge-on framing and unlit metallic surfaces. The viewer now uses a tilted orbit camera, visible material, and ground below the model.

## Deliberately not transplanted

Text-2-CAD's immutable approved-plan protocol, SQLite run/revision store,
artifact downloads, complete mandatory assembly/export acceptance, durable replay,
and replacement frontend are tightly coupled changes. Copying them piecemeal
would regress Mirum's existing domain features. They require a separately designed
API/data migration rather than pretending this compatible port provides them.

Retrieval still learns from compile-success telemetry. It is **not** an
accepted-only semantic or engineering-quality corpus. A valid STEP solid does
not prove dimensions, mates, collision freedom, material suitability or manufacturability.

Legacy `/run` and `/refine` still use their existing narrower validation paths.
The class-based Orchestrator is repaired and tested but is not promoted to replace
the live procedural `/generate` pipeline. Admin model hot-swap remains incomplete.
The legacy Gemini SDK and stock configured model names are unchanged; no live
provider availability, authentication or generation-quality claim is made.

The AST filter, Python audit hook and subprocess limits are defense in depth,
not a hostile-code sandbox. Native Windows remains compatibility-only without
POSIX memory/process-tree guarantees. Public multi-tenant deployment is not qualified.

## Running the offline native gate

Build `Dockerfile.verify` and run the command in the root README. It disables
container networking, drops capabilities, uses a non-root user/read-only filesystem
and tmpfs scratch. Models are deterministic fixtures; the CAD kernel, STEP/GLB
exports, certificate checks, API handlers and subprocess lifecycle are real.

The airfoil unit reference uses the explicit mock XFOIL binary. This is not
validation of aerodynamic predictions against experiments. Live REA tests require
explicit `-m live` selection and a real API key; do not run those as part of this gate.
Warnings or optional skips must be disclosed in the final verification report.
