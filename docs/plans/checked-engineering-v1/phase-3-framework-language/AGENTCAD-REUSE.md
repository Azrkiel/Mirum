# AgentCAD source review and adoption plan

Reference: lizaixi01/AgentCAD at e9f2e904eb21a2171ad41ffe35cae676f8aaf05b. This records the prior source review, not a fresh execution of upstream tests. Current source says 3D model construction was removed; the FreeCAD component supports the viewer. AgentCAD is primarily a drawing runtime, not Mirum's physics engine.

| Source | Pattern | Mirum treatment |
|---|---|---|
| [ops/registry.py](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/ops/registry.py) | Typed operation metadata and schemas | F02: independently implement a small physics-aware registry |
| [spec.py](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/spec.py) | Spec compiles to construction operations plus requirements | F05: separate immutable requirement plan from build plan |
| [engine.py](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/engine.py) | Prevalidate batches; discard failed edits | F06: staged native revisions using existing Mirum jobs |
| [store.py](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/store.py) | Checkpoints, pending commits, journals and recovery | F06: adapt the transaction concepts, retaining Mirum storage/expiry policy |
| [validate](https://github.com/lizaixi01/AgentCAD/tree/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/validate) | Reread saved output and declare coverage | F07: native STEP/STL measurements and mandatory coverage |
| [tampering tests](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/tests/test_e_tamper.py) | Alter file independently of the generator | F07/F10: add equivalent Mirum geometry/evidence adversaries |
| [errors.py](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/src/agentcad/errors.py) | Structured category, retryability and partial-change state | F08: add source/requirement spans and stable recovery semantics |
| [README](https://github.com/lizaixi01/AgentCAD/blob/e9f2e904eb21a2171ad41ffe35cae676f8aaf05b/README.md) | One runtime via Python/CLI/HTTP/MCP; live revision viewer | F08/F09: common service and evidence-bound comparison |

## What does not transfer directly

DXF handles/tags do not solve arbitrary 3D face naming. Drawing validity does not establish beam/thermal/flow performance. Some generic requirement checks warn about unknown fields; Mirum's mandatory semantics must reject them. Upstream sandbox claims do not replace Mirum's native-process controls. Published benchmark outcomes are not evidence for a Mirum port.

Keep drawing-specific code as a possible future 2D adapter, not an implicit dependency of the beam or MEL. Do not import upstream examples, agent transcripts, evidence ZIPs, generated viewers or benchmark datasets as application source.

## Licensing and reuse gate

README and pyproject declare MIT; the reviewed tree did not contain a standalone root license file. Before a code copy, F01 must establish applicable full license/copyright notice, pinned provenance, dependency and bundled-asset terms, and retained attribution. Until resolved, independently implement documented architectural ideas and record their inspiration. This phase plan copies no AgentCAD implementation.

A reuse ledger records upstream path/SHA, selected pattern, direct-copy versus reimplementation decision, license evidence, modifications and targeted tests. Optional AutoCAD/FreeCAD integrations have separate runtime/dependency requirements; they are not automatically introduced by learning from AgentCAD.
