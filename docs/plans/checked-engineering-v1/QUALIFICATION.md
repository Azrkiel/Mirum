# B13 release qualification

**Full AI-assisted V1: BLOCKED. B14 prerequisite: not satisfied.**

Starting commit: `53408cb4f4b88d35fae3ce5fdc3061144c31bc29`.
Final tested implementation: `ea721d90109e6a9fe0530fdf95132eeef70bb69d`.
No merge, publication or deployment occurred. No B14 release candidate is claimed.

## Evidence and scope

The [evidence manifest](evidence/B13.json) records exact commands, revisions,
results, artifact fingerprints and observation limits. Full local evidence is
under `C:/Users/jiang/mirum-evidence/B13/`.

Runtime: B01 image
`sha256:9c18650a306cd167cce98465427ba633948c47aa525d43f874b2fecdb0cc2583`,
Linux x86_64, Python 3.10.22, CadQuery 2.6.1, OCP 7.8.1.1.post1,
NumPy 2.2.6, Pydantic 2.9.2 and Streamlit 1.39.0. Browser:
Chromium 158.0.8127.18 with Playwright 1.61.0.

- **443 passed**, 21 existing warnings, 796.99 s, exit 0: all engineering modules,
  process lifecycle/descendant tests, CEM requirements/API, glider search,
  general native API and all frontend regressions. This invocation started at
  `aef716e5d514a2eb555cce8e2a79e8ec89424147`. Later edits only improve standalone
  qualification tools. The final backend/frontend source is identical:
  `git diff aef716e ea721d9 -- backend frontend` is empty.
- Final real HTTP accepted/rejected/cancelled probe passed at
  `21b52e6a2aa87ce936e5f13a73c26aa08a8b76ea`, without provider credentials/calls.
  Its first cold-start attempt exceeded a 20-second probe readiness window under
  concurrent native testing. A 90-second readiness window passed; the admitted
  job's 420-second budget is unchanged.
- The final browser probe at the final SHA covers actual native selection,
  preview, report and ten browser downloads, refresh, two-tab edits, AI-unavailable
  fallback, unsupported input, cancellation, rejection, OS write failure and
  expiry. It blocks external browser requests. Unsupported input is explicitly
  HTTP-seeded; expiry uses accelerated persisted timestamps, not a six-hour wait.
  The earlier B13 browser-action timeout is retained as a failed development run,
  not reported as a pass. The final manifest records the rerun and page errors.
- Saved complete ZIP/brief replay at the final SHA matched all three verdicts
  (rejected/rejected/accepted) and **51 quantities**, with absolute tolerance
  `1e-6` and relative tolerance `1e-8`, without AI. Input identities, verifier
  source hashes and runtime versions agree. Independent raw STEP/ZIP equality
  is deliberately not the numerical replay criterion.
- Actual removal/replacement of the test container reused the named volume.
  The accepted ZIP remained 16,053 bytes with SHA-256
  `d3f5222ecf449f7e031b3e2e8c889234988e50b1c2e8d89e1164d6b853abf5be`.
  Zero tagged owned native workers remained active before/after replacement.
  This verifies replacement of the pinned runtime, not B14's fresh production build.

## V01-V15 release matrix

| Gate | State | Actual evidence / limitation |
|---|---|---|
| V01 | Partial; live blocked | Form/AI oracle-fixture semantic parity and reviewed-fixture native execution pass. No usable live proposal exists. |
| V02 | Verified | Real native/HTTP/browser rejects 5/10 mm and selects 15 mm; actual artifacts retained. |
| V03 | Verified | Low allowable rejects all candidates through native/HTTP/browser; diagnostics contain no accepted geometry. |
| V04 | Form/offline verified; live blocked | Strict quantities, missing/contradictory requirements and typed review blockers pass. Live ambiguous corpus is unattempted. |
| V05 | Typed/offline verified; live blocked | Capability exclusions and HTTP-seeded holes block UI execution. Unsupported oracle fixtures are not live evidence. |
| V06 | Verified native | Real OCP wrong-size/offset/hole/extra-solid/rotation/root-section cases fail design checks despite valid B-reps. |
| V07 | Verified with labelled fault seams | Missing/nonfinite/malformed/stale/false-pass fixtures fail closed; real owned-process deadlines and OS publication failure are exercised. |
| V08 | Verified | Actual captured-byte identity, brief/candidate associations, payload mutations and HTTP tamper 503 pass. |
| V09 | Verified | Real two-tab/edit/refresh behavior, SQLite CAS, proposal/finalization reservations, idempotency and unreviewed-submit rejection pass. |
| V10 | Verified | Real worker cancel/deadline/shutdown/crash recovery and terminal races pass; ZIP survives container replacement. Late-cancel reservation leak repaired. |
| V11 | Verified | Real OS write denial, integrity/capacity/allowlist tests, lease/expiry/410 and legacy cleanup isolation pass. Held-lease expiry response repaired. |
| V12 | Verified native replay | Saved brief reproduces verdicts and 51 measured quantities within pinned tolerances, without a provider call. |
| V13 | Verified | 443 engineering/process/CEM/glider/general-native-API/frontend regressions pass; caller ordering and narrower legacy claims remain. |
| V14 | Verified typed browser; live AI blocked | Actual preview, reports, downloads, refresh, tab and failure outcomes pass. Missing-provider fallback is observed; successful live extraction is not. |
| V15 | BLOCKED | Three initial real attempts failed; no usable draft. Twenty-seven cases remain unattempted. Fixed benchmark and accuracy gate are unchanged. |

