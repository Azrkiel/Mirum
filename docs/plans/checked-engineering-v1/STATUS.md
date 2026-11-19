# Task status

**Landing on main (U4).** The typed/form path is available. AI draft mode (`POST /engineering/v1/drafts` with `mode: ai`) is OFF by default and is not live-qualified. Enable it with `MIRUM_ENGINEERING_AI_DRAFTS=1` (or `true`); `GET /engineering/v1/capabilities` then reports `ai_enabled: true` and the Streamlit "Checked beam" page offers the AI entry mode. With the flag off the AI route returns 503 "AI draft preparation is not enabled" and no provider is built or key read. When a model reply fails local validation, the error locations/types and the raw reply (capped at 16 KiB) are kept in the proposer audit ledger only, never logged or returned.

**2026-11-18: B13 live corpus qualification PASSED** (run `qual-20261118-k6`, `f11cf90`: 10/10 complete accurate, 0 silent drops, 0 unreviewed executions, 30 calls; see [qualification](QUALIFICATION.md)). B13 steps 12-13 (live proposal reviewed in the real UI; V01-V15 requalification) remain, so B13 and B14 stay open. Earlier history: B01 through B12 are complete within task scope. The [supplied-key diagnostic](handoffs/B13-provided-key-diagnostic.md) passed metadata access and reached generation/local parsing in 7.36 seconds on the 4096-token candidate, but the reply failed strict JSON/schema validation; no repair was authorized in that one-call test. The candidate passes 85 offline checks but is not live-qualified. Antivirus cleanup is verified complete. B13/B14 remain blocked; earlier ledgers are preserved.

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
| [x] | B10 | [Publish complete revision-bound packages](tasks/B10-result-packages.md) | complete | B08, B09 | [B10 handoff](handoffs/B10.md), [evidence](evidence/B10.json) |
| [x] | B06 | [Propose structured briefs with AI](tasks/B06-ai-preparation.md) | complete | B05 | [B06 handoff](handoffs/B06.md), [evidence](evidence/B06.json) |
| [x] | B11 | [Expose the common workflow through APIs](tasks/B11-versioned-api.md) | complete | B05, B06, B09, B10 | [B11 handoff](handoffs/B11.md), [evidence](evidence/B11.json) |
| [x] | B12 | [Build the checked beam interface](tasks/B12-user-workflow.md) | complete | B11 | [B12 handoff](handoffs/B12.md), [evidence](evidence/B12.json) |
| [ ] | B13 | [Qualify the end-to-end behavior](tasks/B13-release-verification.md) | blocked | B06, B07, B08, B09, B10, B11, B12 | [B13 handoff](handoffs/B13.md), [qualification](QUALIFICATION.md), [evidence](evidence/B13.json); 443 regressions pass, live gate blocked |
| [ ] | B14 | [Prepare the reproducible first release](tasks/B14-release-handoff.md) | blocked | B13 | [B14 gate record](handoffs/B14.md); not started, prerequisite blocked |
| [ ] | E01 | [Integrate PicoGK with honest geometry scope](tasks/E01-picogk-adapter.md) | not_started | B14 | Not produced |
| [ ] | E02 | [Integrate the glider CEM without upgrading its claims](tasks/E02-glider-adapter.md) | not_started | B14 | Not produced |
| [ ] | E03 | [Define and qualify a second structural family](tasks/E03-second-structural-family.md) | not_started | B14 | Not produced |
| [ ] | E04 | [Establish physical and manufacturing evidence](tasks/E04-physical-validation.md) | not_started | B14 | Not produced |
| [ ] | E05 | [Expand AI across registered capabilities](tasks/E05-broader-design-generation.md) | not_started | E01, E02 | Not produced |

The expansion tasks E01–E05 are separate releases. E03 and E04 have explicit domain/hardware prerequisites in their cards. Scoped baseline/library evidence is tracked in ACCEPTANCE.md; full V01–V15 release checks remain open until their owning tasks.

## Separate follow-on phase

[Phase 3 task status](phase-3-framework-language/STATUS.md) tracks F01–F11 separately. No existing B/E completion state is changed by this planning addition. F01 execution requires B14 and E05; F03 also requires E01/E02. F11 is optional research after F10.

## R2c: regen run_bytes (2026-11-19)

Owner decision Q1 (a), STOP-L approved 2026-11-19: regen has its own `run_bytes` of 256 MiB (`REGEN.resources`, `capabilities.py`); beam, dragster and glider stay at 32 MiB and `storage_bytes` stays at 512 MiB. Publication (`bundles.py`) now enforces the publishing brief's own `run_bytes` for the pre-ZIP check, the stored-size check and the free-disk check, and its errors name the actual budget. Admission (`jobs.py`) reserves the brief's own `run_bytes`, so a regen job is refused unless storage can hold another 256 MiB. Resource values are not hashed (only `resource_policy_version` is bound into briefs), so no pinned hash or receipt changes. `scripts/regen_release_smoke.py` now gates on an accepted checked run whose artifact hashes equal the browser route's, derives `blocked_by` from the real failure, and records `stored_bytes_measured`.
