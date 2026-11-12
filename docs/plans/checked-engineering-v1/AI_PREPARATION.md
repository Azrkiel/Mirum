# Structured AI preparation (engineering_extraction_v1)

`engineering.proposer.BriefProposer(service, ledger=..., provider=None)` consumes
the B05 service and a shared `ProposalLedger`. Its async
`propose(draft_id, expected_revision=..., prompt=..., cancelled=None)` returns
`ProposalResult(state, prepared, reasons)`; it never finalizes a brief or starts
native generation. Create an initial draft with `service.create({})`, propose
into that revision, display the resulting draft/export, and collect the same
explicit B03 acknowledgement as the typed path. A successful proposal creates a
new unreviewed revision. Input fields and all material/support/load assumptions
remain visible. Equivalent reviewed form/AI meaning retains identical semantic
digests; provenance differs.

The versioned extraction prompt is `EXTRACTION_PROMPT` / `PROMPT_VERSION` in
`backend/engineering/proposer.py`. `ProposalResponse.model_json_schema()` exports
the strict `engineering_proposal_v1` response schema without importing a
provider. `provider_schema()` projects it to the installed Gemini proto's
supported schema subset. Bounds, unknown-field rejection, exact versions and
physical checks remain enforced locally through B03/B05. JSON duplicate keys,
nonfinite values, executable-source fields and arbitrary extra fields fail.
Partial fields, unresolved questions, contradictions and unsupported features
are explicit data. An otherwise complete proposal with unresolved issues cannot
finalize. No silent repair changes physical requirements or fills missing facts.

All extracted fields have `ai_proposed` origins with the original request's
SHA-256, extraction version and model reference. These are unverified proposals,
not user-supplied/certified material facts. Only fixed schema/capability/check
metadata has defaults; no physical input is invented by the service. The user
must acknowledge the exact material source, assumptions and normalized choices.
Provider prompts are not executable Python and are not sent to a compiler.

## Provider and operational bounds

The default `GeminiProposalProvider` uses the existing `clarifier` ModelRegistry
configuration (falling back only to the configured Lite model when no model ID
is set), the existing `GEMINI_API_KEY_HEAVY`, and installed
`google-ai-generativelanguage`. It snapshots model/temperature/token limits per
revision; output tokens are capped at 4096. Each explicit async client closes its
transport in `finally`. SDK retries are disabled (`retry=None`), timeout is 45 s,
and no model fallback or general CAD agent is invoked. It does not alter legacy
global SDK configuration or read `.env` itself. Credentials stay in the provider
client; no subprocess receives them.

One initial call plus at most one schema repair is allowed per draft revision.
Repair uses a fixed instruction and original request, never raw prior output.
Combined instruction/request text plus repair reserve must fit 8000 characters
before the first call; the fixed response schema is separate. Input is rejected,
not clipped. Each response is capped at 16 KiB UTF-8. Oversize responses,
transport/model failures and incomplete SDK candidates stop visibly; only schema
malformation gets a repair. Each call is bounded to at most 45 s and uses B07's
owned-coroutine cancellation/cleanup control. `call_seconds` may lower this bound
for tests but cannot increase it. Outer task cancellation propagates after
cleanup. Typed form preparation remains available after every AI failure.

`ProposalAudit` records provider/SDK/model/configuration and prompt/schema
versions, request digest/size, response size, token usage, reserved call count and
status. It contains no raw prompt/response or key. Failures return fixed safe
diagnostics, never provider exceptions. B09 must persist the ledger with unique
`(draft_id, revision)` claims and cannot reset consumed budgets on retry/restart.
Use one shared ledger instance across request handlers; do not instantiate a new
ledger per HTTP request. The supplied locked memory implementation is local only.

## Revision reservation and B05 integration

B05 now accepts trusted `extra_issues` in create/edit so extractor questions and
contradictions remain review blockers even when all scalar fields parse. It
exposes `input_provenance`, `is_finalized`, `begin_proposal` and `end_proposal`.
The repository's begin/end operations reserve an exact current, unfinalized
revision with an owner token under the same transaction/lock as finalization.
Finalization conflicts while that revision has an active proposal. Calls check
the current revision before initial/repair requests; output is applied only by
the existing CAS edit. Settings-time finalization wins without a provider call;
form edits cannot be overwritten; a newer finalized form revision prevents an
old revision's repair. Reservations release after provider cleanup on every exit.

B09's durable repository must implement these added operations atomically with
finalization, preserve owner checks, and recover abandoned reservations without
resetting the durable call budget. Editing into a new revision remains allowed;
an already finalized brief is immutable. There is no storage migration yet.
B11 owns transport/rate limits and B12 owns actual browser review. B04/B07/B08
execution has no dependency on the proposer or its provider.

## Evidence boundary and fixed corpus

`backend/tests/fixtures/engineering/ai-corpus-v1.json` contains 30 fixed synthetic
inputs: 10 complete, 10 ambiguous/incomplete and 10 unsupported. Complete cases
include expected normalized content. `fixture_response` entries are explicitly
oracle/provider-seam fixtures; they exercise common validation and review gates,
not extraction accuracy. SDK request/schema construction is tested through an
offline transport fixture with retries disabled. Real native execution of a
finalized fixture-produced AI brief proves the provider-free execution boundary.

No live-provider qualification is claimed. Verification uses the B01 image with
network disabled and no provider credentials passed. B13/V15 must make actual
bounded live requests against this unchanged corpus, record model/provider/usage,
require zero silent requirement drops or unreviewed execution, and obtain at
least 8/10 accurate reviewable complete drafts. A fixture pass is not V15. The
installed Google SDK warns that B01's Python 3.10 runtime is no longer supported;
this task preserves the required baseline and leaves live compatibility/release
qualification open. No dependency/model/deployment change is made here.
