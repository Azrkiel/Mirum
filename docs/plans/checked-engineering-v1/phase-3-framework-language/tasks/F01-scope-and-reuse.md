# F01 — Freeze the framework scope and AgentCAD reuse decisions

Prerequisites: B14, E05. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Framework scope/reuse ADR and baseline interface inventory.

## Specific implementation steps
1. Inventory the implemented B/E interfaces and evidence at the current branch head; resolve divergence before designing replacements.
2. Record AgentCAD patterns to adopt, independently implement or defer; pin upstream files/revision and resolve full licensing/attribution before any code transplant.
3. Define Phase 3 capability/backend support matrix, source/IR/runtime versions and release claims; leave physical qualification with E03/E04.

## Accepted end result
- [ ] Approved-by-review technical decision record identifies existing interfaces, new modules, exclusions and acceptance owners.
- [ ] No wholesale upstream import or claimed upstream benchmark success; source reuse records are complete.
- [ ] F01 produces concrete contracts before dependent code starts.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P01 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F01.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F02. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F01-scope-and-reuse.md. Execute F01: Freeze the framework scope and AgentCAD reuse decisions. Check actual prerequisite status and handoffs (B14, E05) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F01.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