## Repairs proved by regression

Cancellation after publication removed files but left 1,236,972 reserved bytes.
The red test observed that leak. Discard now clears terminal nonaccepted storage
reservations transactionally after removing the files.

An active lease correctly retained expired files but the public job view still
reported available delivery. The red HTTP test demonstrated it. Public views now
return compact expired state while the existing stream finishes; new leases
return 410. Both added assertions pass in the 443-test run. No physics threshold,
capability, reviewed input or legacy ordering changed.

## V15 live observations and budget

The corpus is unchanged:
`7fb09dcc06626b7fbe31c0e9a2237792ded90ad292651b33e5d4db2ff29bcfea`.
It contains 10 complete, 10 ambiguous and 10 unsupported cases. The live runner
strips `fixture_response` before processing. No oracle generates a live result.

The user supplied a shared environment-file location. Only its single
`GEMINI_API_KEY` is mapped to the existing provider's `GEMINI_API_KEY_HEAVY`.
Other credentials, key pools and model overrides are not loaded. Credentials
and unrestricted prompts/replies are neither printed nor committed.

Configuration: Gemini, `gemini-3.5-flash`,
`google-ai-generativelanguage/0.6.10`, temperature 0.4, output cap 1024 tokens,
45-second call limit, SDK retries disabled. The persistent
`mirum-checked-b13-live` ledger preserves revision claims and consumed budgets.
No call deadline, size bound, provider or corpus was expanded.

**Three initial attempts consumed; zero repairs; no case repeated.**

- C01: TLS certificate-chain failure before a usable response.
- The existing trusted system CA bundle then verified TLSv1.3 and h2 negotiation.
  gRPC used `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/etc/ssl/certs/ca-certificates.crt`;
  [official gRPC setting](https://github.com/grpc/grpc/blob/master/doc/environment_variables.md).
  Verification was never disabled.
- C02: bounded preparation deadline, no usable reply.
- C03: SDK `DeadlineExceeded`, `HTTPStatus.GATEWAY_TIMEOUT`, no usable reply.

Initial live source: `aef716e5d514a2eb555cce8e2a79e8ec89424147`;
resumed source: `be4b60374b75bc1ed54d7feaacf8cf1689adce68`. Resume skips already
claimed/completed cases; it does not reset budgets or repeat C01.

The resumed command uses the B01 image, read-only source, output at `/evidence`,
the user-provided credential file mounted read-only at `/run/existing-provider.env`,
and the durable budget volume at `/tmp/mirum/v15`:

`python scripts/checked_engineering_live_eval.py /evidence/live /tmp/mirum/v15 be4b60374b75bc1ed54d7feaacf8cf1689adce68 /run/existing-provider.env`

No more live calls are made in this evaluation. There are no usable live drafts
from which to establish accuracy; 27 cases are not evaluated. Zero observed drops
from zero usable replies does not establish the full-corpus zero-drop gate.
The 8/10 complete-case accuracy gate is unproven. No unreviewed native execution
occurred; the live runner additionally guards ProcessRunner.

TLS/HTTP2 works after the CA repair, but configured RPC reachability/compatibility
within the existing deadline remains unresolved. No credential rejection was
observed. The installed SDK's Python 3.10 support warning remains material.
Required prerequisite: a working existing Gemini GenerateContent path within
the pinned limits, followed by properly authorized live qualification. Failed
cases/budgets must not be reset or silently retried.

## Release decision

B13 remains blocked. B14's fresh build/setup, release browser/HTTP smoke, final
candidate replay/recovery and integration handoff are not checked off. Existing
typed behavior is demonstrated but is not labelled a B14 qualified typed-only
candidate. E01-E05 and F01-F11 remain untouched. Preserve artifact and live-budget
volumes. No bandit/pip-audit/ruff pass, physical/manufacturing evidence, production
deployment or main integration is claimed.
