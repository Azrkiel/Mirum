# F09 — Add language editing, AI proposals and visible revision comparison

Prerequisites: F08. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Editor/AI/revision UX and browser evidence using actual native output.

## Specific implementation steps
1. Add a language editor, structured brief view, source diagnostics, plan preview and explicit review state to the existing UI.
2. Make AI propose .mir source or typed edits validated by the exact same parser/compiler; never let model text declare acceptance.
3. Show model/report/version comparisons, changed requirements and measured deltas; render only associated saved artifacts.
4. Test refresh, two-tab edits, cancel, stale previews, provider outage, rollback and expired packages; allow language/form workflows offline.

## Accepted end result
- [ ] A user can author or request a program, inspect its meaning, review, run, compare and export without hidden steps.
- [ ] Meaning-changing edits invalidate review and AI/form/language paths share evidence policy.
- [ ] Visual similarity is never used as proof of physical acceptance.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P03, P08, P11, P13 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F09.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F09-editor-and-ai.md. Execute F09: Add language editing, AI proposals and visible revision comparison. Check actual prerequisite status and handoffs (F08) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F09.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
