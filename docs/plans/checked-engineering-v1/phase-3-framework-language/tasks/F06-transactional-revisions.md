# F06 — Add reversible program revisions and transactional execution

Prerequisites: F05. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Program revision journal, transaction/recovery integration and history APIs.

## Specific implementation steps
1. Reuse durable jobs/bundles from B09/B10; add parent-linked program revisions, change journals, checkpoint selection and conflict detection.
2. Execute operation batches against private staging; expose only fully verified/published revisions as current accepted output.
3. Implement compare and rollback-as-new-revision semantics: retained artifacts may be restored with their original evidence/lock; changed requirements or missing artifacts require a fresh execution.
4. Reconcile crashes between native output, metadata, publication and pointer updates; protect cancellation races and isolate concurrent edits.

## Accepted end result
- [ ] Failed batches cannot replace accepted output or leave the UI on a mixed revision.
- [ ] Crash/restart/cancel recovery and stale-parent conflicts have tested outcomes.
- [ ] Rollback preserves history; expired artifacts are explicitly unavailable or rebuilt under a new run, never reconstructed as old evidence.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P08, P09 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F06.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F07, F08, F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F06-transactional-revisions.md. Execute F06: Add reversible program revisions and transactional execution. Check actual prerequisite status and handoffs (F05) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F06.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
