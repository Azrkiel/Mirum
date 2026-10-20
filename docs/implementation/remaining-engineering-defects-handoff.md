# Handoff: five remaining Mirum engineering defects

> **Status 2026-10-20:** addressed on branch `fix/engineering-defects` — see
> [engineering-defects-resolution.md](engineering-defects-resolution.md) for what is fixed,
> narrowed, or still open, with test evidence. The text below is the original handoff.

## 1. Scope, status and checkout

This handoff covers **only** these unresolved areas:

1. Bracket dimensions.
2. Assembly manifest integrity.
3. Joint degrees of freedom and clearance semantics.
4. Fastener representation and interference.
5. Legacy structural-analysis invalid-section and signed-load false passes.

**None of these five areas is fully repaired by the existing candidate.** The findings and completed adjacent work below must not be confused with proposed fixes. This document is a handoff, not an implementation or approval of those fixes. Recommendations are starting points; the receiving engineer/agent should challenge them and choose a justified design.

Authoritative implementation checkout: `C:/Users/jiang/Mirum-physics`

- Branch: `engineering/verified-physics`.
- Current HEAD/base: `de07fdccc577099fbb65cf2469f84abb1d3a0b84`, tracking `origin/engineering/brep-acceptance`, plus **uncommitted candidate changes**.
- Published-main audit baseline: `6db3156c8c96c9aaaaff7f8e6482318b4842a42a`. Some audit line numbers refer to this older revision; navigate by function name in the current tree.
- Preserve the dirty candidate. Do not reset it, assume HEAD contains the candidate, or implement against published main without explicitly porting the prerequisite acceptance work.
- `C:/Users/jiang/Mirum`, `C:/Users/jiang/Mirum-brep-phase1` and `C:/Users/jiang/Mirum-physics` are separate working trees. Do not overwrite the other trees.
- Read `CLAUDE.md`. Schema, endpoint or sandbox changes require the repository's `.claude/skills/mirum-security-review/SKILL.md` checklist and independent review. Do not weaken execution limits or add unsafe imports to the sandbox-allowlisted `cad_utils.py`.
- No commits, pushes or merges were performed in the preceding work. Commit only if the user authorizes it.

## 2. What has already been done

### Investigation and reproduction

- Audited the relevant builder, Pydantic validator, deterministic assembler and analytic structural functions. Compared published-main findings against the stronger existing branch so old fixes are not proposed again.
- Reproduced the bracket and fastener geometry defects with actual CadQuery/OCP in Docker, not a mocked kernel.
- Confirmed that **actual Pydantic construction**, not just an extracted validator, accepts an empty assembly and duplicate part IDs.
- Inspected the current joint-to-constraint mapping and emitted constraint code. A published-baseline probe found byte-identical generated code for clearance 0 and 7 mm. Current emission still does not apply the clearance field. This is not a full kinematic/clearance test of the final assembly.
- Reproduced current standalone legacy structural false passes for zero cap/web section values and very large negative signed root loads.
- Retained the source audits and documented these five areas as unresolved, rather than treating the passing regression suite as proof that they work.

### Adjacent fixes already implemented — preserve them

In `analysis/structures.py::analyze_wing_structure`:

1. Missing span **and** no explicit root moment no longer become a successful zero-stress bending check. `bending['evaluated']` is false and `bending['passed']` is forced false.
2. An infeasible cruise condition, including `CL > CLmax`, now prevents the overall pass.
3. Explicit supplied root loads remain supported without a span.

Regressions: `analysis/tests/test_structure_acceptance.py` contains all three behaviors. Do not undo the compatibility/control case while hardening the numerical API.

The prerequisite branch already normalizes mate enums, rejects unsupported metadata-only mate types, and invokes `solve_assembly_checked`. **Those fixes are not the remaining joint-semantics solution** and should not be redone as if absent.

The candidate also has a separately bounded deterministic cantilever API with independent STEP measurements and beam FEA. It illustrates separation of geometry, requirements and physics acceptance, but it does **not** repair these legacy helpers, assembly contracts or wing-analysis APIs. Do not label the legacy path as FEA because the new path exists.

## 3. Defect A — bracket dimensions

### Verified behavior and source

