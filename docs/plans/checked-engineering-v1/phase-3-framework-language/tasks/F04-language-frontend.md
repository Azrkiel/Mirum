# F04 — Implement Mirum Engineering Language v0.1

Prerequisites: F02. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Language frontend, grammar tests, formatter and editor-ready diagnostic records.

## Specific implementation steps
1. Implement a tokenizer, parser, AST, source spans and formatter for the frozen grammar in LANGUAGE.md; choose a maintained parser library or small parser and record that choice.
2. Support the version declaration, one named design, named assignments, unit-bearing literals, strings and bounded lists; add comments and duplicate-key/name diagnostics.
3. Reject unsupported syntax and bound bytes/tokens/nodes/nesting before semantic analysis; never evaluate source with Python eval/exec.
4. Add examples and parser/formatter round trips, including malformed and hostile input.

## Accepted end result
- [ ] The example .mir program parses and formats without changing meaning; illegal syntax reports file/line/column.
- [ ] No imports, filesystem/network access, arbitrary Python, loops or user-defined functions exist in v0.1.
- [ ] Parser runs without native CAD or model-provider credentials.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P02, P06 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F04.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F05, F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F04-language-frontend.md. Execute F04: Implement Mirum Engineering Language v0.1. Check actual prerequisite status and handoffs (F02) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F04.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
