# B13 approved antivirus comparison: manual setting step required

Superseded checkpoint: [the user added the exception and the test ran](B13-avast-controlled-result.md).
One call returned actual HTTP 503/high demand with a verified Google certificate.
Manual removal and verification are now pending.

State: blocked pending the manual domain exception step.
Date: November 13, 2026 UTC (November 12 local).
Application source: `0f1864adb06e548222265e9d8fd6377c47aad509`, unchanged.

The user approved the proposed temporary Avast exception for only
`generativelanguage.googleapis.com`, one unchanged C01 request, then removal of
the newly added exception. Authorization `user-20261113-avast-domain-one-call`
is recorded in local evidence. Do not request the same authorization again.

The Windows computer-use tool refused to open the observed Avast executable:
`product policy blocks this app: C:\Program Files\Avast Software\Suite\AvastUI.exe`.
The installed computer-use skill's mandatory guidance also prohibits automating
Windows anti-malware apps. No alternative automation route was attempted and no
antivirus/firewall setting was changed. The agent should have checked this
restriction before offering to automate the setting change.

A manual handoff was requested through the asynchronous input tool: add only the
provider domain in Avast's website/domain exceptions and confirm when added.
The user will also need to remove that same exception after the test. Preserve
all pre-existing settings and exceptions; never disable global protection or TLS
verification. If the user declines, leave protection unchanged and retain this
blocked evidence.

After a server restart, state was checked before repeating work. The connection
still presented an Avast-signed certificate, no request reservation or result
existed, and **zero generation calls** had been consumed from the authorized
one-call comparison. The offline authorization/interception guard passed: it
does not issue HTTP, read the credential or reserve a request while Avast still
intercepts. The script also refuses before authorization and rejects duplicates.

## Resume after manual confirmation

1. Verify the user added only the authorized domain exception.
2. Verify a new TLS connection no longer presents the Avast issuer. The prepared
   script requires a verified Google Trust Services peer before sending the API
   request; if it does not observe that peer, it exits without consuming a call.
3. Run the already prepared bounded command from
   [the root-cause evidence](../evidence/B13-root-cause-investigation.json), using
   `C:/Users/jiang/mirum-evidence/B13/avast-controlled-test-20261112/` and the same
   original C01 prompt, default thinking, 1024-token cap and 45-second deadline.
4. Preserve the request reservation and safe result. Do not repeat a reserved
   request after interruption or failure.
5. Have the user remove only the new exception, then verify its removal and
   restoration of the previous certificate/scanning state. The agent cannot
   automate this removal through the blocked computer-use app.

Local checkpoint: `checkpoint.json` in the same evidence directory. The original
ledgers, corpus and code remain unchanged; B13/B14 and E01/E02 are not promoted.
