# B13 Gemini 3.6 Flash diagnostic

State: blocked. Date: November 12, 2026.
Branch: `engineering/checked-design-v1`.
Tested implementation: `0f1864adb06e548222265e9d8fd6377c47aad509`.

## Requested change

The user requested switching to Gemini 3.6 Flash and trying it. Checked beam
preparation now selects `gemini-3.6-flash` through the dedicated
`GEMINI_MODEL_ENGINEERING_PREPARATION` configuration constant. Legacy CAD model
defaults are preserved. Temperature 0.4, token cap 1024, SDK, gRPC transport,
disabled SDK retries, OS trust, prompt/schema, native bounds and call deadline
are unchanged. This isolates the requested model comparison.

The model-selection and SDK-request tests failed on 3.5, then passed on 3.6.
The proposer/qualification/store selection passed **84 tests, one existing
Python 3.10 support warning, in 36.92 seconds**, with networking disabled.

## One live attempt and result

The request authorized a conservative one-call diagnostic, recorded separately
as `user-20261112-g36-one-call`. A dedicated application-owned volume was created
after authorization. Neither stopped evaluation ledger was reused or reset.

The diagnostic sent only the first original corpus prompt, C01, using the common
provider, schema and preparation validation. It blocked native execution and
disabled schema repair for this one-call budget. Offline fixture checks verified
valid/malformed/transport-failure paths and no repeat on restart before going live.
Credentials were mounted read-only; no key, raw prompt or provider reply was logged.

**The attempt hit the outer 45-second preparation deadline**: unavailable after
45.037221 seconds, provider coroutine cancelled, no usable draft, one consumed
initial attempt and zero repairs. No completed provider response or low-level
provider error classification was available before cancellation. This is not a
full corpus evaluation and provides no live accuracy qualification.

The model switch did not unblock this request. It does not establish that the
credential, service, transport or runtime is the root cause. No billing or token
usage conclusion is drawn from missing usage metadata.

## Accounting and gate

An offline restart returned `already_started` with zero new calls. Read-only
SQLite snapshot checks confirmed the old three-call ledger and r2 one-call ledger
retain their exact database hashes. The new diagnostic ledger records one call
to Gemini 3.6 Flash. Five initial attempts are now recorded across three distinct
stores, with no resets or relabelling.

[Evidence](../evidence/B13-g36-diagnostic.json) includes exact source/image/settings,
commands, authorization, safe audit, artifact hashes and limitations. Local files:
`C:/Users/jiang/mirum-evidence/B13/g36-diagnostic-20261112/`.

B13/V15 remains blocked; B14 and E01/E02 are not eligible. The changed model needs
fresh qualification before any completion claim. The next useful diagnostic is
a bounded comparison of gRPC and REST under an explicit diagnostic call budget.
No further authenticated request, full evaluation, fallback, main merge or
deployment was performed.
