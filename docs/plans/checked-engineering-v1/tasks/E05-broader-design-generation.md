# E05 — Expand AI across registered capabilities

Phase: expansion; separate from V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [E01](E01-picogk-adapter.md), [E02](E02-glider-adapter.md).  
Release checks touched: V01, V05, V08, V13, V15 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Broader typed proposal/routing workflow using qualified capabilities.
- Optional backend decision/integration evidence and remaining language-design questions.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## External or conditional prerequisites

- E03/E04 are required for any new structural or physical/manufacturing claim that depends on them; geometry-only/screening routing preserves its narrower scope.

If these are missing, complete independent preparation and record a blocked handoff. Do not fabricate approval, experiments or capability qualification.

## Read before implementation

- backend/engineering/capabilities.py — planned path; inspect actual predecessor implementation before assuming it exists.
- backend/engineering/proposer.py — planned path; inspect actual predecessor implementation before assuming it exists.

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Allow AI to propose a registered capability and its typed parameters while keeping the reviewed brief and evidence boundary intact.
2. Route unsupported requests to explicit clarification/rejection; never substitute a less capable family silently.
3. Evaluate an optional Zoo backend only for a documented need, with access/licensing/cost/export-fidelity investigation before integration.
4. Preserve native representation and conversion tolerances; add cross-backend identity/claim tests.
5. Use E03/E04 evidence whenever the selected family's claim requires structural or physical/manufacturing qualification.

## Accepted end result

- [ ] Every proposal names a supported capability/version and valid schema.
- [ ] Changing backend does not broaden acceptance scope.
- [ ] Conversions declare tested fidelity and cannot claim editable CAD without evidence.
- [ ] Capability-specific mandatory checks still control every result.

## Verification

- Cross-family routing, unsupported-request and claim-preservation tests.
- Backend adapter/export tests only for integrations actually implemented.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/E05.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: the relevant release/domain integration review; do not invent another task's completion. They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E05 — Expand AI across registered capabilities. Task card: docs/plans/checked-engineering-v1/tasks/E05-broader-design-generation.md. Prerequisites: E01, E02. Additional gate: E03/E04 are required for any new structural or physical/manufacturing claim that depends on them; geometry-only/screening routing preserves its narrower scope. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