- Current source: `backend/cad_utils.py::make_bracket`, approximately lines 701–760.
- The docstring defines `wall_height` as height **above the base**.
- Real probe: `make_bracket(40, 60, 5, 30, 4, n_mounting_holes=0)`.
- Result: valid B-rep, but wall height above the base top was **17.5 mm**, not **30 mm**.

The base box is centered at Z=0; its top is `base_thickness / 2`. The upright is also centered, but its workplane is offset by `base_thickness`. The code therefore mistakes a box center for the intended bottom datum.

### Challenges encountered

A kernel-valid union concealed a dimensional contract violation. B-rep validity and a bounding box alone do not identify the intended base-top datum. Fixing placement can also change selectors used for mounting holes or downstream mating; a pretty render is insufficient.

### Proposed solution — not implemented

Keep the existing centered-base frame unless a documented compatibility decision changes it. For a wall whose bottom is on the base top, the proposed wall center is:

```text
wall_center_z = base_thickness / 2 + wall_height / 2
```

This is a proposed construction change, **not a tested patch**. Verify that the chosen union/junction remains one valid solid and that any deliberate overlap or fillet does not alter the exposed requested height. Do not silently redefine `wall_height` to make the old result appear correct.

### Next steps and acceptance criteria

- [ ] Write a failing native regression for the recorded 40/60/5/30/4 case before modifying the builder.
- [ ] Independently measure the base-top datum and wall-top plane; require their separation to equal the requested wall height within a declared numeric budget.
- [ ] Check base dimensions, wall thickness, one-body validity and STEP reimport, not just a successful union.
- [ ] Parameterize unequal base/wall dimensions and test mounting-hole modes, including the current default. Check that hole/face selectors still target the intended geometry.
- [ ] Reject invalid/nonfinite dimensions rather than relying on kernel failures.
- [ ] Review callers that may have compensated for the old height; document intentional compatibility changes.

## 4. Defect B — assembly manifest integrity

### Verified behavior and source

- `backend/schemas.py::AssemblyManifest._validate_manifest`, approximately lines 550–578.
- Current checks impose size maxima and verify referenced source/target IDs exist.
- Actual `AssemblyManifest.model_validate(...)` accepted both an empty `parts` list and duplicate `part_id` values.
- The baseline audit also demonstrated missing connectivity/declared-anchor validation through an extracted validator. Treat those additional cases as requiring real-model reproductions, not as already exercised end-to-end API failures.

### Challenges encountered

A valid Pydantic object is not necessarily a coherent assembly graph. Duplicate IDs can collapse dictionary entries later. However, a blanket rule that every model needs a mate would wrongly reject legitimate single-part models or explicitly placed components. Named anchors and native selector strings may also be different concepts.

### Proposed solution — not implemented

Move basic identity and graph invariants to the typed manifest boundary:

- Require at least one part and unique IDs.
- Check uniqueness in any normalized/sanitized namespace actually used by the generated assembly, not only raw strings; first confirm the sanitizer's behavior.
- Validate references before execution.
- Define anchor resolution explicitly: declared semantic anchors versus supported native selectors. Do not impose a rule that rejects currently valid selector-based models without a migration design.
- Define a supported connectivity/placement policy. Single-part/no-mate is a valid control. Multipart disconnection should require explicit placement/intent or be rejected under the chosen contract, rather than silently accepted as a solved mechanism.

Use structured validation errors. Do not silently drop, rename or merge duplicate parts to repair an invalid manifest.

### Next steps and acceptance criteria

- [ ] Add failing tests using the **real Pydantic models** for empty and duplicate-ID manifests.
- [ ] Add valid one-part/no-mate and valid multipart controls.
- [ ] Cover undefined references, normalization collisions, unsupported/missing anchors and disconnected components under the documented policy.
- [ ] Check source/target self-reference rules explicitly; do not rely on the generator silently skipping invalid rules.
- [ ] Ensure an invalid manifest cannot reach script execution or accepted artifact delivery.
- [ ] Follow the schema security checklist and request fresh independent review.

## 5. Defect C — joint semantics, freedom and clearance

### Verified source-level gap

