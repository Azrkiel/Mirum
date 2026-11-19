# F08 — Expose one runtime through Python CLI HTTP and agent tools

Prerequisites: F06, F07. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Runtime SDK/CLI/API/MCP bindings with shared contract tests.

## Specific implementation steps
1. Implement thin bindings over one service layer for validate/format/plan/finalize/run/status/cancel/inspect/diff/restore/export.
2. Add a scoped MCP interface after CLI/Python/HTTP equivalence exists; tool schemas derive from the same registry and cannot bypass review.
3. Standardize structured errors including source location, requirement/operation IDs, retryability, partial-change state and suggested next action.
4. Use existing access controls, idempotency, quotas and safe file/URI handling; keep legacy endpoints compatible.

## Accepted end result
- [ ] Equivalent requests through all four interfaces reach identical policy and review checks.
- [ ] No transport permits arbitrary code, private path access or implicit rerun on polling/export.
- [ ] Unavailable dependencies and unsupported syntax/geometry differ from numerical design rejection.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P11, P12 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F08.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F09, F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F08-interfaces.md. Execute F08: Expose one runtime through Python CLI HTTP and agent tools. Check actual prerequisite status and handoffs (F06, F07) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F08.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
