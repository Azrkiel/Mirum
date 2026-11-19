# F07 — Bind semantic features and independent checks to delivered geometry

Prerequisites: F05, F06. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Semantic feature/evidence bindings, strengthened validators and independent tampering fixtures.

## Specific implementation steps
1. Add requirement IDs and measurement bindings for supported semantic features such as beam root section/load endpoint; detect ambiguous mapping after rebuild.
2. Reopen the delivered representation and measure it against the immutable check plan; label each check's capability, method, coverage and unavailable/unperformed scope.
3. Adapt AgentCAD's tampering-test pattern to STEP/STL/metadata: alter dimensions, topology, labels, candidate report and preview independently.
4. Make evidence report numerical/representation error bounds and provenance; hashes or names alone cannot establish geometric/physical correctness.

## Accepted end result
- [ ] Wrong-but-valid geometry and stale/mismatched evidence fail the required checks.
- [ ] 2D tags are not treated as a solution to 3D topological naming; ambiguous feature identity fails closed.
- [ ] PicoGK geometry-only and glider screening results never acquire beam or manufacturing acceptance labels.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P05, P07, P10 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F07.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F08, F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F07-independent-verification.md. Execute F07: Bind semantic features and independent checks to delivered geometry. Check actual prerequisite status and handoffs (F05, F06) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F07.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