- `backend/agents.py::MATE_CONSTRAINTS`, approximately lines 102–109.
- `run_assembler_constraint`, particularly current constraint emission around lines 1228–1258.
- Current mapping includes `FASTENED -> Plane`, `SLIDER -> Plane`, and `REVOLUTE/CYLINDRICAL -> Axis`.
- Existing checked convergence is useful, but it does not establish intended relative freedom or fit. A plane relationship is not a fully rigid fasten; axis alignment alone does not impose a revolute joint's axial restraint.
- Clearance is not emitted by the reviewed rule-to-constraint loop. The 0-versus-7 mm identical-source experiment was on published baseline code, not a complete current final-assembly experiment.

### Challenges encountered

Solver success, residual size, uniqueness of pose and allowed mechanism motion are different questions. Global rigid-body motion must be distinguished from intended relative motion. Redundant constraints and singular configurations make naive constraint counting unreliable. A scalar `clearance` field is ambiguous unless its meaning is declared: axial offset, radial gap and diametral clearance are not interchangeable.

### Proposed solution — not implemented

Start with a bounded joint contract using explicit local frames and a grounded reference body. Expected relative freedoms for ideal joints are:

| Joint | Intended relative freedom |
| --- | --- |
| Fastened | None |
| Revolute | Rotation about one axis; no axial translation |
| Slider | Translation along one axis; no relative rotation |
| Cylindrical | Translation along and rotation about one axis |
| Planar | Two in-plane translations and rotation about the plane normal |
| Ball | Three rotations about a common point |

Choose CadQuery constraint combinations only after checking the installed solver's semantics; **do not assume adding one `Axis` and one `Plane` fixes every joint**. Consider initially supporting fewer joint types and failing closed on unsupported ones.

Make clearance a typed geometric relation with units, direction, reference surfaces and allowed range. Validate it on **final transformed bodies**. Retain final collision/overlap checks, but do not equate zero overlap with correct clearance or contact.

For freedom verification, a numerical constraint Jacobian/rank or controlled perturbation test can complement analytic fixture checks. Define scale-aware thresholds and behavior at singular configurations before treating the result as an acceptance gate.

### Next steps and acceptance criteria

- [ ] Create minimal native two-body fixtures with stable axes/planes and an explicitly grounded body.
- [ ] Verify allowed motion and rejected motion for each supported joint, not just a `SOLVER_SUCCESS` marker.
- [ ] Measure axial and radial relations on final transforms; compare multiple initial placements.
- [ ] Prove changing a supported clearance changes the measured relation correctly. Reject an ambiguous/unsupported clearance rather than ignoring it.
- [ ] Test overconstrained, underconstrained and singular cases; report unsupported/indeterminate states explicitly.
- [ ] Reimport the delivered assembly and validate required measurable relations where export preserves them. Record kinematic intent separately if the exchange format does not retain the joint definition.
- [ ] Bind any joint/clearance evidence to the exact manifest and final artifact revision.

## 6. Defect D — fastener approximation and interference

### Verified behavior and source

- `backend/cad_utils.py::make_metric_thread`, approximately lines 399–446: annular-rib approximation, not a helical thread.
- `make_hex_nut`, approximately lines 518–566: plain minor-diameter bore.
- Current centered/coaxial example:

```python
thread = make_metric_thread(10, 1.5, 12).val()
nut = make_hex_nut(10, 1.5, 17, 8).val()
interference = thread.intersect(nut).Volume()
```

Actual measured positive-volume interference: **82.02268474387722 mm³**. This demonstrates an incompatible physical representation for that pose; it is not a thread-strength or full standards-compliance test.

### Challenges encountered

The helper intentionally chose annular ribs for kernel robustness. Those ribs cannot reproduce the screw motion or contact of a helix. A plain minor-diameter bore intersects the rib envelope. Removing the overlap alone would not create a verified thread fit. Existing comments about helical-sweep instability are design history, not proof that every current-kernel helical implementation must fail.

### Proposed solution options — receiving engineer chooses

**Conservative near-term option:** explicitly distinguish representation modes and gate claims.

- Keep the legacy ribbed mode as clearly labelled **preview-only** geometry.
- Provide a separately specified, noninterfering simplified envelope/cosmetic representation if useful for packaging/assembly previews. Never label it a manufacturing thread or evidence of engagement/load transfer.
- Reject manufacturing/thread-fit acceptance when only preview geometry is available.

