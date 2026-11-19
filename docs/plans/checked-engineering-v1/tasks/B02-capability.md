# B02 — Define the bounded beam capability

Phase: V1  
Live state: see [STATUS.md](../STATUS.md).  
Prerequisites: [B01](B01-baseline.md).  
Release checks touched: V05, V07, V13 ([matrix](../ACCEPTANCE.md)).

## Goal and intended output

- Capability registry and adapter interface, suggested backend/engineering/capabilities.py.
- Documented supported envelope and versioned policy.

This task implements its bounded contribution to the [branch goal](../README.md) and [common contract](../CONTRACT.md). Completion of this task alone does not qualify the whole application.

## Read before implementation

- [backend/engineering/spec.py](../../../../backend/engineering/spec.py)
- [backend/engineering/verify.py](../../../../backend/engineering/verify.py)
- [backend/cem/core.py](../../../../backend/cem/core.py)

Read the predecessor handoffs and [INTEGRATION.md](../INTEGRATION.md). New module names from the master plan are suggestions; preserve the actual integrated interfaces.

## Specific implementation steps

1. Describe the supported rectangular cantilever family using a versioned CapabilityDescriptor; define the adapter contract and registry lookup without changing glider signatures.
2. Enumerate mandatory geometry and beam checks, permitted exports, supported units, current model limitations and explicit unsupported responses.
3. Define fixed-design and bounded-thickness modes, their reviewed inputs and ascending first-pass rule; preserve the old API's caller-order policy.
4. Encode numerical/resource policy identifiers and defaults from CONTRACT.md; qualify compute-budget feasibility using B01 evidence.

## Accepted end result

- [ ] Descriptor declares one capability/version and never implies general structural qualification.
- [ ] Holes, fillets, other supports/loads and non-slender sections are out of scope.
- [ ] Physics checks cannot be replaced by geometry validity.
- [ ] Selection means smallest passing choice in the reviewed set.
- [ ] Metadata is usable without initializing an AI provider.

## Verification

- Descriptor/registry contract tests for required checks, versions, exports and unsupported cases.
- Regression check that existing CEM imports and signatures remain compatible.

Use the reproduced B01 runtime and the task's actual files. Add meaningful behavioral regressions while implementing, and run the required native/API/browser checks for touched paths. Record what ran; a command suggestion is not evidence. Follow applicable repository review instructions.

## Integration handoff and next steps

Create handoffs/B02.md using [TEMPLATE.md](../handoffs/TEMPLATE.md). Record changed files/interfaces, tested source commit, exact checks/results, failure cases, remaining issues and how consumers should integrate. Update STATUS.md and any evidenced release-matrix rows.

Next direct consumers: [B03](B03-contracts.md). They may start only after all their own prerequisites are complete.

Commit/push this task only as authorized by its execution prompt. Preserve main and unrelated work.

## Copy-ready prompt

```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read the repository instructions, docs/plans/checked-engineering-v1/README.md, docs/plans/checked-engineering-v1/CONTRACT.md and docs/plans/checked-engineering-v1/INTEGRATION.md, then the task card named below. Check STATUS.md and prerequisite handoffs against the actual branch before starting. Implement only this task and the smallest necessary prerequisite repairs. Preserve existing behavior and unrelated work. Run the task's meaningful checks and record actual evidence and limitations. Update STATUS.md and create handoffs/<TASK_ID>.md from the template; do not mark an unverified or blocked task complete. This request authorizes committing and pushing this task's completed changes and handoff to engineering/checked-design-v1; use a safe isolated worktree if needed, and never force-push. Do not merge main or deploy. Report the resulting commit, acceptance evidence, blockers and next eligible task.

Task: B02 — Define the bounded beam capability. Task card: docs/plans/checked-engineering-v1/tasks/B02-capability.md. Prerequisites: B01. Start by checking those prerequisites, then carry this task through its stated accepted result.
```
