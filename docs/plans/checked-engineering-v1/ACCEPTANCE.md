# Release acceptance and evidence matrix

The full AI-assisted V1 requires B01–B14 plus every V01–V15 check. Tests already present in the repository are starting points, not fresh proof. B01 has run the existing native/API path; the full V1 release gates remain open.

| ID | Required behavior | Primary task owners | State | Evidence |
|---|---|---|---|---|
| V01 | Form and AI-assisted input produce the same fixed-requirement and brief digests and the same candidate selection when their normalized requirements, choices, and versions match. Their provenance records may differ. | B03, B05, B11, B13 | schema_verified | [B03](handoffs/B03.md): normalized form/AI provenance identity; live/end-to-end parity pending |
| V02 | The 200 x 10 mm reference example rejects 5 and 10 mm thickness and accepts 15 mm within the existing numerical tolerances. | B01, B04, B07, B13, B14 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V03 | A too-low allowable or too-tight deflection limit rejects every candidate with measured reasons and no accepted STEP/preview. | B01, B07, B08, B13 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V04 | Missing load, ambiguous units, kg described as force, contradictory requirements, and unknown material source require clarification. | B03, B05, B06, B11, B12, B13 | schema_verified | [B03](handoffs/B03.md): strict quantities, missing inputs, explicit conversion/clarification; preparation/UI pending |
| V05 | A request for holes, a second load, a different support, or a non-slender section returns unsupported without silently dropping the feature. | B02, B06, B13 | contract_verified | [B02](handoffs/B02.md): descriptor/preparation exclusions; AI/API path pending |
| V06 | Native checks reject valid-but-wrong CAD: offset/rotated prism, wrong size, missing material, extra solid, and changed root section. | B01, B04, B13 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope only; full V1 check pending |
| V07 | Missing solver, nonfinite solver output, malformed worker report, timeout, or failed required check cannot produce acceptance. | B01, B02, B04, B07, B08, B13 | baseline_verified | [B01](handoffs/B01.md); legacy/native scope; [B02](handoffs/B02.md) mandates all checks; full V1 pending |
| V08 | Changing a finalized brief, candidate, report, STEP, or preview breaks evidence association and prevents acceptance or delivery. | B03, B04, B08, B10, B11, B13 | schema_verified | [B03](handoffs/B03.md): brief/candidate/check/bundle identity mutations; real byte publication/delivery pending |
| V09 | Editing input invalidates the old review; concurrent browser tabs and duplicate clicks cannot launch a different unreviewed request. | B03, B05, B09, B11, B12, B13 | schema_verified | [B03](handoffs/B03.md): exact revision-bound review; storage/browser concurrency pending |
| V10 | Cancel, restart, deadline, overload, and duplicate-idempotency cases leave no orphan native processes or partial accepted bundle. | B09, B10, B11, B13, B14 | not_run | Not produced |
| V11 | Failed file write, capacity exhaustion, expired files, and archive download errors are visible and cannot return a misleading accepted package. Unexpired accepted bundles survive unrelated legacy sweeps. | B10, B11, B13, B14 | not_run | Not produced |
| V12 | A replay uses the saved reviewed brief and pinned implementation without calling the AI. Verdicts and measured properties agree within declared tolerances. Raw STEP/ZIP bytes may contain timestamps or metadata; do not promise identical file hashes across independent regenerations unless explicitly established. Digests always identify the exact delivered revision. | B10, B13, B14 | not_run | Not produced |
| V13 | Existing engineering and general CAD routes retain their documented behavior and cannot accidentally acquire broader physics claims. | B02, B04, B07, B08, B09, B11, B13, B14 | compatibility_verified | [B02](handoffs/B02.md): CEM signatures and legacy order; later integration pending |
| V14 | Browser checks cover the accepted preview, report, all downloads, every failure outcome, and page refresh during a job. | B12, B13, B14 | not_run | Not produced |
| V15 | AI evaluation uses a fixed 30-case set: 10 complete supported requests, 10 incomplete/ambiguous requests, and 10 unsupported requests. Require zero silent requirement drops or unreviewed execution, and at least 8 of the 10 complete cases to yield an accurate reviewable draft. Remaining supported failures must be explicit and recoverable through the form. This is a defined benchmark, not proof that arbitrary natural-language requests will always be interpreted correctly. | B06, B13, B14 | not_run | Not produced |

## Evidence rules