**Qualified geometric option:** use traceable catalog geometry or implement a bounded, validated helical male/female pair. Require explicit nominal size, pitch, handedness, lead/start count where relevant, engagement length, profile and applicable fit/tolerance specification/edition. Do not guess a standards class from “M10”. Reusing a standard name in a docstring is not verification.

Do not resolve this by merely enlarging the bore, deleting interference checks or allowing arbitrary interpenetration. A genuine modeled thread can have intended contact but should not have unapproved positive-volume overlap; comparisons must respect native numeric uncertainty.

### Next steps and acceptance criteria

- [ ] Preserve the current interference case as a failing engineering-fit regression; retain valid preview-only controls.
- [ ] Inventory callers and representation expectations before changing helper defaults.
- [ ] Choose and document supported modes and failure behavior, including API/export evidence labels.
- [ ] For a physical thread mode, measure profile/lead/pitch and clearances on actual geometry and after STEP reimport.
- [ ] Test engagement poses and the coupled axial/rotational progression for the declared handedness/lead, using independent measurements.
- [ ] Add bounded kernel robustness tests across supported sizes and engagement lengths; keep existing time/memory limits.
- [ ] State explicitly that geometric thread fit does not establish stripping strength, preload, fatigue or certified fastener performance.

## 7. Defect E — legacy structural false passes

### Verified behavior and source

- `analysis/structures.py::bending_margin`, approximately lines 391–422.
- `shear_margin`, approximately lines 425–453.
- `analyze_wing_structure`, approximately lines 460–556.

The current formulas substitute zero stress when a section denominator is nonpositive. They also turn nonpositive signed stress into an infinite positive margin. Actual current probes using the existing `REF_SPEC`, `REF_WING`, `REF_BOX` fixtures returned:

```text
legacy_negative_loads_passed = true
  root_bending_moment_nm = -1e12
  root_shear_force_n = -1e12

legacy_zero_section_passed = true
  cap_area_m2 = 0
  spar_web_thickness_m = 0
```

These are standalone analytic API defects. Ordinary Pydantic stage bounds do not protect direct function callers. The already-implemented missing-load/cruise-feasibility checks do not address these inputs.

### Challenges encountered

A negative load can be a physically legitimate direction, while a negative area cannot. Blindly rejecting all negative forces loses valid load cases; blindly applying `abs()` can conceal unsupported compression/buckling or unequal directional allowables. A genuine explicit zero load must also remain distinguishable from missing evidence and invalid geometry. Infinity in a valid zero-stress margin needs deliberate serialization; converting it to null must not hide an invalid analysis.

### Proposed solution — not implemented

1. Validate both public margin helpers and the high-level entry point: finite loads; positive finite geometric dimensions/section quantities, material allowables and applicable factors; a positive integral spar count. Validate computed denominators/results too, including overflow/underflow cases.
2. Never convert an invalid denominator into zero stress. Raise a structured validation error or return an explicitly failed/unsupported result consistently with callers; it must not become a safe pass.
3. Define signed-load semantics before patching formulas. For an explicitly supported symmetric scalar idealization, use stress magnitude with a documented sign convention and justified allowables. For unsupported load direction/compression cases, return unsupported/not-evaluated with overall pass false until a valid method exists.
4. Evaluate the declared positive/negative load envelope rather than silently ignoring a negative branch. Preserve load-case identity and governing result.
5. Keep these results labelled analytic sizing estimates, not delivered-geometry FEA or aircraft certification. Do not expand the scope of this repair into a new solver without a separate approved design.

### Next steps and acceptance criteria

- [ ] Add failing tests for the two recorded high-level false passes and direct calls to both margin helpers.
- [ ] Parameterize zero/negative section dimensions, zero/nonintegral spar counts, NaN/infinity, invalid allowables/factors and finite inputs whose arithmetic overflows.
- [ ] Add large positive and negative load controls: negative stress must never yield an automatic infinite safety margin.
- [ ] Test genuine explicit zero load with otherwise valid inputs; define its evidence and JSON representation rather than conflating it with missing data.
- [ ] Preserve all three regressions in `analysis/tests/test_structure_acceptance.py`, including explicit loads without span.
- [ ] Trace callers: the live main path invokes `_run_struct_stage`; `backend/subagents/struct.py` also wraps/duplicates this logic. Avoid divergent validation or turning an invalid result into a success when adapting exceptions/serialization.
- [ ] Request review of mechanical assumptions as well as Python logic. No standards or physical safety claim follows merely from eliminating numerical false passes.

