# Copy-ready task prompts

Run one prompt in a fresh Codex task/session against engineering/checked-design-v1. Each prompt contains its branch and document context and authorizes only that task's commit/push. It does not authorize merging main or deployment. Follow the recommended order in README.md; prerequisite code/handoffs must actually exist.

## B01 — Establish a reproducible baseline

[Task card](tasks/B01-baseline.md) · prerequisites: none

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B01 — Establish a reproducible baseline. Task card: docs/plans/checked-engineering-v1/tasks/B01-baseline.md. Prerequisites: none. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B02 — Define the bounded beam capability

[Task card](tasks/B02-capability.md) · prerequisites: B01

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B02 — Define the bounded beam capability. Task card: docs/plans/checked-engineering-v1/tasks/B02-capability.md. Prerequisites: B01. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B03 — Create reviewed briefs and evidence schemas

[Task card](tasks/B03-contracts.md) · prerequisites: B02

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B03 — Create reviewed briefs and evidence schemas. Task card: docs/plans/checked-engineering-v1/tasks/B03-contracts.md. Prerequisites: B02. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B04 — Adapt the existing deterministic beam workflow

[Task card](tasks/B04-cantilever-adapter.md) · prerequisites: B03

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B04 — Adapt the existing deterministic beam workflow. Task card: docs/plans/checked-engineering-v1/tasks/B04-cantilever-adapter.md. Prerequisites: B03. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B05 — Prepare and finalize briefs without AI

[Task card](tasks/B05-brief-preparation.md) · prerequisites: B03, B04

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B05 — Prepare and finalize briefs without AI. Task card: docs/plans/checked-engineering-v1/tasks/B05-brief-preparation.md. Prerequisites: B03, B04. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B06 — Propose structured briefs with AI

[Task card](tasks/B06-ai-preparation.md) · prerequisites: B05

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B06 — Propose structured briefs with AI. Task card: docs/plans/checked-engineering-v1/tasks/B06-ai-preparation.md. Prerequisites: B05. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B07 — Evaluate the reviewed candidate choices

[Task card](tasks/B07-candidate-search.md) · prerequisites: B04, B05

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B07 — Evaluate the reviewed candidate choices. Task card: docs/plans/checked-engineering-v1/tasks/B07-candidate-search.md. Prerequisites: B04, B05. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B08 — Make acceptance depend on complete evidence

[Task card](tasks/B08-acceptance-policy.md) · prerequisites: B03, B04, B07

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B08 — Make acceptance depend on complete evidence. Task card: docs/plans/checked-engineering-v1/tasks/B08-acceptance-policy.md. Prerequisites: B03, B04, B07. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B09 — Persist and control engineering jobs

[Task card](tasks/B09-job-lifecycle.md) · prerequisites: B07, B08

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B09 — Persist and control engineering jobs. Task card: docs/plans/checked-engineering-v1/tasks/B09-job-lifecycle.md. Prerequisites: B07, B08. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B10 — Publish complete revision-bound packages

[Task card](tasks/B10-result-packages.md) · prerequisites: B08, B09

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B10 — Publish complete revision-bound packages. Task card: docs/plans/checked-engineering-v1/tasks/B10-result-packages.md. Prerequisites: B08, B09. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B11 — Expose the common workflow through APIs

[Task card](tasks/B11-versioned-api.md) · prerequisites: B05, B06, B09, B10

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B11 — Expose the common workflow through APIs. Task card: docs/plans/checked-engineering-v1/tasks/B11-versioned-api.md. Prerequisites: B05, B06, B09, B10. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B12 — Build the checked beam interface

[Task card](tasks/B12-user-workflow.md) · prerequisites: B11

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B12 — Build the checked beam interface. Task card: docs/plans/checked-engineering-v1/tasks/B12-user-workflow.md. Prerequisites: B11. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B13 — Qualify the end-to-end behavior

[Task card](tasks/B13-release-verification.md) · prerequisites: B06, B07, B08, B09, B10, B11, B12

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B13 — Qualify the end-to-end behavior. Task card: docs/plans/checked-engineering-v1/tasks/B13-release-verification.md. Prerequisites: B06, B07, B08, B09, B10, B11, B12. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## B14 — Prepare the reproducible first release

[Task card](tasks/B14-release-handoff.md) · prerequisites: B13

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B14 — Prepare the reproducible first release. Task card: docs/plans/checked-engineering-v1/tasks/B14-release-handoff.md. Prerequisites: B13. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## E01 — Integrate PicoGK with honest geometry scope

[Task card](tasks/E01-picogk-adapter.md) · prerequisites: B14

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E01 — Integrate PicoGK with honest geometry scope. Task card: docs/plans/checked-engineering-v1/tasks/E01-picogk-adapter.md. Prerequisites: B14. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## E02 — Integrate the glider CEM without upgrading its claims

[Task card](tasks/E02-glider-adapter.md) · prerequisites: B14

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E02 — Integrate the glider CEM without upgrading its claims. Task card: docs/plans/checked-engineering-v1/tasks/E02-glider-adapter.md. Prerequisites: B14. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## E03 — Define and qualify a second structural family

[Task card](tasks/E03-second-structural-family.md) · prerequisites: B14

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E03 — Define and qualify a second structural family. Task card: docs/plans/checked-engineering-v1/tasks/E03-second-structural-family.md. Prerequisites: B14. Additional gate: Domain engineer/family decision before dependent implementation. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## E04 — Establish physical and manufacturing evidence

[Task card](tasks/E04-physical-validation.md) · prerequisites: B14

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E04 — Establish physical and manufacturing evidence. Task card: docs/plans/checked-engineering-v1/tasks/E04-physical-validation.md. Prerequisites: B14. Additional gate: Requires physical specimens, equipment and a responsible operator; a code-only session cannot complete the experimental gate. Additional families require their own completed E03-equivalent capability contract. Start by checking those prerequisites, then carry this task through its stated accepted result.
```

## E05 — Expand AI across registered capabilities

[Task card](tasks/E05-broader-design-generation.md) · prerequisites: E01, E02

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: E05 — Expand AI across registered capabilities. Task card: docs/plans/checked-engineering-v1/tasks/E05-broader-design-generation.md. Prerequisites: E01, E02. Additional gate: E03/E04 are required for any new structural or physical/manufacturing claim that depends on them; geometry-only/screening routing preserves its narrower scope. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
