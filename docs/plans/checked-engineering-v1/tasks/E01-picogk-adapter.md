# E01 — Integrate PicoGK with honest geometry scope

Phase: expansion; separate from V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B14](B14-release-handoff.md).  
Release checks touched: V05, V07, V08, V10, V11 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- PicoGK capability adapter, packages and accurately scoped UI/API entry.
- Native integration evidence and follow-on physics qualification backlog.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [picogk-builder/Program.cs](../../../../picogk-builder/Program.cs)
- [backend/engineering/regen_spec.py](../../../../backend/engineering/regen_spec.py)
- [backend/engineering/regen_cli.py](../../../../backend/engineering/regen_cli.py)
- [backend/engineering/regen_mesh.py](../../../../backend/engineering/regen_mesh.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Register the existing regen geometry family with its own specification, voxel allowances, checks, runtime/library provenance and STL exports.
2. Wrap the fixed builder/verifier in the shared jobs/evidence/package interfaces without applying the beam's runtime budget blindly.
3. Supply an explicit minimal worker environment and preserve required native library paths.
4. Run fresh builder/verifier control cases and defect cases; translate reports without adding thermal/flow/structural claims.

## Accepted end result

- [ ] STL/report association, resource policy and native versions are recorded.
- [ ] Existing geometry checks remain mandatory and known defects reject.
- [ ] No editable STEP or physics acceptance is advertised.
- [ ] Current native evidence exists; historical stored reports alone are insufficient.

## Verification

- Existing regen schema/mesh/CLI suites plus fresh native builder adapter integration.
- Known-defect, resource budget and artifact integrity tests.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/E01.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [E05](E05-broader-design-generation.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E01 — Integrate PicoGK with honest geometry scope. Task card: docs/plans/checked-engineering-v1/tasks/E01-picogk-adapter.md. Prerequisites: B14. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
