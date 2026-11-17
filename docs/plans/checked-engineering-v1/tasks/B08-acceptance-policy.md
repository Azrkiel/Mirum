# B08 — Make acceptance depend on complete evidence

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B03](B03-contracts.md), [B04](B04-cantilever-adapter.md), [B07](B07-candidate-search.md).  
Release checks touched: V03, V07, V08, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- backend/engineering/policy.py and evidence-validation contract.
- Explicit decision reasons consumable by reports and UI.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/verification/acceptance.py](../../../../backend/verification/acceptance.py)
- [backend/verification/report.py](../../../../backend/verification/report.py)
- [backend/engineering/service.py](../../../../backend/engineering/service.py)
- [backend/engineering/verify.py](../../../../backend/engineering/verify.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement policy.py against the versioned mandatory-check list, validating report schemas and all expected identity associations.
2. Separate conclusive rejection from insufficient/unavailable evidence; map these to the lifecycle/outcome contract.
3. Require delivered geometry and report identities to agree, and reject unsupported certification/manufacturing claims.
4. Ensure explanations are derived from typed evidence; keep legacy geometry-only output scope unchanged.

## Accepted end result

- [ ] Missing, not-evaluated or unavailable mandatory checks prevent acceptance.
- [ ] A pass flag without complete supporting fields is insufficient.
- [ ] Stale brief/candidate/STEP associations fail closed.
- [ ] Geometry-only acceptance never becomes physics acceptance by label.
- [ ] No AI text can override an engineering decision.

## Verification

- Missing/malformed/false-pass/stale-evidence fixtures and negative claims tests.
- Native failure examples plus compatibility checks for legacy outputs.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B08.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B09](B09-job-lifecycle.md), [B10](B10-result-packages.md), [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B08 — Make acceptance depend on complete evidence. Task card: docs/plans/checked-engineering-v1/tasks/B08-acceptance-policy.md. Prerequisites: B03, B04, B07. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