## 8. Reproduction entry points

These commands reproduce the recorded probes in the **existing frozen candidate image**. Run from Git Bash on this Windows host. They are read-only with respect to the source checkout and make no provider calls. After editing code, rebuild/overlay and hash-check the new image before using it as evidence of a fix; rerunning the old image cannot verify new source.

### Bracket and fasteners — actual native geometry

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only \
  --memory 2g --tmpfs /tmp:rw,size=256m \
  mirum-verified-physics:candidate python -c '
from cad_utils import make_bracket, make_metric_thread, make_hex_nut
b = make_bracket(40,60,5,30,4,n_mounting_holes=0).val()
t = make_metric_thread(10,1.5,12).val()
n = make_hex_nut(10,1.5,17,8).val()
print("bracket_valid", b.isValid())
print("wall_above_base_mm", b.BoundingBox().zmax - 2.5)
print("thread_nut_overlap_mm3", t.intersect(n).Volume())
'
```

Recorded output: `True`, `17.5`, `82.02268474387722`. The numeric `2.5` is the known base-top datum for this reproduction fixture only; a general verifier must derive the actual datum.

### Real Pydantic validation

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only \
  --memory 2g --tmpfs /tmp:rw,size=256m \
  mirum-verified-physics:candidate python -c '
from schemas import AssemblyManifest
p = {"part_id":"same", "description":"probe", "anchor_tags":[">Z"]}
for name, parts in (("empty", []), ("duplicate_ids", [p,p])):
    model = AssemblyManifest.model_validate({"assembly_name":"probe", "parts":parts, "mating_rules":[]})
    print(name, "accepted", bool(model))
'
```

Recorded: both accepted. These are intended rejection cases for the future repaired contract, not positive test fixtures.

### Legacy structural API

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only \
  --memory 2g --tmpfs /tmp:rw,size=256m \
  mirum-verified-physics:candidate python -c '
from analysis.structures import analyze_wing_structure
from analysis.tests.test_structures import REF_SPEC, REF_WING, REF_BOX
negative = analyze_wing_structure(REF_SPEC, REF_WING, REF_BOX,
    root_bending_moment_nm=-1e12, root_shear_force_n=-1e12)
zero = analyze_wing_structure(REF_SPEC, REF_WING,
    dict(REF_BOX, cap_area_m2=0, spar_web_thickness_m=0))
