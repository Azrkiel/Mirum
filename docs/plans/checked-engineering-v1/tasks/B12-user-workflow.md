# B12 — Build the checked beam interface

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B11](B11-versioned-api.md).  
Release checks touched: V04, V09, V14 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Complete Streamlit engineering workflow and user-facing copy.
- Browser evidence and state-management regression coverage.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [frontend/app.py](../../../../frontend/app.py)
- [frontend/requirements.txt](../../../../frontend/requirements.txt)
- [frontend/test_acceptance_evidence_ui.py](../../../../frontend/test_acceptance_evidence_ui.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Add engineering_ui.py and register a clearly scoped workflow using the new APIs.
2. Implement form/AI entry, missing-field resolution and a review page showing force direction/support, material reference, fixed requirements and permitted thicknesses.
3. Add start/cancel/progress and persist the relevant brief revision/job identity in session state; reconstruct state after refresh.
4. Render the accepted STEP-derived preview and candidate comparison; offer all report/package downloads with expiry information.
5. Represent unsupported, rejected, unavailable, cancelled and expired outcomes with actionable messages and prevent old-preview/new-report mixups.

## Accepted end result

- [ ] The reference example can be completed without API knowledge.
- [ ] Every field is reviewable and edits invalidate old confirmation.
- [ ] Refresh and multiple-tab behavior preserve correct revision identity.
- [ ] Accepted scope and unevaluated checks remain visible.
- [ ] Form mode works with AI unavailable.

## Verification

- Frontend state tests and browser flows against actual API/native output.
- Visual inspection of force/support presentation, real GLB, reports and downloads.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B12.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B12 — Build the checked beam interface. Task card: docs/plans/checked-engineering-v1/tasks/B12-user-workflow.md. Prerequisites: B11. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
