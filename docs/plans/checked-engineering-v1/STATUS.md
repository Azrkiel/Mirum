# Task status

B01â€“B08 are complete within their task scopes; B09 is next eligible. Durable jobs, publication, versioned API/UI and release qualification remain pending. Check a task only after its task-specific evidence is recorded in a handoff and its implementation is present in this branch.

| Complete | ID | Task | State | Prerequisites | Evidence / handoff |
|---|---|---|---|---|---|
| [x] | B01 | [Establish a reproducible baseline](tasks/B01-baseline.md) | complete | none | [B01 handoff](handoffs/B01.md), [native evidence](evidence/B01.json) |
| [x] | B02 | [Define the bounded beam capability](tasks/B02-capability.md) | complete | B01 | [B02 handoff](handoffs/B02.md) |
| [x] | B03 | [Create reviewed briefs and evidence schemas](tasks/B03-contracts.md) | complete | B02 | [B03 handoff](handoffs/B03.md), [schema/native regression evidence](evidence/B03.json) |
| [x] | B04 | [Adapt the existing deterministic beam workflow](tasks/B04-cantilever-adapter.md) | complete | B03 | [B04 handoff](handoffs/B04.md), [evidence](evidence/B04.json) |
| [x] | B05 | [Prepare and finalize briefs without AI](tasks/B05-brief-preparation.md) | complete | B03, B04 | [B05 handoff](handoffs/B05.md), [evidence](evidence/B05.json) |
| [x] | B07 | [Evaluate the reviewed candidate choices](tasks/B07-candidate-search.md) | complete | B04, B05 | [B07 handoff](handoffs/B07.md), [evidence](evidence/B07.json) |
| [x] | B08 | [Make acceptance depend on complete evidence](tasks/B08-acceptance-policy.md) | complete | B03, B04, B07 | [B08 handoff](handoffs/B08.md), [evidence](evidence/B08.json) |
| [x] | B09 | [Persist and control engineering jobs](tasks/B09-job-lifecycle.md) | complete | B07, B08 | [B09 handoff](handoffs/B09.md), [evidence](evidence/B09.json) |
| [ ] | B10 | [Publish complete revision-bound packages](tasks/B10-result-packages.md) | not_started | B08, B09 | Not produced |
| [x] | B06 | [Propose structured briefs with AI](tasks/B06-ai-preparation.md) | complete | B05 | [B06 handoff](handoffs/B06.md), [evidence](evidence/B06.json) |
| [ ] | B11 | [Expose the common workflow through APIs](tasks/B11-versioned-api.md) | not_started | B05, B06, B09, B10 | Not produced |
| [ ] | B12 | [Build the checked beam interface](tasks/B12-user-workflow.md) | not_started | B11 | Not produced |
| [ ] | B13 | [Qualify the end-to-end behavior](tasks/B13-release-verification.md) | not_started | B06, B07, B08, B09, B10, B11, B12 | Not produced |
| [ ] | B14 | [Prepare the reproducible first release](tasks/B14-release-handoff.md) | not_started | B13 | Not produced |
| [ ] | E01 | [Integrate PicoGK with honest geometry scope](tasks/E01-picogk-adapter.md) | not_started | B14 | Not produced |
| [ ] | E02 | [Integrate the glider CEM without upgrading its claims](tasks/E02-glider-adapter.md) | not_started | B14 | Not produced |
| [ ] | E03 | [Define and qualify a second structural family](tasks/E03-second-structural-family.md) | not_started | B14 | Not produced |
| [ ] | E04 | [Establish physical and manufacturing evidence](tasks/E04-physical-validation.md) | not_started | B14 | Not produced |
| [ ] | E05 | [Expand AI across registered capabilities](tasks/E05-broader-design-generation.md) | not_started | E01, E02 | Not produced |

The expansion tasks E01â€“E05 are separate releases. E03 and E04 have explicit domain/hardware prerequisites in their cards. Scoped baseline/library evidence is tracked in ACCEPTANCE.md; full V01â€“V15 release checks remain open until their owning tasks.
