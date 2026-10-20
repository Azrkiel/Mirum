# Mirum — structural B-rep acceptance implementation handoff

**Status: implemented and native-tested in isolation; final independent API/UI review pending. Not production-approved, merged or deployed.**

## 1. Location and baseline

- Existing user checkout: `C:/Users/jiang/Mirum`, branch `integration/text2cad-hardening`. Preserved unchanged.
- Implementation checkout: `C:/Users/jiang/Mirum-brep-phase1`, branch `engineering/brep-acceptance`.
- Both committed baselines remain `4501ff067e5e89556210611c910be0463e222d7b`. This task made no commits or pushes.
- The implementation checkout contains an exact overlay of the **191 inherited working-tree files** from the existing local hardening, plus this slice's additions/changes. It is not a clean GitHub-main-only implementation.
- `.hermes/inherited-baseline.json` records inherited hashes; `.hermes/frozen-source.json` binds **129 runtime/config/test files**; `.hermes/incremental-files.json` identifies **13 source/test files** changed by this slice relative to that inherited working source.
- A branch name alone does not contain uncommitted edits. Preserve this checkout or the full source bundle. Do not reset/clean the original checkout, and do not blindly apply the incremental patch to a fresh GitHub main clone that lacks the inherited hardening.

## 2. What this slice implements

### Native solid-set inspector

`backend/verification/solid_set.py` is separate from the legacy six-check certificate. It inspects the actual imported STEP and:

- Requires native OCP, successful complete STEP root transfer and non-null geometry.
- Accounts for the full topology tree; rejects loose faces/shells/wires/edges/vertices and empty body-level wrappers.
- Preserves legitimate multiple bodies in compounds/compsolids rather than requiring one solid.
- Requires closed shells, geometric BRepCheck validity, finite strictly positive per-solid volume/area and finite tessellation-independent optimal bounds.
- Audits actual imported face/edge/vertex tolerances against a **0.001-mm application numeric ceiling**.
- Checks potentially intersecting final-position body pairs using native Boolean common; rejects measured positive-volume interpenetration while permitting face contact and separated bodies.
- Bounds inspection to **32 solid occurrences**, **100000 traversed topology occurrences** and **500 MiB STEP input**.

These are declared structural/numerical application policies, not manufacturing tolerances or a mathematical proof against every pathological B-rep. STEP translation may normalize/heal the imported representation. Native Boolean zero is not proof of exact disjointness.

### One shared acceptance service and bounded worker

`backend/verification/acceptance.py` captures a source-produced native result to STEP through the existing AST-gated compiler. The acceptance capture executes the generated source once; ordinary critic/model retries before that capture remain separate pipeline work.

`backend/verification/acceptance_worker.py` inspects and reimports that STEP, then derives the delivered GLB from it—not from an unchecked original GLB or a separate certificate-only execution. It preserves XCAF assembly labels/hierarchy when present, otherwise imports every plain-STEP root. It checks imported body count and volume, basic native GLB integrity, and STEP/GLB hashes.

Native work uses the inherited ProcessRunner: **30-second deadline, 1536-MiB address-space cap, bounded output and owned cancellation/cleanup**. The worker receives a minimal environment without provider keys. It is lifecycle/resource containment, not an OS sandbox for hostile code or files.

Every accepted response includes `scope: structural_solid_set_v1`, measured evidence, source/STEP/GLB SHA-256 bindings, native versions, and `engineering_certified: false`. Hashes bind bytes; they are not digital signatures or certification authority.

### Targeted API paths

- `/generate`: accepts the final positioned native result, not merely an aggregate of isolated-part checks. Existing strict part-certificate coverage remains required for multipart runs. The returned source digest matches the portable source returned to the editor.
- `/run`: rejects execution-only success or loose topology and returns current structural evidence.
- `/refine`: applies the same final-assembly gate and returns fresh evidence instead of reusing or discarding a previous revision's certificate.
- `/export/parasolid`: validates the source STEP and supports an unchanged, hash-bound STEP fallback. The old same-file fallback-copy bug is fixed. **Converted Parasolid bytes are refused until output revalidation is implemented**; this is not qualified Parasolid support.
- Optional aero extraction receives the accepted final STEP, not another source execution. Aero/structural physics correctness and fallback provenance are not certified by this slice.

