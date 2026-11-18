# B13 release qualification

**Live corpus qualification: PASSED (2026-11-18, run `qual-20261118-k6`). B13 steps 12-13 (live proposal reviewed in the real UI, V01-V15 requalification) remain; B14 prerequisite not yet satisfied.**

**2026-11-17/18, GitHub Actions runner (`.github/workflows/b13-live.yml`), key k3, `gemini-4-hydrogen`, google-genai 2.8.0, thinking low.**
Five qualification runs; run 5 qualified. Raw replies are kept out of evidence; safe JSON is under
`C:/Users/jiang/mirum-evidence/B13/u5-20261117/`.

| Run | Commit | Result |
| --- | --- | --- |
| `qual-20261117-hydrogen-k3` | `83e46ac` | 10/10 complete accurate, A01-A05 clarification, 0 silent drops; stopped at A06 on HTTP 429 (16 calls) |
| `qual-20261117-hydrogen-k3-paced` | `83c946c` | stopped at C01 on HTTP 503 (1 call) |
| `qual-20261118-k3-unavail` | `d6dacf2` | policy `b13-unavailable-429-503-v1` (owner Q1(b)): C01 deadline -> unavailable (1 of 3 allowed), C02/C03 accurate (22 s each), stopped at C04 on HTTP 504 (4 calls) |
| `qual-20261118-k3-504v2` | `554b442` | policy `b13-unavailable-429-503-504-v2` (owner, 2026-11-18): all 30 cases accounted for but every one unavailable (9 HTTP 429, 13 HTTP 503, 7 HTTP 504, 1 deadline); 0/10 complete accurate, 0 silent drops, 0 unreviewed executions -> blocked (30 calls) |
| `qual-20261118-k6` | `f11cf90` | **PASSED.** Policy v2, new key k6 (owner, 2026-11-18). 30/30 cases accounted for, 0 unavailable, 0 deadlines; **10/10 complete accurate**; A01-A10 all `clarification_required` as expected; U01-U10 9 `unsupported` + U07 `clarification_required` (blocked, not finalizable); 0 silent drops, 0 unreviewed executions. 30 calls, 5.5-25.8 s per case. Image `sha256:3527bfa6...` |

The scoring, corpus, 45 s deadline, 4096 tokens and no-SDK-retry rules are unchanged. No V-row is promoted.

Latest supplied-key diagnostic: metadata access passed, and ordinary application
gRPC generation on `0c0458ae802cedba75d7680809a8254b3f76ab7d` reached local parsing
in 7.358356 seconds with the 4096-token setting. The response failed strict
JSON/schema validation; the one-call budget allowed no repair, and exact validation
errors were not retained. No accurate reviewable draft or qualification pass is
claimed. See [handoff](handoffs/B13-provided-key-diagnostic.md) and
[evidence](evidence/B13-provided-key-diagnostic.json). The key value is not recorded.

Latest controlled result: after the user added the approved domain exception,
TLS verification observed a Google Trust Services issuer. The unchanged 1024-token
C01 request still returned actual HTTP 503/UNAVAILABLE in 3.748222 seconds, with
a provider high-demand indication. Excluding Avast did not resolve this request.
The user removed the temporary exception; a TLS-only check verified restoration
of the previous Avast issuer with zero additional API calls. Separately, an unqualified
4096-token candidate at `0c0458ae802cedba75d7680809a8254b3f76ab7d` passes 85 offline
tests; no live request used that new allowance. See [handoff](handoffs/B13-avast-controlled-result.md)
and [evidence](evidence/B13-avast-controlled-result.json). No V-row is promoted.

Latest checkpoint: the user authorized the one-call domain-exception comparison.
The automation tool rejected opening Avast, so the setting change requires the
user's manual action. A post-restart check still observed the Avast issuer and
no request reservation/result; zero authorized comparison calls have run.
See [checkpoint](handoffs/B13-avast-test-checkpoint.md). No security setting or
qualification gate changed.

Latest investigation: two additional bounded diagnostic calls returned a REST
ReadTimeout (35.199233 seconds) and actual HTTP 503/UNAVAILABLE (1.136582 seconds).
A TLS-only probe confirmed Avast re-signs the provider connection even in Docker.
That explains the historical trust-root failure; its role in current generation
failures remains unproven pending an authorized controlled exception comparison.
The old SDK also discards thinking-token/model metadata and lacks thinking config.
See [handoff](handoffs/B13-root-cause-investigation.md) and
[evidence](evidence/B13-root-cause-investigation.json). No application/security
configuration or release gate changed. Earlier diagnostic results below remain historical.

Latest diagnostic: model metadata access succeeded over gRPC and REST. One REST
C01 generation returned in 5.137554 seconds with `MAX_TOKENS` under the existing
1024-token cap; it was rejected as incomplete. No accurate draft, full corpus
rerun or release promotion is claimed. The application remains unchanged at
`0f1864adb06e548222265e9d8fd6377c47aad509`; REST was diagnostic-only.
See [handoff](handoffs/B13-transport-comparison.md) and
[evidence](evidence/B13-transport-comparison.json). Earlier results below are historical.

Latest model diagnostic: at the user's request, checked preparation now selects
`gemini-3.6-flash`. Implementation `0f1864adb06e548222265e9d8fd6377c47aad509`
passed 84 targeted offline tests. A separate authorized one-call C01 diagnostic
hit the outer preparation deadline after 45.037221 seconds, with no draft and
no repair. Earlier ledgers remain unchanged. See [handoff](handoffs/B13-g36-diagnostic.md)
and [evidence](evidence/B13-g36-diagnostic.json). No full corpus rerun or release
gate promotion occurred; the results below describe historical candidates.

Latest result: the user authorized a distinct fresh 30-case/60-call run at frozen
implementation `2ce8f217cff8c64c2fa91226b1de67e006a6493c`. C01 returned SDK
`ServiceUnavailable` (SDK-mapped status 503) in 1.401079 seconds. The run stopped with one
initial attempt, zero repairs, no usable drafts and 29 cases unattempted. Offline
restart preserved the same one-call result without another request. The old
three-call ledger remains unchanged. See [live-r2 handoff](handoffs/B13-live-r2.md)
and [evidence](evidence/B13-live-r2.json). Authorization is no longer the blocker;
configured provider service/access remains unresolved. No V-row is promoted.

November 12 follow-up: [provider repair checkpoint](handoffs/B13-provider-repair.md)
and [supplemental evidence](evidence/B13-provider-repair.json) freeze implementation
`2ce8f217cff8c64c2fa91226b1de67e006a6493c`. Per-client OS trust repairs a reproduced
TLS configuration fault; 83 focused offline tests pass. No new live call was made,
and the later GenerateContent deadlines remain unexplained. Separate fresh-run
authorization was required at that checkpoint and has now been received. The
results below remain historical; V01-V15 and B13 completion have not been
refreshed or promoted.

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
