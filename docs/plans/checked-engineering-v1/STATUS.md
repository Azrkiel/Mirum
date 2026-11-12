# Task status

B01 native baseline is complete; later application tasks remain pending. Check a task only after its task-specific evidence is recorded in a handoff and its implementation is present in this branch.

| Complete | ID | Task | State | Prerequisites | Evidence / handoff |
|---|---|---|---|---|---|
| [x] | B01 | [Establish a reproducible baseline](tasks/B01-baseline.md) | complete | none | [B01 handoff](handoffs/B01.md), [native evidence](evidence/B01.json) |
| [ ] | B02 | [Define the bounded beam capability](tasks/B02-capability.md) | not_started | B01 | Not produced |
| [ ] | B03 | [Create reviewed briefs and evidence schemas](tasks/B03-contracts.md) | not_started | B02 | Not produced |
| [ ] | B04 | [Adapt the existing deterministic beam workflow](tasks/B04-cantilever-adapter.md) | not_started | B03 | Not produced |
| [ ] | B05 | [Prepare and finalize briefs without AI](tasks/B05-brief-preparation.md) | not_started | B03, B04 | Not produced |
| [ ] | B07 | [Evaluate the reviewed candidate choices](tasks/B07-candidate-search.md) | not_started | B04, B05 | Not produced |
| [ ] | B08 | [Make acceptance depend on complete evidence](tasks/B08-acceptance-policy.md) | not_started | B03, B04, B07 | Not produced |
| [ ] | B09 | [Persist and control engineering jobs](tasks/B09-job-lifecycle.md) | not_started | B07, B08 | Not produced |
| [ ] | B10 | [Publish complete revision-bound packages](tasks/B10-result-packages.md) | not_started | B08, B09 | Not produced |
| [ ] | B06 | [Propose structured briefs with AI](tasks/B06-ai-preparation.md) | not_started | B05 | Not produced |
| [ ] | B11 | [Expose the common workflow through APIs](tasks/B11-versioned-api.md) | not_started | B05, B06, B09, B10 | Not produced |
| [ ] | B12 | [Build the checked beam interface](tasks/B12-user-workflow.md) | not_started | B11 | Not produced |
| [ ] | B13 | [Qualify the end-to-end behavior](tasks/B13-release-verification.md) | not_started | B06, B07, B08, B09, B10, B11, B12 | Not produced |
| [ ] | B14 | [Prepare the reproducible first release](tasks/B14-release-handoff.md) | not_started | B13 | Not produced |
| [ ] | E01 | [Integrate PicoGK with honest geometry scope](tasks/E01-picogk-adapter.md) | not_started | B14 | Not produced |
| [ ] | E02 | [Integrate the glider CEM without upgrading its claims](tasks/E02-glider-adapter.md) | not_started | B14 | Not produced |
| [ ] | E03 | [Define and qualify a second structural family](tasks/E03-second-structural-family.md) | not_started | B14 | Not produced |
| [ ] | E04 | [Establish physical and manufacturing evidence](tasks/E04-physical-validation.md) | not_started | B14 | Not produced |
| [ ] | E05 | [Expand AI across registered capabilities](tasks/E05-broader-design-generation.md) | not_started | E01, E02 | Not produced |

The expansion tasks E01–E05 are separate releases. E03 and E04 have explicit domain/hardware prerequisites in their cards. All V01–V15 release checks are initially not_run; track actual evidence in ACCEPTANCE.md and the B13/B14 handoffs.
