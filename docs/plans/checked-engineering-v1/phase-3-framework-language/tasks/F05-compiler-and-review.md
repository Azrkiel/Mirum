# F05 — Compile language programs into reviewed executable engineering plans

Prerequisites: F03, F04. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Compiler/type checker, review bridge, machine-readable execution plan and reference beam integration.

## Specific implementation steps
1. Resolve symbols, capability/backend references and dimensions; lower the AST to canonical ProgramIR and existing reviewed briefs.
2. Produce construction instructions and immutable requirement/check plans from reviewed specification meaning; never derive acceptance thresholds from generated geometry.
3. Implement check/plan/review/run stages: compilation creates a draft, actual execution requires the exact reviewed semantic digest and backend lock.
4. Pin defaults explicitly in the plan, enforce capability restrictions and expose conversions; choose only registered solver models and candidate spaces.

## Accepted end result
- [ ] Reference beam language and form routes match normalized requirements, candidate choices and bounded solver outcomes.
- [ ] Changing load/material/allowed candidates/backend policy invalidates the appropriate review; comments alone need not change semantic approval.
- [ ] No missing requirement, unsupported feature or unit error can start native execution.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P03, P06, P07 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F05.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F06, F07, F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F05-compiler-and-review.md. Execute F05: Compile language programs into reviewed executable engineering plans. Check actual prerequisite status and handoffs (F03, F04) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F05.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
