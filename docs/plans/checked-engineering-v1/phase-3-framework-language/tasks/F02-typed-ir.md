# F02 — Define Mirum's typed engineering program and operation registry

Prerequisites: F01. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Typed IR, operation registry, JSON schemas and migration/compatibility fixtures.

## Specific implementation steps
1. Introduce versioned ProgramIR and typed operation descriptors with required fields, units, preconditions, outputs and supported capability versions.
2. Separate requirements, candidate choices, engineering analysis plans, geometry build plans and evidence expectations; attach source spans and stable semantic IDs.
3. Bridge the new records to the existing strict beam contracts; do not turn legacy Literal-restricted contracts into permissive universal models.
4. Define canonical semantics hashes, source hashes, execution lock and artifact hashes independently; reject unknown fields and unresolved references.

## Accepted end result
- [ ] Equivalent normalized form/AI/language inputs agree on requirement meaning while retaining distinct provenance.
- [ ] Unsupported operations and unit mismatches fail before kernel startup.
- [ ] Existing beam API/spec/hash behavior remains compatible.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P02, P03 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F02.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F03, F04. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F02-typed-ir.md. Execute F02: Define Mirum's typed engineering program and operation registry. Check actual prerequisite status and handoffs (F01) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F02.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
