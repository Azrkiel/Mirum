# F11 — Decide whether a native Mirum geometry kernel is justified

Prerequisites: F10. Track state in [STATUS.md](../STATUS.md).
Optional research; not required for F01–F10 release.

## Goal and deliverable
- Own-kernel feasibility report and, when justified, a separate bounded prototype roadmap.

## Specific implementation steps
1. Measure an explicit limitation of existing backends: supported operation, robustness, precision, throughput or deployment constraint; document commercial/open-source alternatives.
2. Propose a bounded kernel scope and representation, tolerance/error model, topology algorithms, persistence/export format, licensing and staffing/benchmark plan.
3. Build a limited experimental prototype only if justified and authorized by the task session; compare against trusted analytical/reference cases and adapter conformance.
4. Write a go/no-go decision and separately scoped implementation roadmap; no experimental backend enters production or inherits existing acceptance.

## Accepted end result
- [ ] The decision distinguishes Mirum's engineering runtime from a new solid-modeling kernel.
- [ ] A go decision has measurable targets and a bounded prototype/evidence plan; a no-go is a valid completed research outcome.
- [ ] No claim that this research task delivers a general-purpose production geometry kernel.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P04, P05 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F11.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: phase release/domain decision review. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F11-own-kernel-research.md. Execute F11: Decide whether a native Mirum geometry kernel is justified. Check actual prerequisite status and handoffs (F10) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F11.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
