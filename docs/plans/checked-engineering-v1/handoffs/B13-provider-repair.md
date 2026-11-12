# B13 provider repair and authorization checkpoint

State: blocked pending separate live evaluation authorization.
Branch: `engineering/checked-design-v1`.
Starting commit: `a75136119342c3dee59cadac23f4dd19520f86f2`.
Frozen implementation: `2ce8f217cff8c64c2fa91226b1de67e006a6493c`.
Date: November 12, 2026.

## Demonstrated fault and repair

Offline SDK inspection accepted the current schema and Python version. Python
3.10 triggers a support warning; it is a maintenance risk, not evidence for the
GenerateContent deadlines. No dependency/runtime upgrade was justified by these
checks. Existing CAD/numerical pins and the original B01 recipe remain unchanged.

Credential-free transport checks reproduced a trust fault: default gRPC roots
failed certificate verification; installed OS roots established the channel and
a deliberately nonexistent non-model RPC returned `UNIMPLEMENTED`. The ordinary
preparation provider now supplies OS trust per client, retaining TLS verification,
the SDK gRPC transport, disabled SDK retries, 45-second calls and awaited closure.
The evaluator uses this same provider. There is no global override or fallback.

The earlier CA environment override existed only in the evaluation command.
This repairs that application/configuration gap. It does **not** establish that
the C02/C03 GenerateContent deadlines are resolved. No new authenticated RPC or
generation request ran. The user-supplied credential file was parsed with network
disabled; only its variable name was reported. Its contents were never logged.

## Verification and durable accounting

[Supplemental evidence](../evidence/B13-provider-repair.json) records exact
commands, package/image identities, hashes and limitations. The targeted
proposer/qualification/store selection passed **83 tests, one support warning,
35.62 seconds**, with networking disabled. The tested tree is identical to the
frozen implementation commit. Baseline: 68 tests. Real SDK transport construction
and serialization were also checked offline with the RPC replaced by a fixture.

Evaluator tests cover fixed complete/ambiguous/unsupported oracle inputs, changed
requirements, explicit supported failure with typed recovery, native blocking and
guard restoration, outer deadlines, interrupted reservations, exhausted budgets,
immutable identities and restart without repeated calls. Errors/elapsed time are
per case. Shared blockers remain stopped on restart, including on case 30. Result
JSON replacement is atomic; consumed calls come from the durable SQLite ledger.

The old volume was mounted read-only; a temporary SQLite snapshot confirms three
initial calls, zero repairs and C01-C03 unavailable. Its database hash is unchanged.
All failures are complete inputs. The remaining seven complete cases cannot meet
8/10. Preserve the old volume, ledger, result JSON and historical B13 evidence.

## Authorization still required

Proposed run: `b13-v15-20261112-r2`. Create dedicated volume
`mirum-checked-b13-live-20261112-r2` only after separate explicit authorization.
Use the unchanged corpus SHA-256
`7fb09dcc06626b7fbe31c0e9a2237792ded90ad292651b33e5d4db2ff29bcfea`.
The budget is at most 60 additional calls: one initial and at most one schema
repair for each of 30 cases. The three old calls remain recorded separately.
The first original C01 prompt also checks the configured endpoint; no free probe.
Stop on a shared access/runtime blocker. No mid-run model/prompt tuning, repeated
cases, unreviewed execution, relaxed bounds or hidden provider fallback.

Image: `sha256:9c18650a306cd167cce98465427ba633948c47aa525d43f874b2fecdb0cc2583`.
Settings: Python 3.10.22, `google-ai-generativelanguage/0.6.10`,
`gemini-3.5-flash`, temperature 0.4, output cap 1024 tokens.
Prompt/schema: `engineering_extraction_v1` / `engineering_proposal_v1`.
Capability/policies: `rectangular_cantilever` 1.0.0, `beam_numerical_v1`,
`engineering_resources_v1`.

The evaluator keeps positional output/state/source/optional-credential arguments
and now requires `--run-id`, `--authorization`, `--previous-run` and `--image-id`.
Use new output and state locations. Never point it at the historical volume.
Record the user's fresh authorization separately before invocation. The user
provided the credential-file path during this checkpoint; mount only that file
read-only and keep its contents out of commands, logs and committed evidence.

## Remaining sequence

After authorization, finish plan steps 9-13: fixed live corpus, exact review of a
successful corpus proposal in the actual UI, typed/live semantic and execution
parity, broad regressions, real HTTP/browser/native/package/replay/storage checks,
then refreshed V01-V15 evidence. Only qualifying evidence permits B13 completion
and B14 to start. The old 443-test and HTTP/browser evidence remain historical.

B14's pinned installed images, clean installation and operator instructions are
untouched. E01/E02 remain ineligible. No main merge, publication or deployment.
Preserve the untracked execution plan and separate Phase 3 work.
