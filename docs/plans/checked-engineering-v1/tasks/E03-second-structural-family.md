# E03 — Define and qualify a second structural family

Phase: expansion; separate from V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B14](B14-release-handoff.md).  
Release checks touched: V05, V06, V07, V08, V12 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- A selected capability contract followed by its implementation and qualification evidence.
- Explicit blockers if domain decisions or solver/runtime access are missing.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## External or conditional prerequisites

- Domain engineer/family decision before dependent implementation.

If these are missing, complete independent preparation and record a blocked handoff. Do not fabricate approval, experiments or capability qualification.

## Read before implementation

- backend/engineering/capabilities.py — planned path; inspect actual predecessor implementation before assuming it exists.
- backend/engineering/contracts.py — planned path; inspect actual predecessor implementation before assuming it exists.

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Begin with a scoped family decision and domain review: document geometry, loads/supports, materials, manufacturing objective, required evidence and exclusions before implementation.
2. If a family has not been selected, produce the concrete capability proposal and identify the needed decision; do not invent an approved physics envelope.
3. Implement the selected family as a separate adapter with suitable geometry predicates and a solver justified by the declared model.
4. For 3D stress cases, implement meshing, geometry-bound load/support mapping, independent benchmarks, convergence checks and treatment of stress singularities.
5. Qualify the family through input, native geometry, physics, failure and package tests before enabling it.

## Accepted end result

- [ ] A documented family-specific contract is approved before solver implementation.
- [ ] The family has its own verified benchmark cases and failure cases.
- [ ] Beam evidence is never reused as proof for unsupported geometry.
- [ ] Every new advertised claim has a corresponding acceptance gate.

## Verification

- Family-specific native and independent numerical benchmarks.
- Shared contract/lifecycle regression tests and out-of-envelope cases.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/E03.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: the relevant release/domain integration review; do not invent another task's completion. They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E03 — Define and qualify a second structural family. Task card: docs/plans/checked-engineering-v1/tasks/E03-second-structural-family.md. Prerequisites: B14. Additional gate: Domain engineer/family decision before dependent implementation. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
