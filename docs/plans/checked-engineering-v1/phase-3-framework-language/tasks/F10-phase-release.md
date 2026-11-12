# F10 — Qualify and release the language and multi-kernel framework

Prerequisites: F03, F04, F05, F06, F07, F08, F09. Track state in [STATUS.md](../STATUS.md).
This is implementation work for the follow-on phase, not a completed capability.

## Goal and deliverable
- Phase 3 release candidate, compatibility/replay fixtures and integration handoff; no automatic merge/deploy.

## Specific implementation steps
1. Run P01–P14 and applicable existing V1/E gates in clean pinned environments; record real adapter, compiler, API/MCP and browser results.
2. Reproduce the beam example end to end and a real PicoGK geometry-only example through the language/runtime; run shared geometry conformance only where both support the operation.
3. Publish language/IR/adapter compatibility policy, version migration examples, dependency/license notices and replay/migration/rollback instructions.
4. Archive source, canonical IR, reviewed brief, operation/check plan, execution lock, attempts, evidence and exact artifacts under a bounded delivery contract.

## Accepted end result
- [ ] Every Phase 3 gate has actual evidence or the release is explicitly blocked.
- [ ] Supported backends and language features match the registry and UI; neither 'any kernel' nor physical qualification is claimed without testing.
- [ ] Old clients and saved V1 packages remain usable; reviewed locks are never silently migrated.

## Verification and integration
Exercise the task behavior with independent positive/negative fixtures. Applicable release checks: P01, P02, P03, P04, P05, P06, P07, P08, P09, P10, P11, P12, P13, P14 in [ACCEPTANCE.md](../ACCEPTANCE.md). Native adapters require real native evidence; mock paths are labelled explicitly. Use the B01/B14 reproduced runtime and applicable security review instructions.

Read [FRAMEWORK.md](../FRAMEWORK.md), [LANGUAGE.md](../LANGUAGE.md) and [INTEGRATION.md](../INTEGRATION.md). Keep existing versions/clients working. Record interface/format changes and exact tested commit in handoffs/F10.md using [TEMPLATE.md](../handoffs/TEMPLATE.md).

Next direct consumers: F11. They start only after all their own prerequisites are satisfied. For F11, a go/no-go decision is the outcome; an entire new CAD kernel is not implied.

## Starter prompt
```text
Work in Azrkiel/Mirum on branch engineering/checked-design-v1. Read repository instructions and docs/plans/checked-engineering-v1/phase-3-framework-language/README.md, FRAMEWORK.md, LANGUAGE.md and INTEGRATION.md (all in that phase directory), then docs/plans/checked-engineering-v1/phase-3-framework-language/tasks/F10-phase-release.md. Execute F10: Qualify and release the language and multi-kernel framework. Check actual prerequisite status and handoffs (F03, F04, F05, F06, F07, F08, F09) before dependent work. Preserve completed B/E implementations; implement only this task. Run meaningful native/compiler/API/browser checks appropriate to the change, document actual evidence and unresolved prerequisites, update this phase's STATUS.md and write handoffs/F10.md. Commit and push verified task changes to this branch without force-pushing; do not merge main or deploy. If prerequisites or mandatory evidence are missing, complete independent preparation and record a blocked handoff rather than claiming completion. Report the commit, accepted result and next eligible task.
```
