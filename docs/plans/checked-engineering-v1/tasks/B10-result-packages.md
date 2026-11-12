# B10 — Publish complete revision-bound packages

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B08](B08-acceptance-policy.md), [B09](B09-job-lifecycle.md).  
Release checks touched: V08, V10, V11, V12 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Package/report publisher, archive download support and manifest schema.
- Storage/expiry integration with jobs and clear diagnostic packages.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/artifacts.py](../../../../backend/artifacts.py)
- [backend/verification/acceptance.py](../../../../backend/verification/acceptance.py)
- [backend/dragsters/service.py](../../../../backend/dragsters/service.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement bundles.py using exact accepted bytes; produce all files specified in CONTRACT.md without a new CAD execution.
2. Build a manifest over payloads and store manifest/archive digests outside the archive; generate deterministic plain-language reports from typed evidence.
3. Use a dedicated namespace, explicit file allowlist and atomic staging/rename; integrate B09's terminal publication transaction and crash reconciliation.
4. Reserve storage at admission; enforce 32 MiB/run, 512 MiB/50 runs, six-hour bundle lifetime and seven-day compact record lifetime.
5. Implement download leases/expiry and diagnostics-only rejected packages; keep legacy sweeps from evicting new bundles.

## Accepted end result

- [ ] Required files and fingerprints match before accepted publication.
- [ ] Preview comes from the measured STEP revision.
- [ ] Write failure, disk full or missing report produces no partially accepted result.
- [ ] Unexpired packages survive legacy cleanup; active downloads finish safely.
- [ ] Saved ZIP is self-contained and rejected packages have no accepted geometry.

## Verification

- Artifact integrity/tampering, atomic failure injection, archive layout and MIME/allowlist tests.
- Capacity reservation, expiry, active-download and legacy-sweeper tests.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B10.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B11](B11-versioned-api.md), [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B10 — Publish complete revision-bound packages. Task card: docs/plans/checked-engineering-v1/tasks/B10-result-packages.md. Prerequisites: B08, B09. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
