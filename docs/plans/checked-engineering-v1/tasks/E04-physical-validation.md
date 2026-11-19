# E04 — Establish physical and manufacturing evidence

Phase: expansion; separate from V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B14](B14-release-handoff.md).  
Release checks touched: V08, V12 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Test protocol, measurement templates and, when physically executed, a revision-bound validation report.
- Manufacturing evidence checklist and specific outstanding qualification work.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## External or conditional prerequisites

- Requires physical specimens, equipment and a responsible operator; a code-only session cannot complete the experimental gate.
- Additional families require their own completed E03-equivalent capability contract.

If these are missing, complete independent preparation and record a blocked handoff. Do not fabricate approval, experiments or capability qualification.

## Read before implementation

- [examples/engineering/cantilever.json](../../../../examples/engineering/cantilever.json)
- [backend/engineering/verify.py](../../../../backend/engineering/verify.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Define a beam specimen/fixture and measurement protocol, uncertainty budget and acceptance limits before testing.
2. Record material/stock source, dimensions, manufacturing process, support conditions and calibration of measurement equipment.
3. With an available authorized physical setup, test several reviewed thicknesses and controlled load levels within the model's supported range.
4. Compare observations to predictions, investigate clamp/material/manufacturing effects, and bind measurements to exact specimen/CAD revisions.
5. Add process-specific tolerances, inspection instructions and responsible engineering release only for claims the evidence supports.

## Accepted end result

- [ ] Physical observations are distinguishable from simulations and copied fixtures.
- [ ] Uncertainty and comparison limits were fixed before measurement.
- [ ] Reports identify actual specimens, material, load, geometry revision and test conditions.
- [ ] No manufactured/tested/certified claim is issued without the respective evidence.

## Verification

- Physical protocol execution and data-validation checks when setup exists.
- Traceability and numerical comparison tooling tests.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/E04.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: the relevant release/domain integration review; do not invent another task's completion. They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E04 — Establish physical and manufacturing evidence. Task card: docs/plans/checked-engineering-v1/tasks/E04-physical-validation.md. Prerequisites: B14. Additional gate: Requires physical specimens, equipment and a responsible operator; a code-only session cannot complete the experimental gate. Additional families require their own completed E03-equivalent capability contract. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
