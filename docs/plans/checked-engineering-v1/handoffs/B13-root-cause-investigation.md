# B13 root-cause investigation and controlled-test boundary

State: blocked, pending authorization for a controlled antivirus/network test.
Date: November 12, 2026.
Application source remains `0f1864adb06e548222265e9d8fd6377c47aad509`.

## New observations

The user requested deeper testing to identify the actual failure. Two diagnostic
generation calls were reserved durably before requests, outside the stopped
qualification runs. Both used the unchanged original C01 prompt, Gemini 3.6 Flash,
1024 output tokens, temperature 0.4, no retries/repair, a 45-second external
deadline and the same response bounds. The second changed only thinking level.

1. Baseline raw REST/default thinking: **ReadTimeout after 35.199233 seconds**;
   no HTTP response or usage metadata was received.
2. Raw REST/`thinkingLevel=MINIMAL`: **actual HTTP 503, status UNAVAILABLE,
   after 1.136582 seconds**. No generation usage or usable draft was returned.

REST can therefore stall too; the failures are not exclusive to gRPC. Neither
call proves that thinking control or a token increase fixes the workflow. The
previous REST `MAX_TOKENS` result is genuine, but is a separate observed failure.
No full-corpus evaluation or unreviewed native execution occurred.

## Confirmed interception

A credential-free TLS probe from the same Docker image connected to
`generativelanguage.googleapis.com` with hostname/certificate verification on.
The peer certificate issuer was **Avast Web/Mail Shield Root**. Its signing root
matches the Avast root installed in Windows and the container's trusted OS bundle.
Avast Antivirus and Firewall services are running. No protection was changed.

This establishes that Avast intercepts the connection even inside Docker. It
explains the original stock-gRPC-root certificate failure and the success after
using OS trust. It does **not yet establish** that Avast causes the current
timeouts or the HTTP 503. Provider-side unavailability remains another possibility.
The accessible WebShield report had no matching domain entries; other relevant
Avast logs were not readable, and no escalation was attempted.

## SDK observation gap

Offline protobuf tests confirmed that `google-ai-generativelanguage/0.6.10` lacks
`thinking_config`, `thoughtsTokenCount` and serving `modelVersion` fields. Its REST
decoder ignores unknown JSON fields, discarding the latter two. Earlier probes
also rejected incomplete candidates before retaining usage. The new diagnostic
retains safe raw-REST usage fields before completeness validation, but the two
new live calls returned no usage. Thinking-token exhaustion is still unproven.

The initial generic certificate classifier returned `other`; direct issuer
inspection then identified Avast. The public Google status page exposed no
incident data through static retrieval, so no current-outage conclusion is made.

## Prepared next experiment, not authorized

The decisive next comparison is a temporary Avast website-scanning exception
for **only `generativelanguage.googleapis.com`**, followed by one unchanged baseline
C01 request and immediate removal of that exception. This reduces scanning for
that domain and therefore needs separate explicit approval. It does not disable
antivirus/firewall globally or disable application TLS verification.

The prepared script refuses before authorization, requires a verified Google
Trust Services peer instead of Avast before issuing the request, reserves one
call durably, and rejects repetition. Its authorization guard passed offline
with all network/credential reads prohibited. It preserves the model, prompt,
1024-token cap, default thinking, no-retry policy and existing limits to isolate
the scanning change. No security exception has been added.

If authorized, record existing settings/exceptions, add only the domain exception,
verify the peer certificate, execute the bounded command, and remove only the
new exception in cleanup. Verify removal and re-observe interception afterward.
If that scoped change is unavailable, stop rather than silently disabling a
global security feature. See the [prepared command and evidence](../evidence/B13-root-cause-investigation.json).

Vendor references: [Avast website troubleshooting](https://support.avast.com/avast/en/us/home/current/solutions/troubleshoot-website-access-issues),
[domain exceptions](https://support.avast.com/avast/en/us/home/current/solutions/antivirus-scan-exclusions).
Their guidance supports a controlled comparison; it is not proof of this cause.

B13/B14 remain blocked and E01/E02 remain ineligible. Source, dependencies, corpus
and old ledgers are unchanged. No merge or deployment occurred.
