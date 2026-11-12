# E02 — Integrate the glider CEM without upgrading its claims

Phase: expansion; separate from V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B14](B14-release-handoff.md).  
Release checks touched: V05, V07, V08, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Glider adapter and consistent screened-result delivery.
- Compatibility evidence and remaining manufacturing-qualification work.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/cem/core.py](../../../../backend/cem/core.py)
- [backend/cem/api.py](../../../../backend/cem/api.py)
- [backend/cem/glider/service.py](../../../../backend/cem/glider/service.py)
- [backend/cem/evidence.py](../../../../backend/cem/evidence.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Map glider mission/discovery requirements and existing typed outputs into the common capability and job interface.
2. Preserve family support, closure, measured-mass, calibration, uncertainty and construction-mode distinctions.
3. Translate screened/rejected results without changing accepted_for_manufacture or engineering_certified restrictions.
4. Exercise existing CEM tests and integrated representative runs; maintain current routes for compatibility.

## Accepted end result

- [ ] Screening evidence remains screening evidence in API, UI and packages.
- [ ] Unsupported families/environments remain explicitly unsupported.
- [ ] Geometry, analysis and calibration provenance describe one revision.
- [ ] Existing CEM clients and checks continue working.

## Verification

- Existing CEM requirement, pipeline, geometry-integrity and closure suites.
- Cross-interface claim-preservation and package-integrity tests.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/E02.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [E05](E05-broader-design-generation.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E02 — Integrate the glider CEM without upgrading its claims. Task card: docs/plans/checked-engineering-v1/tasks/E02-glider-adapter.md. Prerequisites: B14. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