B06 scoped update: [handoff](handoffs/B06.md) and [manifest](evidence/B06.json)
record 307 passing final engineering/CEM tests. Fixture-produced AI and form
briefs share semantic identities and the finalized AI brief runs real native
search without further provider calls (V01). Ambiguous/contradictory/partial and
unsupported proposal fixtures block review (V04/V05); stale/duplicate requests,
deadline/cancellation and finalization races are covered (V09). The fixed 30-case
corpus and oracle fixtures are implemented. **V15 remains not_run**: no live
provider request or extraction-accuracy qualification occurred. Full V1 release
still requires B09–B14 and all open API/browser/publication/live-provider gates.

B08 scoped update: [handoff](handoffs/B08.md) and [manifest](evidence/B08.json)
record 244 passing tests, including 32 evidence-policy cases. Native measured
rejections remain distinct from unavailable evidence (V03/V07); stale or mutated
report/STEP/preview/source and unsupported claims fail closed (V08), with legacy
outputs unchanged (V13). The separate real probe selected 15 mm, reported
0.677248677 mm / 26.666666667 MPa, and retained exact artifact hashes plus a
reproduction script in the manifest. Candidate acceptance is not a committed V1
run or qualified publication; B09/B10/B13 gates remain open.

B07 scoped update: [handoff](handoffs/B07.md) and [manifest](evidence/B07.json)
record 197 passing tests. Real reviewed search rejects 5/10 mm and selects 15 mm
(V02), rejects all reviewed choices with a low allowable (V03), stops on required
evidence failure (V07), and preserves legacy service/API order and outputs (V13).
Cancellation/deadline controls use labelled seams and retain no accepted bytes;
durable lifecycle, publication, API/browser integration and full release gates
remain open.

B05 scoped update: [handoff](handoffs/B05.md) and [manifest](evidence/B05.json)
record 166 passing offline library tests. Typed service preparation/finalization
preserves normalized semantic identity (V01), exposes field-specific unresolved
or unsupported inputs (V04), and rejects stale reviews/concurrent edits (V09).
This is the memory-repository/service seam; durable storage, live AI parity and
browser confirmation remain open.

B04 scoped update: [handoff](handoffs/B04.md) and [manifest](evidence/B04.json)
record 178 passing tests at the exact implementation SHA. The real new adapter
agrees with the legacy path on reference verdicts/measurements (V02); native
wrong-B-rep/changed-root cases reject (V06), mandatory-evidence worker seams stop
(V07), and captured source/STEP/GLB association is checked (V08). Legacy suites
and caller ordering remain compatible (V13). These rows retain their open full
V1 release scope; no API, bundle publication or browser qualification is implied.

For each row record the exercised implementation commit, commands, environment/runtime versions, observed result and limitations. Distinguish native CAD, numerical checks, provider fixtures, live-provider requests and browser/physical observations. Re-run affected checks after relevant changes.

For V15 use the fixed 30-case corpus: 10 complete supported, 10 incomplete/ambiguous and 10 unsupported requests. Require zero silent requirement drops on this corpus, no unreviewed execution and at least 8/10 complete requests producing accurate reviewable drafts. Live access and a bounded provider budget must exist; offline fixtures alone do not satisfy V15. Missing live evidence leaves full AI release qualification blocked.

For V12 raw byte equality is not the numerical reproducibility criterion. Compare verdicts and measured quantities with the pinned model's explicit tolerances while keeping each delivered file's exact fingerprint.

## Physical qualification

No V01–V15 row establishes manufactured behavior or standards conformity. E04 supplies a separate predeclared protocol and measured evidence for those claims. Simulated and physical observations must be distinguishable.

B09 scoped update: [handoff](handoffs/B09.md) and [manifest](evidence/B09.json)
record 322 passed, 18 warnings in 475.63s. Real SQLite CAS/terminal races, durable proposal budgets,
worker cancel/deadline/shutdown/crash recovery and container replacement passed
(V09/V10). Real legacy HTTP/native reference and rejected behavior passed (V13).
No publication/browser/live-provider qualification is implied; full V1 gates stay open.

B10 scoped update: [handoff](handoffs/B10.md) and [manifest](evidence/B10.json)
record 16 passed in 64.98s at the final implementation plus 282 broader regressions.
Real native publication, exact captured bytes, tamper/write/capacity failures,
lease/expiry, seven-day records and legacy cleanup boundaries passed (V08/V10/V11).
ZIP includes replay inputs and pinned native reports; independent numerical replay
and public HTTP/browser/live-provider qualification remain for B11-B14.
