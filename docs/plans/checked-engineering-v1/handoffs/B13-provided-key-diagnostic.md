# B13 supplied-key diagnostic: generation reached local validation

State: blocked on live JSON/schema validation.
Date: November 13, 2026.
Tested application: `0c0458ae802cedba75d7680809a8254b3f76ab7d`.
No application source, dependency or antivirus change was made in this diagnostic.

## Authorization and secret handling

The user supplied a key and requested testing it. The diagnostic was bounded to
one model metadata request and one original C01 generation request, with no
retries or schema repair. Authorization is `user-20261113-provided-key`.
Do not copy the key into this handoff, evidence, source, command arguments or logs.

A verified hidden-input helper passed the key through Docker stdin into the
diagnostic process only. No credential file was created or replaced. A key-pattern
scan found no API-key value in the diagnostic files. The helper and containers
exited; the original credential file and all stopped ledgers are unchanged.

## Observed result

- Gemini 3.6 Flash metadata access passed in **0.218302 seconds**.
- C01 used the ordinary application gRPC provider and the frozen 4096-token
  configuration. It reached local response parsing in **7.358356 seconds** with
  no provider-failure classification or timeout.
- The reply failed the one-call guard's local JSON/schema validation. The strict
  checks remained enabled. No reviewable draft, finalization or native execution
  occurred; the one initial attempt remains recorded in its own volume.

The provider only returns a reply after one STOP candidate and nonempty text.
The recorded `schema_valid=false` therefore establishes that this attempt reached
local parsing. It does not establish a correct draft. The guard did not retain
the exact JSON/Pydantic error locations/types, and the raw response was not saved,
so the particular field/format failure cannot now be identified from this run.

The guard raises before BriefProposer receives the malformed reply. Consequently,
audit response/token counters remain zero despite a returned provider candidate;
do not infer zero token use or billing. The normal workflow permits one schema
repair, but this explicitly one-call diagnostic did not exercise it.

## Accounting and interpretation limits

Read-only SQLite inspection confirmed one audit and one consumed call at model
3.6 Flash / 4096 tokens. A network-disabled repeat check rejected the existing
reservation before credential input or another call. Prior ledgers were not
reused, decremented or relabelled.

The supplied key can access the model and obtain a generation response in this
test. The key, token allowance and time differ from earlier failed observations;
this does not prove the old key was invalid or that high demand is permanently
resolved. One diagnostic case does not qualify the fixed 30-case corpus.

Next diagnostic should retain safe validation paths/types without raw values and
exercise the ordinary one-initial/at-most-one-schema-repair path under a bounded
authorization. Never loosen the strict schema to obtain a pass or automatically
repeat this reserved revision.

[Evidence](../evidence/B13-provided-key-diagnostic.json) contains safe observations,
settings, hashes, privacy and accounting checks. Local artifacts:
`C:/Users/jiang/mirum-evidence/B13/new-key-20261113/`.
B13/B14 remain blocked; no E task, full qualification run, merge or deployment began.
