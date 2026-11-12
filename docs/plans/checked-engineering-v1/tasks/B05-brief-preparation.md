# B05 — Prepare and finalize briefs without AI

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B03](B03-contracts.md), [B04](B04-cantilever-adapter.md).  
Release checks touched: V01, V04, V09 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- backend/engineering/briefs.py with documented service methods and persistence interface.
- Typed example workflow usable before transport/UI integration.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/engineering/api.py](../../../../backend/engineering/api.py)
- [backend/engineering/spec.py](../../../../backend/engineering/spec.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement framework-independent draft create/read/edit/validate/finalize service methods; leave public API registration to B11 and the full UI to B12.
2. Return field-specific missing-input and unsupported-feature results; expose exactly what will be reviewed.
3. Finalize only the expected draft revision with explicit acknowledgement of the material source, assumptions and candidate choices.
4. Provide a repository interface for brief persistence and test it locally; B09 supplies the durable storage implementation. Ensure the finalized record is immutable.

## Accepted end result

- [ ] Typed preparation and finalization run offline without credentials.
- [ ] Incomplete or superseded drafts cannot produce executable briefs.
- [ ] Changed fields invalidate previous confirmation and use a new revision.
- [ ] Finalized capability/policy versions are checked before execution.
- [ ] Inputs remain visible and exportable for downstream UI/API consumers.

## Verification

- Draft revision, stale edit, duplicate finalization and no-provider tests.
- Service-level use of the complete reference brief.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B05.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B06](B06-ai-preparation.md), [B07](B07-candidate-search.md), [B11](B11-versioned-api.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B05 — Prepare and finalize briefs without AI. Task card: docs/plans/checked-engineering-v1/tasks/B05-brief-preparation.md. Prerequisites: B03, B04. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
