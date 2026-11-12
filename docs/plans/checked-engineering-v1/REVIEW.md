# Planning package review

Reviewed baseline: d4189b6190c94d3069d9f5c364a7a722178dd775. Documentation-only package; no application runtime/test pass is claimed.

## Planning gaps addressed

| Finding | Resolution |
|---|---|
| Inconsistent meaning of acceptance across workflows | Capability-specific mandatory checks and separate lifecycle/outcome. |
| Stale user review after changed input or policy | Immutable finalized revisions and version checks. |
| AI could change requirements while searching | Reviewed fixed requirements and bounded candidate list; no model calls during execution. |
| “Best” could imply a global optimizer | Ascending first passing thickness among at most four reviewed options. |
| Existing artifact formats do not include the complete package | B10 explicitly adds archives/history/source/reports in an isolated namespace. |
| Manifest/archive hashes could be circular | Payload manifest inside; manifest/ZIP digests outside. |
| Container replacement could lose supposedly durable jobs | Mounted data volume and explicit recovery tests. |
| Cancellation could race publication | Single terminal commit and cancellation precedence. |
| Legacy cleanup could delete new results | Isolated namespace and unexpired-capacity reservation. |
| Replays could be judged by unstable export metadata | Numerical reproducibility separate from exact per-run byte identity. |
| Form/AI origin could change semantic hashes | Origin/timestamps kept outside normalized meaning hashes. |
| New sorting could alter old API behavior | Legacy caller ordering retained. |
| B05 could imply a UI before the API exists | B05 service contract; B11 transport; B12 full UI. |
| B09 could depend circularly on B10 | B09 defines publisher seam and recovery contract; B10 implements atomic publication. |
| Shared docs could have competing completion checkboxes | STATUS.md is authoritative; task cards/PLAN are acceptance specifications. |
| Hardware/domain tasks could be “completed” with invented evidence | E03/E04 explicitly stop dependent work on missing family/physical inputs. |
| Mocked providers could be called live qualification | Separate bounded live benchmark gates full AI release. |

## Package checks

Before publishing, validate: all 19 task cards/prompts exist; all 15 release checks have task owners; IDs are unique; dependencies are acyclic; recommended sequence respects them; internal links resolve; referenced existing source paths exist at the recorded baseline; all statuses start uncompleted; no application files enter the commit.

Structural validation passed before publication: 29 files, 19 task cards, 19 full prompts, 15 release checks, 297 relative links, unique task IDs, acyclic dependencies and a valid recommended execution order. All application tasks remain not_started and release checks remain not_run. Every task card has its goal/output, implementation steps, accepted end result, verification and integration handoff sections. All changed paths are inside this new documentation package. These are documentation checks, not runtime or engineering qualification.

## Explicit prerequisites and later decisions

B01 must reproduce the native runtime. Live provider access/budget is needed for V15, while typed operation remains offline-capable. Actual material and load inputs need identified sources. E03 needs a selected domain-reviewed family and E04 needs specimens, equipment and an operator. Optional Zoo access/licensing/cost/export evaluation belongs to E05, not the V1 critical path.

New implementation findings must be resolved or recorded as specific blockers before the affected acceptance gate. This planning review does not guarantee the absence of every future implementation issue.
