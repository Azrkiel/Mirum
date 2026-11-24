# F09 live Gemini qualification plan

Status: executed 2026-11-24. Run [live-20261124-a](https://github.com/Azrkiel/Mirum/actions/runs/45444047733) on `b08fd9e` passed S1-S4 and stopped at S5 on a clarification question; it spent 2 calls. After PR #145 fixed the prompts, run [live-20261124-b](https://github.com/Azrkiel/Mirum/actions/runs/45450348779) on `b65136b` passed all six steps with 3 calls. That is 5 of the approved 6 calls; the `f09-live-` reservation cap of 2 is spent. The plan below is kept as written.

## Why a new harness

No existing live harness reaches the F09 route. Every existing live budget is also spent. See handoff rows L1 and L2 in [handoffs/F09.md](handoffs/F09.md).

| Harness | Typed beam AI draft | MEL compile | Guarded stage | Program review and run | Remaining reservations |
|---|---|---|---|---|---|
| `b13-live.yml` | partial: calls `BriefProposer` in process, not `POST /drafts` | no | no | no | diagnostic 0 of 6, qualification 0 of 5 |
| `b13-step12.yml` | yes: one `POST /drafts` mode `ai` through the checked review UI | no | no | checked-brief review and run only, not a program revision | 0 of 1 |
| `e05-live.yml` | no (generator capabilities only) | no | no | no | round 2 spent for all three capabilities |
| `b13-step12-rehearsal.yml` | no (form mode, AI off) | no | no | checked-brief review and run only | none (provider-free) |
| `release-e2e.yml` | no: `/drafts` mode `ai` is expected to return 503 | no | no | typed beam run only | none; only a `main` dispatch is release evidence |

The F09 route is: typed `POST /drafts` (mode `ai`, beam) -> `frontend/program_workspace.py::_source_from_beam` -> `POST /engineering/v1/mel/plan` -> stage with `X-Mirum-Require-Requirements` and source ID `typed-ai-proposal.mir` -> explicit review -> run -> verified publication.

## Harness

`scripts/f09_live_proposal.py` drives that route in process against a disposable store. `--mode rehearsal` injects the existing typed proposer fixture at the provider client and is exercised by `backend/tests/test_f09_live_proposal_rehearsal.py` in native CI. `--mode live` uses the same code with the real provider client. Steps:

1. Form route reference beam (200 x 10 mm, 50 N): semantic hash, `brief_sha256`, candidate semantics, provenance `form`.
2. Committed `beam.mir` compiled through `/mel/plan`: same identities, provenance `language`.
3. Accepted head: stage, review and run `beam.mir`; 5 and 10 mm rejected, 15 mm published with verified evidence.
4. Equivalent AI proposal: the same identities as steps 1 and 2, with AI provenance (prompt SHA-256, distinct source hash).
5. Fixed-requirement change (length 201 mm): refused by the guard before a revision row exists; history unchanged. A provider reply that does not change the length is `not_exercised` and fails the run.
6. Allowed candidate (20 mm): staged under the guard, run refused before review, then reviewed, run, verified and published with `typed-ai-proposal.mir`. The head moves only then.

The source label `typed-ai-proposal.mir` is supplied by the client and proves nothing by itself. The evidence therefore links each AI-derived revision to its typed draft: input mode `ai`, `ai_proposed` field origins, prompt SHA-256 and the ledger call count of that proposal.

Scope: this harness closes the live part of P03 and the server-side live part of P13 (live proposal, guard refusal, guarded staging, explicit review, run, verified publication). It does not drive the browser. The browser behavior of the same route is covered by the injected-fixture Chromium run in the F09 handoff. A live browser pass would need its own approval and budget, modeled on `b13-step12.yml`, if the owner wants it before release.

Three proposals, each at most the initial call plus the proposer's single schema repair: at most 6 provider calls, counted from the backend proposal ledger and enforced by the harness and the daily call cap.

## Live workflow

`.github/workflows/f09-live.yml` mirrors `b13-live.yml`: `workflow_dispatch` only, `main` only, environment `b13-live` (required reviewer, `main` branch policy, secret `B13_GEMINI_API_KEY`), the same hardened container, and safe JSON evidence only. Order: validate inputs, build the verification image at the dispatched SHA, run the rehearsal mode in that image, then reserve `f09-live-<run_id>` (cumulative cap 1), then the live mode. A failed rehearsal spends no reservation and no call. The job fails unless the evidence verdict is `passed`.

## Pass criteria

The evidence artifact `f09-<run_id>` records the run ID, authorization ID, dispatched SHA and image ID, at most 6 provider calls, and a pass for steps 1-6. Raw replies, prompts' replies, keys and the ledger are never uploaded.

## Sequence and approvals needed

1. Owner: approve merging PR #144 (with the harness and workflow) to `main` after the independent security review required for code merges. This is the eligible revision.
2. Owner: issue an authorization ID for one `f09-live` run with a cap of 6 provider calls on the `B13_GEMINI_API_KEY` key, and approve the `b13-live` deployment when the job asks.
3. Lead: dispatch `f09-live.yml` on `main` with a fresh run ID, then record the run and artifact in F09 L5, P03 and P13.
4. Owner: authorize the clean `release-e2e.yml` dispatch on `main` (`clean=true`). Lead records it in F10 R2 and P14.

Merge, deployment and public enablement stay separate decisions. K51 still blocks public enablement of the program API. Physical acceptance stays in its own record.
