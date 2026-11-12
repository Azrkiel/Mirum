# Release acceptance and evidence matrix

The full AI-assisted V1 requires B01–B14 plus every V01–V15 check. Tests already present in the repository are starting points, not fresh proof. B01 has run the existing native/API path; the full V1 release gates remain open.

| ID | Required behavior | Primary task owners | State | Evidence |
|---|---|---|---|---|
| V01 | Form and AI-assisted input produce the same fixed-requirement and brief digests and the same candidate selection when their normalized requirements, choices, and versions match. Their provenance records may differ. | B03, B05, B11, B13 | not_run | Not produced |
| V02 | The 200 x 10 mm reference example rejects 5 and 10 mm thickness and accepts 15 mm within the existing numerical tolerances. | B01, B04, B07, B13, B14 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V03 | A too-low allowable or too-tight deflection limit rejects every candidate with measured reasons and no accepted STEP/preview. | B01, B07, B08, B13 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V04 | Missing load, ambiguous units, kg described as force, contradictory requirements, and unknown material source require clarification. | B03, B05, B06, B11, B12, B13 | not_run | Not produced |
| V05 | A request for holes, a second load, a different support, or a non-slender section returns unsupported without silently dropping the feature. | B02, B06, B13 | not_run | Not produced |
| V06 | Native checks reject valid-but-wrong CAD: offset/rotated prism, wrong size, missing material, extra solid, and changed root section. | B01, B04, B13 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V07 | Missing solver, nonfinite solver output, malformed worker report, timeout, or failed required check cannot produce acceptance. | B01, B02, B04, B07, B08, B13 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V08 | Changing a finalized brief, candidate, report, STEP, or preview breaks evidence association and prevents acceptance or delivery. | B03, B04, B08, B10, B11, B13 | not_run | Not produced |
| V09 | Editing input invalidates the old review; concurrent browser tabs and duplicate clicks cannot launch a different unreviewed request. | B03, B05, B09, B11, B12, B13 | not_run | Not produced |
| V10 | Cancel, restart, deadline, overload, and duplicate-idempotency cases leave no orphan native processes or partial accepted bundle. | B09, B10, B11, B13, B14 | not_run | Not produced |
| V11 | Failed file write, capacity exhaustion, expired files, and archive download errors are visible and cannot return a misleading accepted package. Unexpired accepted bundles survive unrelated legacy sweeps. | B10, B11, B13, B14 | not_run | Not produced |
| V12 | A replay uses the saved reviewed brief and pinned implementation without calling the AI. Verdicts and measured properties agree within declared tolerances. Raw STEP/ZIP bytes may contain timestamps or metadata; do not promise identical file hashes across independent regenerations unless explicitly established. Digests always identify the exact delivered revision. | B10, B13, B14 | not_run | Not produced |
| V13 | Existing engineering and general CAD routes retain their documented behavior and cannot accidentally acquire broader physics claims. | B02, B04, B07, B08, B09, B11, B13, B14 | not_run | Not produced |
| V14 | Browser checks cover the accepted preview, report, all downloads, every failure outcome, and page refresh during a job. | B12, B13, B14 | not_run | Not produced |
| V15 | AI evaluation uses a fixed 30-case set: 10 complete supported requests, 10 incomplete/ambiguous requests, and 10 unsupported requests. Require zero silent requirement drops or unreviewed execution, and at least 8 of the 10 complete cases to yield an accurate reviewable draft. Remaining supported failures must be explicit and recoverable through the form. This is a defined benchmark, not proof that arbitrary natural-language requests will always be interpreted correctly. | B06, B13, B14 | not_run | Not produced |

## Evidence rules

For each row record the exercised implementation commit, commands, environment/runtime versions, observed result and limitations. Distinguish native CAD, numerical checks, provider fixtures, live-provider requests and browser/physical observations. Re-run affected checks after relevant changes.

For V15 use the fixed 30-case corpus: 10 complete supported, 10 incomplete/ambiguous and 10 unsupported requests. Require zero silent requirement drops on this corpus, no unreviewed execution and at least 8/10 complete requests producing accurate reviewable drafts. Live access and a bounded provider budget must exist; offline fixtures alone do not satisfy V15. Missing live evidence leaves full AI release qualification blocked.

For V12 raw byte equality is not the numerical reproducibility criterion. Compare verdicts and measured quantities with the pinned model's explicit tolerances while keeping each delivered file's exact fingerprint.

## Physical qualification

No V01–V15 row establishes manufactured behavior or standards conformity. E04 supplies a separate predeclared protocol and measured evidence for those claims. Simulated and physical observations must be distinguishable.