### UI evidence

The card is labelled **Structural Geometry Checks**, with an explicit nonengineering/GD&T/manufacturing/physical-safety disclaimer. Manual/refinement updates adopt only current scope-qualified, hash-matching evidence; previous analysis/context is cleared. Scoped generation evidence must match the new GLB before publication. Failed updates preserve the previous accepted view. Models with no attached evidence are explicitly labelled unverified previews.

## 3. Actual verification

### Full rebuilt-image native gate

Local immutable verification image:

```text
sha256:ed497cfd86bc314047178f74fec3f581b283a594c0ceb4d3c51c04318577cda2
```

Built from this isolated source with `Dockerfile.verify`. Actual result in `.hermes/acceptance-full-native-r1.log`:

```text
584 passed, 1 deselected, 274 warnings in 315.07s (0:05:15)
exit code 0
```

The deselected case is the explicitly marked live-provider test. Tests ran with network disabled, a read-only root, nonroot image user, dropped capabilities, no-new-privileges, 4-GiB memory/2 CPUs and tmpfs scratch. This verifies offline/native behavior, **not live language-to-CAD quality**.

Warnings remain, including inherited Google SDK/Python support, Pydantic, CadQuery/pyparsing and PDF deprecations. A successful test run is not warning-free production qualification. The test Dockerfile remains distinct from the production Dockerfile.

Other results overlap and must not be added to 584:

- Full frontend selection: **48 passed**.
- Initial real shared-helper/four-route selection: **5 passed** before later coverage additions.
- Candidate integration: **55 passed / 2 failed**. Both failures were obsolete contract tests: failure injection targeted the old in-process certificate instead of the new worker boundary; refinement expected no certificate instead of a fresh one. Tests were migrated with stronger rejection/current-evidence assertions, and the final full gate passed.
- Inspector-focused worker reported **37 native tests passed**, also exercised by the parent integration/full gate.
- Real RED logs show missing shared service, loose-face delivery false acceptance, incorrect source digest binding and stale/unbound UI evidence before their fixes.

### Actual application runtime smoke

`artifacts/acceptance-phase1/` contains outputs created through real FastAPI TestClient handlers and native workers in the immutable image. Model seams returned a deterministic four-hole plate fixture; no CAD-generation provider call occurred. Native geometry, STEP import/export, GLB rendering, HTTP/SSE handling and hash checks were real.

- `/generate`, `/run`, `/refine` returned structurally accepted GLBs.
- STEP fallback export returned actual STEP bytes whose SHA-256 matched its response.
- A solid-plus-loose-face `/run` request returned an error with no artifact.
- Accepted source/GLB/STEP hashes were independently checked on the host.
- The nominal plate's measured solid count was 1 and volume was **23321.41598682459 mm³**. This inspector does not independently prove the requested hole dimensions or manufacturing specification.

Additional **ad-hoc** native coverage passed hollow-sphere delivery and delivery of two truly separate STEP roots, with 2 imported solids and measured volume **1999.9999999999995 mm³**. The first ad-hoc harness used an inappropriate exact floating-point volume assertion; its corrected bounded comparison passed. This was a harness issue, not a production source change. Permanent regression coverage for these cases and helper-level lifecycle fault injection remains a recommendation.

## 4. Review status — do not omit this

