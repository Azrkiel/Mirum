# B13 — Qualify the end-to-end behavior

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B06](B06-ai-preparation.md), [B07](B07-candidate-search.md), [B08](B08-acceptance-policy.md), [B09](B09-job-lifecycle.md), [B10](B10-result-packages.md), [B11](B11-versioned-api.md), [B12](B12-user-workflow.md).  
Release checks touched: V01, V02, V03, V04, V05, V06, V07, V08, V09, V10, V11, V12, V13, V14, V15 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Release qualification report and updated acceptance evidence matrix.
- Resolved regressions or a precise blocked-release report.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [Dockerfile.verify](../../../../Dockerfile.verify)
- [pytest.ini](../../../../pytest.ini)
- [backend/tests](../../../../backend/tests)
- [frontend](../../../../frontend)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Assemble the V01–V15 release matrix and map each requirement to an actual automated or observed check.
2. Run focused unit/service tests, real native geometry/physics, HTTP, lifecycle/storage and browser tests; then run the applicable existing regression suite.
3. Exercise deliberate wrong-but-valid geometry and unavailable/stale-evidence failures, avoiding tests that only mirror implementation.
4. Run the bounded 30-case live-provider benchmark if configured and authorized by the task session; otherwise explicitly block AI release qualification while preserving offline verification.
5. Retain exact commands, versions, counts, hashes, observed limitations and the tested implementation commit.

## Accepted end result

- [ ] Every V01–V15 row has reproducible evidence or an explicit release blocker.
- [ ] Native tests execute CadQuery/OCP; provider fixtures are not labelled live.
- [ ] The reference selection and rejected/no-artifact behavior pass end to end.
- [ ] No known mandatory regression is waived into success.
- [ ] Live benchmark has zero silent requirement drops on the corpus and at least 8/10 complete requests produce correct reviewable drafts.

## Verification

- All checks in ACCEPTANCE.md, with established commands from B01 and task handoffs.
- Fresh artifact verification and browser inspection of the actual run.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B13.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B14](B14-release-handoff.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B13 — Qualify the end-to-end behavior. Task card: docs/plans/checked-engineering-v1/tasks/B13-release-verification.md. Prerequisites: B06, B07, B08, B09, B10, B11, B12. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
