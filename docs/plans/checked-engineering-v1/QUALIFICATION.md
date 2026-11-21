# B13 release qualification

**B13 is complete (2026-11-20).**
- Live corpus qualification passed on 2026-11-18 (`qual-20261118-k6`).
- The live review-UI run passed on 2026-11-19 (`step12-20261119-c01`).
- Step 13 requalified V01-V15 on the RC1 release images at freeze SHA `a7f1953`; see [Step 13](#step-13-v01-v15-on-the-rc1-freeze-2026-11-20).
- B14 is in progress.

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

## Step 13: V01-V15 on the RC1 freeze (2026-11-20)

**Freeze SHA:** `a7f1953652b9bde252cb9e3742a472c0aa1b50d0` (main). All runs below are on that SHA.
- **Workflow dispatches:** release-e2e (`clean=true`), release-storage (`jobs=all`), release-docs, release-images and release-guards are `workflow_dispatch` runs on main, so their artifacts carry release names.
- **CI:** the native gate is the push run on the same SHA.

| Run | Actions | Result |
| --- | --- | --- |
| CI, native gate (3 shards) | 44726464166 | 4325 passed, 18 skipped. The skips are the 14 native XFOIL/AVL tests, 3 "adapter installed by a later PR", the runner-dependent `test_cem_window_coverage` foil-digest receipt, and `test_real_release_md`, which by design runs in schema-tests instead. |
| release-e2e, freeze (`clean=true`) | 44726477204 | 167/167 passed on the release images, with Chromium. All four capabilities produce accepted checked runs, with screenshots, and the LLM-unavailable route is covered. |
| release-storage, all jobs | 44726481438 | Lifecycle, capacity and retention: 23 cases, 22 passed as R/F/A and S08c recorded as `not_injected`. S16: 311 existing storage and process tests pass on the release image. |
| release-docs | 44726485338 | All 13 `RELEASE.md` steps passed from a clean checkout. |
| release-images | 44726489503 | Pinned images build. Pip check, smoke, regen release smoke and AI parity pass. |
| release-guards | 44726494181 | Startup guards pass. |

**Image IDs at the freeze:**
- `mirum-backend:release`: `sha256:0894fed5371a5e3db9f27d6deb519eb71d36eb98372b2e3d15d05433748dcefe`
- `mirum-frontend:release`: `sha256:6fedd6016fadc2d5fbfeedc4a575dde972c6d81b79802019dd4ebd591969c7b1`
- `mirum-backend:b13-eval`: `sha256:17d928dd6748a56b90ff14f4b25892b70433faa120add5670d0c9bb9d3b0f105`

**Lock SHA-256:**
- `requirements-release.lock`: `9ffe8a24…7eed`
- `frontend/requirements.lock`: `d15d3875…21e5d`
- `e2e/requirements.lock`: `a3063bfa…6fd5e`

Full values and the `pip freeze` are in the release-e2e evidence JSON.

**Regen release smoke:** accepted on both the browser and checked routes. The STL and spec bytes are identical across routes. The stored bundle is 130,773,574 bytes, and peak container memory is 2.38 GB.

**Live calls:** none were made for step 13. The live rows cite run 5 (`qual-20261118-k6`, `f11cf90`, Actions 44358308796) and step 12 (`step12-20261119-c01`, `86e6326`, Actions 44638943618). R2's AI parity tests at the freeze SHA bridge both runs to the release image. The lifetime total is 91 generation calls: 90 in B13 qualification and 1 in step 12.

Labels: **R** means a real observation on the release stack. **F** means a fault was injected and verified. **A** means accelerated time. **L** means live provider.

| Row | State | Evidence at `a7f1953` (unless marked live) | Kind |
|---|---|---|---|
| V01 | Verified | Run 5: 10/10 complete cases accurate (L). Step 12: AI proposal identical to the typed request, 10/10 downloads match (L). AI parity in release-images. Native service tests in CI. | L + fixtures |
| V02 | Verified | release-e2e `test_beam_reference`: 5 and 10 mm rejected, 15 mm accepted, STEP preview, every download hash equals the API's. S13 replay. | R |
| V03 | Verified | release-e2e `test_negative_api::test_v03_too_tight_deflection…`: every candidate is rejected on the deflection check only (stress passes), and no geometry is offered. | R |
| V04 | Verified | Run 5 ambiguous cases ended in clarification, with 0 silent drops (L). release-e2e `test_v04_typed_request_missing_the_load…`: `clarification_required`, finalize returns 422, no job is created. | L + R |
| V05 | Verified | Run 5 unsupported cases (L). release-e2e `test_v05_holes_and_second_load…`: both fields `unsupported`, finalize returns 422. 422 on an unsupported capability. | L + R |
| V06 | Verified native | Native gate: real OCP wrong-size, offset, hole, extra-solid and rotation cases fail the design checks. S16 on the release image. | fixtures |
| V07 | Verified | Native gate fail-closed fixtures. S03: the builder is stopped with SIGSTOP and the real 420 s deadline is observed (F). S16. | F + R |
| V08 | Verified | Native tamper tests. S14: one STEP byte flipped, then the same file truncated, on the release stack (F). S16. | F |
| V09 | Verified | release-e2e `test_negative` and `test_negative_api`: stale review returns 409, idempotency, two tabs, refresh. Step 12 (L). | R |
| V10 | Verified | S01, S02, S04a, S04b, S05 and S06 (R): cancel, graceful stop, kill during run and at rename, process inventory, replay. S07a and S07b. S12: replace, and down/up keeping the volume. release-e2e idempotency. | R + F |
| V11 | Verified (labelled) | S08a and S08b (F). S08c `not_injected`: the kernel refused the read-only remount with EBUSY, so it is recorded as observation only (lead decision). S09a (R): real regen bundles retained until the reservation would exceed 512 MiB. S09b and S09c (F). S10 (A). S11 (R). S14 (F). | mixed |
| V12 | Verified | S13: the delivered C01 ZIP replayed with networking disabled (R). Step-12 brief identity (L). | R + L |
| V13 | Verified | The native gate's legacy and engineering suites (4325). release-e2e route and generator labels (`test_llm_route`, `test_generators`). S11. | R + fixtures |
| V14 | Verified | release-e2e beam UI, dragster browser and generator screenshots. S15 failure views (real, plus accelerated expiry). Step-12 screenshots (L). | R + L |
| V15 | Verified | Run 5: 10/10 accurate, 0 drops, 0 unreviewed executions, 30 calls (L). Parity holds at the freeze SHA. | L |

**Mutation proofs (R6):**
- Removing `publisher.recover()` turns S04b red (run 44708636516).
- Skipping the lease checks in cleanup turns S10 red (44708644698).
- Dropping the commit-time cancel check turns `test_engineering_jobs.py::test_atomic_cancel_wins_and_repeated_terminal_transitions_are_immutable` red (44711693867). Black-box S01 and S02 cannot reach that window.

**Evidence copies (local):** `C:/Users/jiang/mirum-evidence/RC1/a7f1953/`.

## V01-V15 release matrix (historical, before RC1)

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

## STEP-12 live review-UI run (passed 2026-11-19)

Finish-B13 step 12 evaluates one live proposal for a fixed-corpus complete request (default C01, input-selectable
among C01-C10 only) through the real review UI of the release stack. On 2026-11-19 the owner approved up to
three provider calls.

- **Job:** `.github/workflows/b13-step12.yml`, `workflow_dispatch` only (inputs `run_id`, `authorization`,
  `case_id`), main only, environment `b13-live` (same secret and concurrency group as `b13-live.yml`).
  Atomic reservation tag prefix `b13-s12-`, cumulative cap 1. Release images come from
  `.github/actions/build-release-images/`; the stack is `compose.release.yml` plus
  `e2e/compose/b13-step12.override.yml` in a disposable project with run-local volumes and a run-local
  `openssl rand -hex 32` token; `down -v` at the end. The override is the only place the AI draft flag is set,
  and passes the provider key (pass-through from the one step that starts the stack) to the backend only.
- **Driver:** `scripts/b13_step12_ui.py` (Playwright on the runner's system Chrome, hashed lock
  `.github/ci-requirements/b13-step12-playwright.lock`). It first proves its scrapers on a typed reference
  draft (no provider), makes one proposal click, checks the displayed values (normalized-input tree, source
  line, brief SHA-256) against the corpus's fixed expected content BEFORE the acknowledgement, then
  acknowledges, finalizes, runs, polls and downloads through the UI, compares each download with the API
  bytes, and compares the semantic identities and selection with the same typed request (brief SHA-256,
  outcome, selected candidate, attempts).
- **Budget, enforced in code:** at most one proposal request, so at most 2 provider calls (initial plus the
  existing single schema repair), never retried; a 429/503/504 or any unavailable outcome stops the run and is
  recorded as `unavailable` (job red, evidence uploaded, no retry). The count is read from the backend's own
  proposal ledger (`audits.calls`, written before each provider call) by `docker compose exec` into the
  backend container, read-only, printing counts and statuses only, before the proposal, after it, and after
  run/poll/download (and after the typed comparison). The job fails if the total exceeds 3 or any call is
  recorded after the proposal. As a hard stop behind that, the override sets the backend's own
  `MIRUM_ENGINEERING_AI_DAILY_CALLS=3` and one call in flight.
- **Security review fixes (2026-11-19):**
  - *Un-ledgered provider routes (M1).* The proposer reads only `GEMINI_API_KEY_HEAVY` (no dedicated variable
    exists), so the key is also visible to `/generate` (no flag, no budget) and `/dragsters/interpret`. The
    override sets `MIRUM_DRAGSTER_INTERPRET_DAILY_CAP=0`, which makes the interpreter refuse before the
    network (`extraction._reserve_provider_call`: `cap <= 0` raises). `/generate` has no cap; the checked UI
    never calls it, and after the run the driver reads the backend access log and fails the job if any path
    outside an allowlist (`/auth/check`, `/engineering/v1/...`) was requested, recording the path counts. An
    allowlist, not a denylist: the verification re-review found a further un-ledgered route
    (`/cem/glider/requirements`), so any route the checked UI does not use fails the job. This detects, it does
    not prevent, such a call; preventing it needs a backend change and its own security review.
  - *One ledger call is one HTTP request (M2).* `backend/tests/test_engineering_provider_single_request.py`
    points the real proposer and the real google-genai 2.8.0 client at a local stub through
    `GOOGLE_GEMINI_BASE_URL` (503 and 429) and asserts exactly one inbound request per `generate()`.
  - *Ledger at exit (L1).* The budget check runs on the at-exit ledger read on every path, recorded as
    `at_exit_budget_problems`.
  - *Screenshots (L3).* Model free text (alerts, source line, expanders) is masked; the evidence error string
    is the exception type plus a fixed message and a `stage` marker, never DOM text.
  - *Reservation order (L4).* Inputs are validated first; the reservation tag is created after the image build,
    stack start and the provider-free `--preflight-only` selector check, as the last step before the first
    provider call.
  - *Rehearsal.* `.github/workflows/b13-step12-rehearsal.yml` (pull_request on the driver, lock, overrides,
    step-12 workflow and `frontend/engineering_ui.py`, plus dispatch) runs `scripts/b13_step12_ui.py
    --rehearsal` with no secret, no environment and the AI flag off: the typed draft of the same case goes
    through the same review UI to acknowledge, finalize, run, poll, download and sha256-vs-API, with the
    ledger read at every checkpoint (0 calls) and the access log checked.
- **Evidence (JSON and PNG only, 90 days):** `evidence.json` (case, source SHA, image IDs, displayed-vs-expected
  rows, acknowledgement/finalize/run outcome, job id, download hashes vs API, call counts per checkpoint,
  verdict) and a PNG per step. Never raw replies, the SQLite ledger, packages or compose logs; failure logs are
  printed to the job log with the token and key shapes redacted.
- **Residual risk (as accepted for `b13-live`):** container egress is not restricted to the provider host.

### Results

The `b13-step12.yml` workflow was dispatched on `main` at
`86e632694fc62422f9f96fda56fe95055ef8aa80` (PR #85 merge). GitHub Actions run `44638943618`
(`step12-20261119-c01`) passed (exit 0) for C01 under authorization `owner-q2-2026-11-19-max3`.
The reservation tag `b13-s12-step12-20261119-c01` used the `b13-s12-` prefix and consumed the
one-run cap (1/1).

| Check | Observed result |
| --- | --- |
| Provider budget | 1 of 3 authorized calls, for the initial proposal. No schema repair. Ledger counts were `before` 0, `after_proposal` 1, `after_run` 1, `at_exit` 1, `after_stop` 1 (after a graceful backend stop), and `final` 1. |
| Displayed fields | All fields matched the fixed expected values before acknowledgement. The displayed brief SHA-256 matched the API's. |
| AI and typed runs | Both rejected 5 mm and 10 mm and accepted 15 mm, with the same selected candidate and brief SHA-256 `34aee33577a2ce2da90ed9c0e3727c53843cbcca26a4442632cd8d3bdcb5280e`. |
| Downloads | 10/10 UI SHA-256 values matched the API byte hashes and response headers. |
| Access log and evidence | The backend log contained only `/auth/check` and `/engineering/v1/...` paths. The evidence contains neither the raw provider reply nor the key. |

The release images were built by the pipeline from PRs #77 and #80. The run's image IDs are in
the [B13 evidence manifest](evidence/B13.json).

The evidence is retained as JSON and PNG only in Actions artifact `b13-step12-step12-20261119-c01`
and locally at `C:/Users/jiang/mirum-evidence/B13/step12-20261119-c01/`. This was the last planned
use of key k6. The owner will rotate it after the run.

## Release decision

B13 remains blocked. B14's fresh build/setup, release browser/HTTP smoke, final
candidate replay/recovery and integration handoff are not checked off. Existing
typed behavior is demonstrated but is not labelled a B14 qualified typed-only
candidate. E01-E05 and F01-F11 remain untouched. Preserve artifact and live-budget
volumes. No bandit/pip-audit/ruff pass, physical/manufacturing evidence, production
deployment or main integration is claimed.

## E05 live qualification (planned)

Planned only. No live call has been made and no result is claimed here.

- **Scope:** `co2_dragster`, `regen_chamber`, `glider_cem`, each through its E05 AI proposer and `proposal_contract.conform`. The beam is not re-run.
- **Corpora:** `backend/tests/fixtures/engineering/proposals/<capability>-v1.json`, pinned in `scripts/e05_live_eval.py` by SHA-256 (co2_dragster `fdc429fa...`, regen_chamber `95b161ec...`, glider_cem `c4113bb1...`). A mismatch refuses the run before any call. Corpus sizes: 12, 13 and 14 cases, 3 complete each.
- **Budget (owner Q1, 2026-11-20):** 30 provider calls per capability, 90 total. Every call, including a repair, reserves one `AiCallBudget` unit that is never refunded. Exhaustion ends that capability with `budget_exhausted`. There is no retry.
- **Key (owner Q2):** k6 through environment `b13-live`, secret `B13_GEMINI_API_KEY`. The container gets only `GEMINI_API_KEY_HEAVY`. `GEMINI_API_KEY_PRO` is blank, which keeps the glider key-mismatch guard passing.
- **Reservation:** one tag `e05-<capability>-<run_id>` per capability, created before any call. A capability is reserved once, ever. A second run needs new owner authorization.
- **Scoring (per case):** `accurate` = complete kind, state `proposed`, every `expected_content` path equal to the proposal, and no `ai_proposed` field outside the stated set. `silent drop` = state `proposed` and not accurate. `unreviewed execution` = any subprocess start during proposal (blocked and counted).
- **Gate, per capability (B13 run-5 rule, adapted):** B13 run 5 (`qual-20261118-k6`) passed when all 30 cases ran, complete-accurate was at least 8 of 10 complete cases, silent drops were 0 and unreviewed executions were 0 (`scripts/checked_engineering_live_eval.py`, `qualification` rule). These corpora have only 3 complete cases each, so this plan uses the same rule with the threshold set to every complete case: all cases run, all complete cases accurate, 0 silent drops, 0 unstated AI values, 0 unreviewed executions. Any stop, budget exhaustion or provider error blocks.
- **Evidence:** `e05-live.json` with run id, authorization, tested SHA, image id, corpus SHA-256s, calls per capability, and per-case case id, kind, expected state, state and counts. No prompts, raw replies or reasons. Only `*.json` is uploaded.
- **Workflow:** `.github/workflows/e05-live.yml`, manual dispatch only, `main` only, pace 12 s between calls, 120-minute job limit.
