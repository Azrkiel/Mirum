# B06 — Propose structured briefs with AI

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B05](B05-brief-preparation.md).  
Release checks touched: V04, V05, V15 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- backend/engineering/proposer.py and preparation integration.
- Versioned extraction prompt/schema and 30-case benchmark inputs.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/config.py](../../../../backend/config.py)
- [backend/subagents/model_registry.py](../../../../backend/subagents/model_registry.py)
- [backend/agents.py](../../../../backend/agents.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Implement proposer.py using the configured provider and a constrained response model for fields, unresolved questions, unsupported requests and candidate proposals.
2. Route output through B03/B05 validation; retain source provenance and show all inferred values for review.
3. Enforce one preparation call and one repair call per draft revision, 45 seconds each, bounded request/response size, and cancellation; record provider/version/usage safely.
4. Handle absent credentials, model errors, partial output and contradictions without enabling generation or disabling form preparation.
5. Create fixtures for the live-evaluation corpus; defer actual provider qualification to B13 if access is not configured.

## Accepted end result

- [ ] AI outputs data, not executable Python in this path.
- [ ] Invented material/load/support facts cannot become unreviewed executable inputs.
- [ ] Malformed responses and unsupported features have visible outcomes.
- [ ] No provider calls occur after brief finalization during V1 execution.
- [ ] Offline fixtures are labelled as such; unavailable live qualification is recorded.

## Verification

- Provider-seam fixtures for valid, ambiguous, contradictory, unsupported and malformed responses.
- Deadline, output-size, call-count and no-provider fallback tests.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B06.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B11](B11-versioned-api.md), [B13](B13-release-verification.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B06 — Propose structured briefs with AI. Task card: docs/plans/checked-engineering-v1/tasks/B06-ai-preparation.md. Prerequisites: B05. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
