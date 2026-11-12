# B04 — Adapt the existing deterministic beam workflow

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B03](B03-contracts.md).  
Release checks touched: V02, V06, V07, V08, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- backend/engineering/cantilever.py and registry registration.
- Adapter evidence mapping and compatibility coverage.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/engineering/spec.py](../../../../backend/engineering/spec.py)
- [backend/engineering/service.py](../../../../backend/engineering/service.py)
- [backend/engineering/beam_fea.py](../../../../backend/engineering/beam_fea.py)
- [backend/engineering/verify.py](../../../../backend/engineering/verify.py)
- [backend/verification/acceptance.py](../../../../backend/verification/acceptance.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement the cantilever adapter that converts a reviewed brief and one selected thickness into the existing CantileverSpec.
2. Delegate to the existing builder/acceptance/verifier and translate its evidence into the shared result schema.
3. Preserve independent STEP reimport, measured dimensions/inertia, beam-model checks and exact artifact association.
4. Register the adapter under a fixed capability/version; preserve the old generate route and candidate-order behavior.

## Accepted end result

- [ ] Old and new paths agree on the reference candidate verdicts and measured values.
- [ ] No duplicate beam solver or geometry oracle is introduced.
- [ ] Unsupported feature/geometry cannot receive a beam acceptance claim.
- [ ] Every result identifies the candidate, brief, native file and verifier version.
- [ ] Unavailable native work remains unavailable.

## Verification

- Existing native/service/API suites plus adapter equivalence tests.
- Deliberately valid-but-wrong B-reps and changed-root-section cases.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B04.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B05](B05-brief-preparation.md), [B07](B07-candidate-search.md), [B08](B08-acceptance-policy.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B04 — Adapt the existing deterministic beam workflow. Task card: docs/plans/checked-engineering-v1/tasks/B04-cantilever-adapter.md. Prerequisites: B03. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
