# B09 — Persist and control engineering jobs

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B07](B07-candidate-search.md), [B08](B08-acceptance-policy.md).  
Release checks touched: V09, V10, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Durable jobs/store implementation, brief-store adapter and documented publisher seam.
- Runtime volume/admission configuration and recovery procedure.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/process_runner.py](../../../../backend/process_runner.py)
- [backend/solver_slot.py](../../../../backend/solver_slot.py)
- [backend/engineering/api.py](../../../../backend/engineering/api.py)
- [backend/cem/api.py](../../../../backend/cem/api.py)
- [docker-compose.yml](../../../../docker-compose.yml)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement jobs.py/store.py with SQLite transactions, brief repository storage and a dedicated application-owned data directory under the permitted runtime tree.
2. Configure a writable persistent volume for release; keep native scratch separate and protect in-flight records.
3. Admit one active engineering job, share the applicable limit with the old engineering route, and enforce bounded total/per-stage execution.
4. Implement status/progress, idempotency, cancellation and one terminal-state commit with defined cancellation/completion precedence.
5. On restart reconcile interrupted jobs and filesystem staging. Terminate owned descendants and release resources on every exit. Detect/reject unsupported multi-process deployment.

## Accepted end result

- [ ] Duplicate same-content submissions reuse a job; changed content with the same key conflicts.
- [ ] Cancel/restart/crash/deadline leaves no orphan processes or accepted partial work.
- [ ] No job is accepted before durable publication; B10 integrates the publisher behind a defined seam.
- [ ] Persistent data survives the documented container-replacement procedure.
- [ ] Overload returns retry information and no hidden queue or budget expansion.

## Verification

- Process lifecycle/cancellation tests and real worker cleanup checks.
- SQLite/concurrent terminal transition tests, restart reconciliation and admission/legacy coexistence.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B09.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B10](B10-result-packages.md), [B11](B11-versioned-api.md), [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B09 — Persist and control engineering jobs. Task card: docs/plans/checked-engineering-v1/tasks/B09-job-lifecycle.md. Prerequisites: B07, B08. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
