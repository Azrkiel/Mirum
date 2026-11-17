# F03 — Implement and qualify the kernel adapter framework

Prerequisites: F02, E01, E02. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Two qualified backend adapters, capability/backend compatibility matrix and conformance fixtures.

## Specific implementation steps
1. Implement the adapter protocol and runtime probe/negotiation described in ARCHITECTURE.md.
2. Adapt existing OCCT/CadQuery and PicoGK builders without duplicating their native mathematics; register glider as an engineering capability rather than a geometry kernel.
3. Bind backend build/runtime, representation, supported operations/export formats, tolerances and evidence scope in the execution lock.
4. Add real conformance fixtures for supported common geometry and explicit unsupported cases; preserve native worker limits/cancellation and independent artifact reread.

## Accepted end result
- [ ] Both OCCT and PicoGK pass native conformance in their declared envelopes; mocks cannot satisfy this gate.
- [ ] No silent fallback, fake STEP export, representation swap or inherited physics claim.
- [ ] Unavailable backend and conversion precision failures are reported distinctly.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P04, P05 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F03.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F05, F10. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F03-kernel-adapters.md. Execute F03: Implement and qualify the kernel adapter framework. Check actual prerequisite status and handoffs (F02, E01, E02) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F03.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