print("negative_loads_passed", negative["passed"])
print("zero_section_passed", zero["passed"])
'
```

Recorded: both `True`. A repaired implementation must not report these as safe passes; tests should reflect the chosen explicit rejection/unsupported/signed-analysis contract.

## 9. Verification already performed and practical challenges

### Evidence, not proof these five defects are fixed

- Full offline suite in the native Linux/OCP image: **643 passed, 1 deselected, 274 warnings**, exit 0. A fresh full rerun also passed, in **394.54 s**.
- Subsequent focused engineering/structural subset: **82 passed, 18 warnings**, exit 0, **75.25 s**.
- Existing implementation/test snapshot: 15 files matched the exercised image byte-for-byte. This manifest covers the prior candidate, **not future edits to these five areas**.
- Independent final candidate review found no blocking issues inside the separate bounded cantilever scope. It did **not** approve these unresolved legacy features.

### Problems encountered and how they were handled

| Challenge | What happened / solution already used | Implication for the next worker |
| --- | --- | --- |
| Different revisions/worktrees | Published-main audit and stronger local acceptance code differ. Findings were pinned to revisions; current native probes confirmed the retained defects. | Recheck HEAD, dirty diff and current function names before editing. Do not copy baseline line numbers blindly. |
| Native runtime unavailable on host | Host Python lacks pytest and the native CAD stack used for these tests. | Use the existing Linux CAD image or a properly rebuilt equivalent; do not replace native tests with mocks. |
| Clean dependency build blocked | `Dockerfile.verify` pip installation failed certificate-chain validation. TLS was not disabled. | Fix the trusted CA/environment path if needed. The passing source-overlay tests are not a clean release-build claim. |
| Executable fixtures on `noexec` tmpfs | The first baseline run failed on `/tmp/mock_xfoil` despite chmod. An image with normalized executable fixtures at canonical paths passed later. | Keep executable shims out of noexec scratch; retain scratch protections. Do not misdiagnose fixture permissions as a CAD regression. |
| Misleading successful statuses | Valid B-reps, Pydantic objects, solved constraints and passing old tests all missed some intended engineering properties. | Add independent adversarial predicates and demonstrate RED before fixing each defect. |
| Review attempts did not yield verdicts | Some Claude CLI attempts were empty or timed out. A separate isolated reviewer completed; empty output/exit zero was not counted as approval. | Require a real verdict on the final changed snapshot; passing a prior unrelated review is insufficient. |

Useful evidence paths:

- `C:/Users/jiang/Mirum-physics-evidence/audit-mirum-baseline.txt`
- `C:/Users/jiang/Mirum-physics-evidence/audit-physics-baseline.txt`
- `C:/Users/jiang/Mirum-physics-evidence/tested-source-sha256.json`
- `C:/Users/jiang/Mirum-physics-full-overlay.log` — original passing full run.
- `C:/Users/jiang/Mirum-physics-reverification.log` — fresh passing full run.
- `C:/Users/jiang/Mirum-physics-full.log` — retained clean dependency-build failure.
- `C:/Users/jiang/Mirum-physics-runtime.Dockerfile` — source-overlay recipe, not a clean release image.

The prior archive is a snapshot of prior work; creating this handoff does not silently update that archive or turn its test evidence into verification of future patches.

## 10. Suggested execution order and definition of done

The sequence below is a proposal, not a requirement to implement all five in one patch. Bracket/fastener work shares `cad_utils.py`; manifest/joint work shares assembly interfaces. Separate ownership or use isolated branches to prevent conflicting edits.

1. **Freeze and preserve the starting candidate.** Record revision, dirty diff and hashes; read repository rules. Agree the signed-load, connectivity, anchor, joint-clearance and thread-representation contracts before changing them.
2. **Structural numerical false passes and manifest integrity.** Add real failing boundary tests, implement explicit validation/failure semantics, exercise public callers and preserve existing valid controls. These are relatively bounded false-acceptance repairs.
3. **Bracket dimensions.** Add the native failing case, fix the datum construction, and verify hole/selector/caller compatibility and exported STEP.
4. **Joint semantics.** Build one grounded native fixture per supported joint; prove relative freedom and clearance. Narrow supported types rather than inventing a broad success label.
5. **Fastener representations.** Make preview-versus-physical scope explicit, then qualify only a bounded chosen geometry path. Do not claim success merely because an enlarged bore removes overlap.
6. **Fresh integrated verification.** Discover existing related tests before adding files. Run focused regressions first, then the repository's offline native suite. Capture actual runner exit status, exclusions/warnings and current-source/image hashes. No paid model calls are necessary for these deterministic repairs.
7. **Independent final review and handoff update.** Resolve blocking findings with regression tests and re-review changed snapshots. Record each of the five items as fixed, narrowed/unsupported, or still open, with measured evidence. Commit/push only on explicit authorization.

Canonical suite inside the correctly rebuilt native verification environment:

```bash
python -m pytest -q -p no:cacheprovider --tb=short -r s
```

Retain the existing offline/live-test separation from `pytest.ini`. Reuse the established hardened Docker execution settings from `C:/Users/jiang/Mirum-physics-evidence/README.md`; do not run an unchanged frozen image and report it as verification of newly edited files.

### Completion checklist

- [ ] Each retained false-acceptance example has a real regression and a documented repaired result or explicit unsupported result.
- [ ] Valid controls still work: correct brackets, one-part manifests, supported joints/clearances, explicitly labelled fastener representations, valid structural sections and explicit load cases.
- [ ] Geometry/assembly claims use native measurements of the final transformed/exported revision, not echoed input metadata or screenshots.
- [ ] Invalid/missing/unsupported evidence cannot become an overall engineering pass through defaults, sanitization or exception handling.
- [ ] Existing native acceptance and execution/security limits are not weakened to accommodate broken geometry.
- [ ] Focused/full results and independent review apply to the actual final source and artifacts.
- [ ] Remaining scope limits are explicit. No standards-compliance, manufacturing-fit or physical-safety certification is inferred from kernel validity or a green pytest suite.
