# B13 authorized fresh evaluation: stopped on shared provider failure

State: blocked. Date: November 12, 2026.
Branch: `engineering/checked-design-v1`.
Tested implementation: `2ce8f217cff8c64c2fa91226b1de67e006a6493c`.
Starting documentation head: `39b9ae31c4d8e96610417bfa4700ba11038429fd`.

## Authorization and actual result

The user explicitly authorized one fresh fixed 30-case evaluation with at most
60 additional calls: one initial and at most one schema repair per case, under
the existing limits and shared-blocker stop rule. Authorization is recorded as
`user-20261112-r2-60calls`; this resolves the earlier authorization checkpoint.

Run `b13-v15-20261112-r2` used the frozen source, immutable B01 runtime image,
unchanged provider/model/settings and unchanged corpus. A new application-owned
volume, `mirum-checked-b13-live-20261112-r2`, was created after authorization.
The credential file was mounted read-only; no key, raw prompt or provider reply
was logged. C01 was the first corpus request and the endpoint check; no separate
authenticated diagnostic or throwaway generation was sent.

**C01 returned unavailable after 1.401079 seconds.** The SDK classification was
`ServiceUnavailable`, code `HTTPStatus.SERVICE_UNAVAILABLE` (503). It was not
classified as a TLS certificate failure, credential rejection, model rejection,
schema rejection or incomplete response. These negative classifications do not
prove credential validity or model accessibility. The root cause of 503 remains
unresolved; there is no evidence that changing Python or models would fix it.

The shared-blocker stop rule ended the evaluation immediately: **one consumed
initial attempt, zero repairs, zero usable drafts, 29 unattempted cases**. The
59 unused calls are a ceiling, not permission to bypass this stop or reset C01.
No unreviewed execution occurred. Zero observed drops from no usable drafts does
not qualify the corpus. Exit code 2 is the expected blocked result.

## Accounting and preservation checks

- Restarted the same evaluator/identity with networking disabled and a separate
  output directory. It read the persisted stop reason, exited 2, issued no new
  request and retained exactly one call. Result JSON is byte-identical.
- Inspected both volumes through read-only mounts and temporary SQLite snapshots.
  The old run still contains three failed initial calls and has its original
  database hash. The new run contains one failed initial call and a durable stop.
- No native execution occurred; the evaluation container was removed on exit.
- The historical and fresh runs account for four initial attempts total. Their
  ledgers, identities and evidence remain separate. No billing inference is made.

Exact commands, runtime/configuration identities, authorization, safe per-case
audit, hashes and limitations are in [B13-live-r2.json](../evidence/B13-live-r2.json).
Local evidence: `C:/Users/jiang/mirum-evidence/B13/live-r2-20261112/`.
The earlier checker invocation lacked writable temp storage; its successful
rerun added a small tmpfs and never wrote either budget volume.

## Release decision and next dependency

B13/V15 remains blocked on the configured provider's service/access path.
Do not automatically continue or repeat this stopped run, spend unused calls on
diagnostic generations, tune the prompt/model, or relabel it as a fresh run.
Preserve both volumes and all results. Further candidate diagnosis starts offline;
any changed live qualification needs an explicit candidate/run/budget decision.

The live UI parity and full V01-V15 refresh remain unfinished. The old broad
regression and browser/HTTP/replay results remain historical. B14's fresh pinned
release images, installation and operator documentation have not started.
E01/E02 remain ineligible. No main merge, publication or deployment occurred.
