# B13 transport comparison: access works, output budget exhausted

State: blocked. Date: November 12, 2026.
Application implementation: `0f1864adb06e548222265e9d8fd6377c47aad509`.
Starting documentation head: `a65e16c3b687970e2cec4b46142c3ea887ef1664`.
Application source/runtime dependencies were not changed.

## Requested test and actual observations

The user requested testing the proposed gRPC-versus-REST path. The diagnostic
was bounded to two model-metadata requests and one original C01 generation,
with no retries or schema repair. Authorization, exclusive durable request
reservations, exact commands and safe observations are in
[B13-transport-comparison.json](../evidence/B13-transport-comparison.json).

Both authenticated `GetModel` requests passed using the same supplied key,
Gemini 3.6 Flash and installed OS trust: **gRPC 0.637708 seconds; REST 0.608386
seconds**. Returned metadata matched the requested model and advertised
`generateContent`. This establishes metadata access, not extraction accuracy or
a completed generation. Each request used a 15-second timeout and disabled SDK
retries; REST authentication refresh/reissue was also disabled.

The one REST generation request used the unchanged original C01 prompt,
provider schema, temperature 0.4 and 1024-token output cap. The installed SDK's
synchronous REST transport ran inside a separate disposable container with a
40-second SDK timeout and external 45-second process deadline. It returned one
candidate after **5.137554 seconds**, with finish reason **`MAX_TOKENS`**.
The local completeness guard rejected it: no complete reviewable draft, schema
repair, finalization or native execution occurred. Exit 2 is a diagnostic failure.

`MAX_TOKENS` means the request's token maximum was reached, according to
[Google's response reference](https://ai.google.dev/api/generate-content#FinishReason).
That identifies output-budget exhaustion on this REST attempt. It does not
explain the earlier gRPC deadlines or establish how thinking consumed the budget.
The diagnostic did not retain token usage from the incomplete candidate. Its
local RuntimeError/candidate flag is not a server rejection of `candidate_count`.

Offline inspection also confirmed that the installed SDK's `GenerationConfig`
protobuf lacks `thinking_config`, so this SDK cannot expose that control through
its current typed request. No SDK upgrade or model substitution was made.

## Bounds and preservation

The local request-reservation/privacy harness and common-validation/one-call
generation harness passed offline with fixtures. These are not live accuracy
evidence. An initial local harness syntax error was corrected before live use.
No raw key, unrestricted prompt or provider reply was logged. All three request
reservations remain durable and prevent automatic repeats.

Prior stopped evaluation/diagnostic volumes were not mounted or changed. The
REST test used a new dedicated volume and did not reset any consumed budget.
Local evidence: `C:/Users/jiang/mirum-evidence/B13/transport-comparison-20261112/`.

## Next candidate and release gate

A bounded output-token increase or explicit thinking control is now an
evidence-based candidate to test. Production REST also needs cancellation and
cleanup tests; this synchronous diagnostic is not a production transport change.
Keep the 45-second deadline, 16 KiB response bound, fixed corpus and physics gates.
Freeze and qualify the same configuration used by ordinary preparation.

B13 remains blocked pending accurate live drafts, full corpus qualification and
the V01-V15 refresh. B14 has not started; E01/E02 remain ineligible. No main merge,
deployment, fresh full evaluation or additional provider call was performed.
