# B11 — Expose the common workflow through APIs

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B05](B05-brief-preparation.md), [B06](B06-ai-preparation.md), [B09](B09-job-lifecycle.md), [B10](B10-result-packages.md).  
Release checks touched: V01, V04, V08, V09, V10, V11, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Versioned API routes, request/response examples and OpenAPI descriptions.
- Transport compatibility and deployment access checks.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/main.py](../../../../backend/main.py)
- [backend/engineering/api.py](../../../../backend/engineering/api.py)
- [backend/tests/test_engineering_api.py](../../../../backend/tests/test_engineering_api.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Add api_v1.py and register the explicit routes in CONTRACT.md, delegating to prepared service interfaces.
2. Implement expected-revision draft updates/finalization and finalized-brief-only job submission; disallow replacement physical values in job requests.
3. Apply existing authentication/rate-limiting policy, typed errors, no-store responses where appropriate and idempotency handling.
4. Expose job progress, cancellation and safe allowlisted downloads; preserve existing endpoints and return expired versus unknown records appropriately.

## Accepted end result

- [ ] Stale revisions conflict rather than executing different input.
- [ ] Submit/poll/cancel/download never re-runs the builder implicitly.
- [ ] HTTP failures distinguish validation, conflict, overload, expiry and unavailable service.
- [ ] Job terminal outcomes match the policy and published package.
- [ ] Existing API tests remain valid.

## Verification

- Real TestClient/HTTP flows for form and fixture AI preparation, finalization, jobs and downloads.
- Auth, path containment, rate/admission, idempotency and old endpoint regressions.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B11.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B12](B12-user-workflow.md), [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B11 — Expose the common workflow through APIs. Task card: docs/plans/checked-engineering-v1/tasks/B11-versioned-api.md. Prerequisites: B05, B06, B09, B10. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