- Independent candidate review of the three new native acceptance modules reported **no concrete blocking security/logic issue**. It reviewed inherited lifecycle/compiler context and performed real curved-solid/multibody probes. Its suggestions concern permanent curved/cavity/multiple-root and helper lifecycle coverage.
- This was **limited candidate review**, not approval of the complete API/UI slice.
- The separate official Claude Code read-only review ended with **`Error: Reached max turns (12)`**, exit 1. No approval verdict was obtained.
- Preparation of a complete-source, no-tools retry was blocked because the app approval timed out. No code executed in that blocked preparation. The user explicitly chose to hand off the tested implementation with final review pending rather than authorize retry.
- **Final independent API/UI review is therefore pending. Do not call this release approved, production-ready or qualified for unattended engineering use.**

The full-suite result remains real and valid for its frozen source; it does not replace the missing review verdict. Coding/review CLI activity is separate from the network-disabled CAD-generation tests.

## 5. Remaining scope and next steps

This is the **structural acceptance slice of Phase 1**, not completion of the full B-rep engineering roadmap.

1. Complete independent review of the incremental source/API/UI changes when authorized; fix concrete blockers with native RED/GREEN tests and rebuild/reverify changed snapshots.
2. Add permanent curved/cavity/true-multiple-root and helper-level cancellation/timeout/memory/hash-mismatch/render-failure regressions as appropriate. Inherited ProcessRunner coverage does not by itself prove every new helper boundary.
3. Integrate into the original checkout only after explicit comparison/review and user authorization. No automated merge/copyback occurred.
4. Finish remaining Phase-1 provenance work: accepted-only retrieval/training promotion, legacy synthetic/unavailable evidence paths, persistent immutable artifact/revision storage and any remaining unverified export/PMI paths.
5. Phase 2: approved typed feature/requirement IR and independently measured design-intent dimensions/tolerances, starting with plates/brackets/shafts/flanges.
6. Later: correct joint semantics, fits/clearance stack-ups, actual semantic PMI/GD&T with selected standards/editions, process DFM and validated geometry-bound physics.

Known limits: trusted-local single-backend Linux/OCP deployment; no native Windows qualification, public authentication/multitenancy, global distributed admission, hostile-file OS isolation, general mechanism dynamics, exact near-kernel interference proof, independent-kernel validation, standards compliance, manufacturing capability, strength/fatigue/buckling/aero certification or regulatory sign-off. Legacy PMI/physics/training claims are not repaired by attaching the new structural certificate. The app can reject more invalid models without yet generating more semantically correct designs; no live success-rate improvement is claimed.

## 6. Reproduction

From the preserved implementation checkout:

```sh
git status --short --branch
git diff --check
docker build -f Dockerfile.verify -t mirum-brep-acceptance-verify .
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --init \
  --cap-drop ALL --security-opt no-new-privileges --memory 4g --cpus 2 \
  --tmpfs /tmp:rw,size=1g,mode=1777 \
  -e GEMINI_API_KEY_HEAVY=offline-fixture-never-sent \
  -e GEMINI_API_KEY= -e GOOGLE_API_KEY= \
  mirum-brep-acceptance-verify python -m pytest -q \
  -p no:cacheprovider --tb=short -r s
```

`MSYS_NO_PATHCONV=1` is for Windows Git Bash Docker path handling. The sentinel is a dummy offline configuration value, not a credential. Rebuild/package downloads can require network; the actual tests run without provider networking. Do not substitute unchanged GitHub main for the inherited hardened source.

## 7. Evidence to preserve

- `.hermes/inherited-baseline.json`, `frozen-source.json`, `incremental-files.json`, `static-security.json`.
- `.hermes/acceptance-full-native-r1.log`, `acceptance-runtime-smoke.log`, frontend and focused RED/GREEN logs.
- `.hermes/claude-review-r1.txt` (failed review), independent candidate review, original task plan and inspector policy document.
- `artifacts/acceptance-phase1/`: `generate.glb`, `run.glb`, `refine.glb`, `mounting-plate.step`, evidence JSON, accepted source and smoke summary.

The handoff bundle includes a full source snapshot plus an incremental patch and hash manifest. It contains no `.git` directory, `.env`, model credentials, virtualenvs or private telemetry. Its source is uncommitted working-tree content. Archive integrity does not constitute review approval.
