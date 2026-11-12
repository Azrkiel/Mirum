# Phase 3 release gates

F01–F10 release requires every P01–P14 row plus the prerequisite B/E evidence. F11 is optional research and not a release prerequisite. All gates initially not_run.

| ID | Accepted behavior | State | Evidence |
|---|---|---|---|
| P01 | AgentCAD reuse inventory pins source files and licensing/attribution decisions; no bundled logs, benchmarks or unreviewed transplants. | not_run | Not produced |
| P02 | Versioned grammar/IR/registry reject unknown operations, duplicate fields, unresolved references and invalid source before native work. | not_run | Not produced |
| P03 | Form, AI and language input with equivalent reviewed meaning produce the same requirement/candidate semantics; provenance remains distinguishable. | not_run | Not produced |
| P04 | Real OCCT and PicoGK adapters pass their declared conformance suites; unsupported requests and unavailable backends cannot silently fall back. | not_run | Not produced |
| P05 | Representation, conversion and numerical tolerances are explicit; STEP, mesh and voxel claims remain distinct and geometry cannot imply physics acceptance. | not_run | Not produced |
| P06 | Parser/compiler handle malformed source, unit mismatches, invalid dimensions, oversize input and version errors with bounded execution and source diagnostics. | not_run | Not produced |
| P07 | Language-to-beam execution rejects 5/10 mm and accepts 15 mm under the existing reviewed 200x10 mm / 50 N reference case; fixed requirements never change. | not_run | Not produced |
| P08 | Transaction failure, stale edits, cancellation, restart and rollback cannot mix revisions or overwrite previously accepted results. | not_run | Not produced |
| P09 | Expired or altered checkpoint artifacts cannot be restored with old evidence; rebuild creates a new run and rechecks its requirements. | not_run | Not produced |
| P10 | Independent tampering of geometry, semantic bindings, reports and previews is detected; required missing/ambiguous evidence blocks acceptance. | not_run | Not produced |
| P11 | CLI, Python, HTTP and MCP enforce the same validation/review/acceptance policy and preserve existing API compatibility. | not_run | Not produced |
| P12 | Identity, access control, path restrictions, idempotency and resource limits apply across every transport and worker. | not_run | Not produced |
| P13 | Browser editor/form/AI, review, diagnostics, actual preview, compare, rollback and downloads work across refresh and provider-unavailable cases. | not_run | Not produced |
| P14 | Source+IR+lock+evidence packages replay within declared numerical tolerances; migration is explicit and clean-runtime/native/provider evidence is clearly distinguished. | not_run | Not produced |

Use native kernel workers for P04/P07 and actual UI artifacts for P13. Fixture AI and real provider behavior must be reported separately. The offline language/core path must work without credentials. P14 records exact source/compiler/IR/adapter/native/solver versions and geometry comparison tolerances. Existing V15 remains a live-AI qualification requirement; a new AI language prompt/schema needs its own bounded live corpus before release claims.

Target reference demonstration: the beam .mir fixture yields the existing reviewed candidate results (5/10 rejected, 15 accepted). The PicoGK capability demonstrates its own declared geometry-only result; it is not forced through the beam solver. Shared adapter fixtures compare only supported common operations.

No P gate substitutes for E03 family qualification or E04 physical/manufacturing evidence. Numerical convergence within an idealized beam model is not physical validation. Failed/missing mandatory evidence is a release blocker.
