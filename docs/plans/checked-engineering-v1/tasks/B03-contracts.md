# B03 — Create reviewed briefs and evidence schemas

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B02](B02-capability.md).  
Release checks touched: V01, V04, V08, V09 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- backend/engineering/contracts.py and representative valid/invalid fixtures.
- Written schema and hash boundaries for downstream consumers.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/engineering/spec.py](../../../../backend/engineering/spec.py)
- [backend/cem/core.py](../../../../backend/cem/core.py)
- [backend/verification/report.py](../../../../backend/verification/report.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement strict models for EngineeringBrief, CandidateSpec, CheckResult, EngineeringRun and capability references in contracts.py.
2. Separate immutable fixed requirements, reviewed candidate choices, material/allowable references and acknowledged support/load assumptions.
3. Normalize supported quantities; reject missing/conflicting/unsupported input. Preserve field origin and clarification needs without treating AI guesses as supplied material facts.
4. Define fixed-requirement and brief digests over normalized meaning and versions; exclude record IDs, timestamps and input-mode provenance. Store candidate identity separately.
5. Specify lifecycle/outcome enums, version handling and required evidence fields before consumers are implemented.

## Accepted end result

- [ ] Nonfinite values, booleans as numbers, unknown fields and unsupported units fail validation.
- [ ] Kg is not silently interpreted as N; conversion requires explicit reviewed meaning.
- [ ] Equivalent normalized form/AI inputs have matching semantic digests.
- [ ] Material or force changes change the requirement digest; thickness choices change the brief digest.
- [ ] Edited input creates a new revision and invalidates prior review.

## Verification

- Schema rejection, canonicalization, unit conversion and digest mutation tests.
- Model round-trip fixtures shared with downstream tasks.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B03.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B04](B04-cantilever-adapter.md), [B05](B05-brief-preparation.md), [B08](B08-acceptance-policy.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B03 — Create reviewed briefs and evidence schemas. Task card: docs/plans/checked-engineering-v1/tasks/B03-contracts.md. Prerequisites: B02. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
