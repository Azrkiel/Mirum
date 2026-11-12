# B07 — Evaluate the reviewed candidate choices

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B04](B04-cantilever-adapter.md), [B05](B05-brief-preparation.md).  
Release checks touched: V02, V03, V07, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- backend/engineering/search.py and structured attempt stream/history.
- Preserved legacy search compatibility.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/engineering/service.py](../../../../backend/engineering/service.py)
- [backend/engineering/spec.py](../../../../backend/engineering/spec.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement fixed-design and bounded search using the finalized, normalized candidate list; validate the entire set before any native work.
2. Evaluate at most four unique thicknesses in ascending order and stop after the first accepted candidate.
3. Preserve fixed requirements and record per-attempt candidate identity, native measurements, required checks and rejection reasons.
4. Stop on unavailable evidence; expose cancellation/deadline hooks and progress events for B09.
5. Preserve caller-supplied ordering on the legacy endpoint.

## Accepted end result

- [ ] The reference sequence is rejected, rejected, accepted with 15 mm selected.
- [ ] All-fail means only that no reviewed candidate passed.
- [ ] No hidden AI call, requirement change, clipping or search expansion occurs.
- [ ] Single-design mode performs exactly one attempt.
- [ ] Attempt records are sufficient for policy checks and later replay.

## Verification

- Selection, all-fail, fixed-mode, preflight, cancellation-hook and unavailable-stage tests.
- Real adapter execution of the reference sequence.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B07.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B08](B08-acceptance-policy.md), [B09](B09-job-lifecycle.md), [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B07 — Evaluate the reviewed candidate choices. Task card: docs/plans/checked-engineering-v1/tasks/B07-candidate-search.md. Prerequisites: B04, B05. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
