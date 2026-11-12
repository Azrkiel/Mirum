# B14 — Prepare the reproducible first release

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B13](B13-release-verification.md).  
Release checks touched: V02, V10, V11, V12, V13, V14, V15 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Reproducible release setup, operator/user documentation and final integration handoff.
- Clearly scoped release candidate; no automatic production change.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [README.md](../../../../README.md)
- [Dockerfile.verify](../../../../Dockerfile.verify)
- [docker-compose.yml](../../../../docker-compose.yml)
- [backend/requirements.txt](../../../../backend/requirements.txt)
- [frontend/requirements.txt](../../../../frontend/requirements.txt)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Pin and document the supported runtime, dependencies, volume ownership and single-process deployment assumptions.
2. Create fresh-environment typed setup/smoke instructions, AI setup guidance, capability descriptions, examples and troubleshooting.
3. Verify startup without provider credentials for typed use, package download integrity, expiry/restart behavior and supported feature flags.
4. Record the release candidate commit/image, V01–V15 evidence, model/capability/policy versions and rollback/migration instructions.
5. Prepare a branch-to-main integration handoff; do not merge, publish or deploy without a separate instruction.

## Accepted end result

- [ ] Fresh setup reproduces the reference example and all release claims match B13 evidence.
- [ ] UI/backend feature availability and supported models agree.
- [ ] Complete ZIPs identify the exact accepted revision and replay inputs.
- [ ] Installation, restart, expiry and recovery steps are usable by another session.
- [ ] If live AI remains unqualified, label the result a typed-only candidate rather than complete V1.

## Verification

- Fresh environment build and real HTTP/browser smoke.
- Final documented setup and package replay checks.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B14.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [E01](E01-picogk-adapter.md), [E02](E02-glider-adapter.md), [E03](E03-second-structural-family.md), [E04](E04-physical-validation.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B14 — Prepare the reproducible first release. Task card: docs/plans/checked-engineering-v1/tasks/B14-release-handoff.md. Prerequisites: B13. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
