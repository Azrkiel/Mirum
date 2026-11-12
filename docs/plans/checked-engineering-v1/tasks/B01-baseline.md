# B01 — Establish a reproducible baseline

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: none.  
Release checks touched: V02, V03, V06, V07 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- A reproducible runtime/baseline recipe and completed B01 handoff.
- Minimal documented prerequisite fixes only if baseline reproduction needs them.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [Dockerfile.verify](../../../../Dockerfile.verify)
- [requirements-mirum-verification.lock](../../../../requirements-mirum-verification.lock)
- [pytest.ini](../../../../pytest.ini)
- [backend/tests/test_engineering_spec.py](../../../../backend/tests/test_engineering_spec.py)
- [backend/tests/test_engineering_service.py](../../../../backend/tests/test_engineering_service.py)
- [backend/tests/test_engineering_native.py](../../../../backend/tests/test_engineering_native.py)
- [backend/tests/test_engineering_api.py](../../../../backend/tests/test_engineering_api.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Inspect the current remote branch, repository instructions, native dependency setup and relevant tests; record the implementation commit and runtime provenance.
2. Reproduce the existing typed cantilever example through native tests and a loopback HTTP request. Retain accepted STEP/GLB and a rejected physics response as local evidence.
3. Separate pre-existing failures from this work. Repair only a minimal prerequisite defect when it is necessary to establish the baseline; document broader defects as blockers.
4. Record exact commands, dependency/image versions, result counts, artifact hashes, process budgets, and any provider/runtime limitations in the handoff.

## Accepted end result

- [ ] The real Linux/OCP runtime builds and verifies the reference beam.
- [ ] Invalid dimensions and failed physics return no accepted CAD.
- [ ] Tests exercise real native geometry, not only mocks.
- [ ] No live provider call is required for the typed baseline.
- [ ] A fresh session can reproduce the recorded commands.

## Verification

- Existing test_engineering_spec.py, test_engineering_fea.py, test_engineering_native.py, test_engineering_service.py and test_engineering_api.py.
- Real loopback request and inspection of the returned geometry/preview.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B01.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B02](B02-capability.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B01 — Establish a reproducible baseline. Task card: docs/plans/checked-engineering-v1/tasks/B01-baseline.md. Prerequisites: none. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
