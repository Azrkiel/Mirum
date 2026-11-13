# B13 controlled exception result: provider high demand

State: blocked on live generation; temporary exception cleanup pending.
Live application source: `0f1864adb06e548222265e9d8fd6377c47aad509`.
New offline candidate: `0c0458ae802cedba75d7680809a8254b3f76ab7d`.
Date: November 13 UTC / November 12 local, 2026.

## Controlled live result

The user approved the domain-only Avast exception and one unchanged C01 request,
then confirmed adding it manually. The automation tool could not open Avast;
no alternative automation path was attempted. Authorization and the durable
one-call reservation remain separate from all stopped qualification ledgers.

The new connection passed TLS/hostname verification with certificate issuer
**CN=WE2,O=Google Trust Services,C=US**, replacing the observed Avast issuer.
Only then did the prepared script reserve and issue the one generation request.
The original C01 prompt, Gemini 3.6 Flash, default thinking, 1024-token cap,
temperature 0.4, schema, response bounds and no-retry policy stayed fixed.

The request returned **actual HTTP 503 / UNAVAILABLE after 3.748222 seconds**.
The provider error indicated **high demand**. No model candidate, usage metadata,
reviewable draft, repair call or unreviewed native execution occurred.

This reproduces the current 503 while the exception is effective and a verified
Google certificate is observed. The generation service's reported high demand
is the blocker for this request. Excluding Avast did not resolve it. Avast's
interception explains the original trust failure; this test does not determine
the exact cause of every earlier timeout or imply that token exhaustion is gone.

## Separate offline output-budget candidate

The earlier successful REST response stopped at `MAX_TOKENS` with 1024 tokens.
The new candidate gives checked preparation its own 4096-token allowance, within
the pre-existing ProviderSettings ceiling, while legacy clarifier tokens remain
1024. Model, prompt, SDK, gRPC transport, 45-second deadline, 16 KiB structured
response bound and all physical policies remain unchanged.

SDK request/configuration assertions were observed failing at 1024, then passing
at 4096. The proposer/qualification/store selection passed **85 tests, one existing
Python support warning, in 36.80 seconds**, with network disabled. A new truncated
SDK-candidate regression proves failure remains visible, the transport closes,
one consumed attempt is recorded and no schema repair occurs.

This is an **offline candidate, not live-qualified**. It has not demonstrated a
complete 4096-token live draft and cannot fix provider capacity by itself. Any
future qualification must freeze and exercise this exact ordinary-provider
configuration, with its applicable explicit live-run budget.

## Cleanup required

The user has been asked to remove only the temporary
`generativelanguage.googleapis.com` exception. Removal confirmation and restored
scanning/certificate verification are still pending. Do not claim rollback
complete. The app cannot be automated through the blocked computer-use tool.
No more provider requests are planned from this exhausted one-call authorization.

After the user confirms removal, verify the prior Avast issuer/scanning state
with a TLS-only check, record the result, and close the cleanup checkpoint.
Preserve all pre-existing exceptions and all consumed-call records.

[Evidence](../evidence/B13-avast-controlled-result.json) records commands/settings,
safe error classifications and fingerprints. Local evidence remains under
`C:/Users/jiang/mirum-evidence/B13/avast-controlled-test-20261112/`.
B13/B14 stay blocked, E01/E02 stay ineligible, and no merge/deployment occurred.
