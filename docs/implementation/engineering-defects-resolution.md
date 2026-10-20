# Resolution: five remaining Mirum engineering defects

Branch `fix/engineering-defects`, based on `origin/main` `511d1ce` (PR #6, which already
contains the verified-physics candidate described in
`remaining-engineering-defects-handoff.md`). The handoff's dirty candidate in
`C:/Users/jiang/Mirum-physics` was compared file-by-file against `origin/main`
(CR-normalised SHA): every candidate file is identical on main; only the handoff document
itself was not yet committed. It is added here unchanged.

Status per item: **fixed**, **narrowed** (supported scope made explicit, the rest fails
closed), or **open**. Nothing here is a standards, manufacturing-fit or physical-safety
certification.

| # | Defect | Status |
|---|---|---|
| A | Bracket dimensions | Fixed |
| B | Assembly manifest integrity | Fixed (documented placement/anchor policy) |
| C | Joint freedom and clearance | Narrowed: 5 joint types supported with verified relations; SLIDER and coupling types fail closed |
| D | Fastener representation and interference | Narrowed: preview vs envelope modes labelled; thread fit always fails closed; no physical thread mode |
| E | Legacy structural false passes | Fixed for the analytic helpers; compression/buckling still not modelled (stated) |

## A. Bracket dimensions — fixed

`backend/cad_utils.py::make_bracket`

* The wall now stands on the base-top datum: wall centre `z = base_thickness/2 + wall_height/2`.
  The base keeps its centred frame. `wall_height` keeps its documented meaning; it was not
  redefined.
* Found while testing: the default 2-hole mode put the first hole at
  `-base_length/2 + hole_diameter`, which cut into the wall (default bracket lost 124 mm³ of
  wall), and the holes were cut from a `faces(">Z")` workplane, which is the *wall top*.
  Holes are now explicit cylinders through the base only, spaced over the free flange
  `[-L/2 + wall_thickness, L/2]`. Hole positions therefore move; this is an intentional
  compatibility change.
* Inputs are validated (finite, positive, not bool; wall thinner than base length; integer
  hole count; holes fit in the free flange) before the kernel runs, and the result must be one
  solid.
* Callers: no code path calls `make_bracket`; it is offered to the Machinist through
  `AVAILABLE_TEMPLATES`. No caller compensated for the old height.

Tests: `backend/tests/test_bracket_dimensions.py` — the recorded 40/60/5/30/4 case and 5
parameterised dimension sets (including the default hole mode and 1/3-hole modes) measured
from planar-face datums, bounding box, exact volume (proves holes miss the wall), hole
diameter/axis/extent, and STEP reimport; 15 invalid-input cases.

## B. Manifest integrity — fixed

`backend/schemas.py::AssemblyManifest._validate_manifest` and `MatingRule`.

Contract enforced by the real Pydantic model:

* At least one part. Unique raw `part_id`s, and unique names in the namespace the assembly
  actually uses (`sanitize_part_id`, now defined once in `schemas.py` and imported by
  `agents.py`). Collisions such as `bolt-1`/`bolt_1`, truncation at 64 chars, and
  `bolt.1`/`bolt 1` are rejected, never merged or renamed.
* References must exist (unchanged).
* Self-reference: only the legacy translation-only self-placement is allowed (single-part
  `'0, 0, 0'`). A self-referencing rule with a `mate_type` is rejected.
* Anchors: a `mate_type` joint needs a planar face selector `[<>][XYZ]` (optionally indexed,
  `>Z[-2]`); named anchors do not survive STEP export, so they cannot carry a joint. Any
  other anchor must be declared in the part's `anchor_tags`. An undeclared native selector is
  still accepted (it is resolved against geometry), so existing selector-based planner output
  keeps working.
* Placement/connectivity (multi-part only): `parts[0]` is the grounded base. Every part must
  be placed either through mate joints from exactly one root (the base or an explicitly
  translated part) or by its own translation. Disconnected mate components, unplaced parts,
  and parts placed by both a joint and a translation (or by two different translations) are
  rejected. Single-part/no-rule manifests and translation-only multi-part manifests remain
  valid.
* A rule may not set both `mate_type` and `translation`.
* `run_assembly_critic_loop` re-validates the manifest from its dump, so a manifest built with
  `model_construct`/`model_copy` or mutated after validation cannot reach execution.

Tests: `backend/tests/test_manifest_integrity.py` (real Pydantic models only).

## C. Joint semantics, freedom and clearance — narrowed

Verified on the installed CadQuery 2.6.1 solver before changing anything: with the old map,
`FASTENED -> Plane` kept an initial 30° spin (spin is free) and `REVOLUTE -> Axis` kept an
initial (7, -4, 10) offset (all translations free); `clearance` was never emitted, and the
generated scripts for clearance 0 and 7 were byte-identical.

New design (`backend/joint_contract.py`, `cad_utils.assemble_mated_parts`,
`verification/joints.py`, `verification/joint_worker.py`):

* **Frames.** Each anchor is the single planar face its selector picks in the part's own
  modelled frame; frame = area centroid + outward normal. Zero, several or non-planar matches
  are rejected as indeterminate.
* **Placement.** Closed form, no iterative solver: the target is rotated by the minimal
  rotation that opposes the faces (half-turn about a documented deterministic axis when the
  normals are equal) and translated so the faces are coaxial with an axial gap equal to
  `clearance` along the source normal. Joint components are placed breadth-first from their
  single root. Loop-closing joints must already hold (redundant) or the assembly is rejected
  as overconstrained.
* **Clearance** is now a typed relation: axial face gap in mm along the source anchor normal,
  finite, `0 <= c <= 1000`. It is never a radial/diametral fit. It must be 0 for BALL and for
  translation-only rules (rejected, not ignored).
* **Supported joints and intended freedom** (residual set → constrained DOF):
  FASTENED 6 (none free), REVOLUTE 5 (rotation about axis), CYLINDRICAL 4 (rotation + axial
  translation), PLANAR 3 (in-plane translations + rotation about normal), BALL 3 (rotations).
  **SLIDER fails closed** (two planar faces do not define a slide direction); GEAR, SCREW,
  RACK_PINION and CAM fail closed as before.
* **Independent verification on the delivered artifact.** After STEP acceptance, a bounded
  worker reloads the accepted STEP bytes, resolves each anchor on the stored part, moves it by
  the stored location and measures it natively: opposition error, radial offset, axial gap vs
  declared clearance, FASTENED spin, and a numerical residual Jacobian (scale-aware rank
  threshold) whose rank/null space must equal the declared freedom. Pairwise final-pose
  interference is re-checked. The report is bound to the manifest SHA-256 and the STEP
  SHA-256 and attached as `geometry_certificate.assembly_joints`. Failure blocks delivery in
  `/generate` and `/refine`.
* **Kinematic intent** is recorded in that report; STEP/GLB carry one static pose and do not
  retain joint definitions. Mobility for closed loops is reported as not computed.
* **What the freedom evidence is, and is not.** The residual-rank check confirms the
  implemented constraint set at the delivered frames constrains exactly the complement of the
  declared free motions (`freedom.basis` says so). Geometry-level evidence is the measured
  pose residuals plus the controlled-perturbation tests. It is not a swept-motion analysis:
  a REVOLUTE/PLANAR/CYLINDRICAL part may still collide with a neighbour somewhere along its
  free motion; only the delivered pose is interference-checked.
* **Scope limit: joints align anchor-face centroids.** An off-centre position on a face (bolt
  patterns, several parts on one face) cannot be expressed by a planar-face joint and must use
  an explicit translation. This matches the old CadQuery `Plane`/`Point` behaviour (face
  centres), so it is not a regression; the planner prompt now says so.

`solve_assembly_checked` is kept (generated scripts that build their own constrained
`cq.Assembly` still use it) and its IPOPT regression is kept.

Tests: `backend/tests/test_joint_semantics.py` — pure-contract rank and finite-motion tests
for each joint at two frame orientations; end-to-end native fixtures through the real
sandbox, STEP acceptance and joint worker for every supported joint at clearance 0 and 0.7;
clearance 0 → 7 changes the measured gap by 7 mm; three different modelled part frames;
equal-normal half-turn; allowed motions re-verify and rejected motions (tilt, spin, axial,
radial) fail for each joint on the delivered bodies; redundant loop accepted, conflicting
loop rejected; non-planar and ambiguous anchors rejected; overlap rejected by acceptance and
by the worker; SLIDER rejected before execution. Updated:
`test_assembly_failure_ports.py`, `test_solver_convergence_ports.py`,
`test_native_api_hardening_ports.py` (they asserted the old solver emission).

## D. Fastener representation — narrowed

* `make_metric_thread`, `make_hex_bolt`, `make_hex_nut` take `representation`:
  `"preview"` (default, unchanged geometry; labelled preview-only) or `"envelope"` (plain
  nominal-diameter shank / nominal-diameter bore; mating pairs touch without overlap). Any
  other value — including "physical"/"helical" — raises. No qualified helical thread was
  implemented; the conservative option from the handoff was chosen.
* The recorded preview pair interference (82.0227 mm³) is kept as a regression and
  `verification.fasteners.assess_thread_fit` returns `passed: False` for every pair
  (`preview_only` / `envelope_only`).
* Delivered results that use fastener helpers carry
  `geometry_certificate.fastener_representation` (static AST scan of the part sources) with
  `manufacturing_thread_geometry: false`, `thread_fit_evaluated: false`.
* The existing STEP acceptance already rejects any positive-volume interpenetration, so a
  preview bolt + preview nut assembly cannot be delivered; the prompts now instruct
  `representation='envelope'` for mated pairs.
* Not established by any mode: thread engagement, fit class, stripping strength, preload,
  fatigue, certified fastener performance.

Tests: `backend/tests/test_fastener_representation.py`.

## E. Legacy structural false passes — fixed (analytic scope)

`analysis/structures.py`, `backend/main.py::_run_struct_stage`, `backend/subagents/struct.py`,
`StructuralReport`.

* `StructuralInputError` for non-finite loads, non-positive/non-finite section values,
  allowables and load factors, non-integral or `< 1` spar counts, invalid spec/wing values,
  and non-finite/non-positive *computed* height, inertia, web area, stress or margin
  (underflow/overflow). Zero stress is never substituted.
* Signed loads: symmetric two-cap idealisation with one tensile allowable and a
  sign-independent shear allowable, so margins use stress magnitude; `stress_pa` stays
  signed. Negative stress can no longer produce `+inf`. Cap compression/buckling and web
  shear buckling are not evaluated for either sign (`compression_evaluated: false`, warning).
* Estimated loads evaluate both the positive and negative ultimate load cases and report the
  governing one (`load_cases`, `governing_load_case`). Explicit loads are one signed
  `explicit` case.
* A genuine zero load is `status: "zero_load"`, MS = inf (JSON null with
  `bending_status/shear_status = "zero_load"`), distinct from `"not_evaluated"`.
* Both pipeline mapping sites turn `StructuralInputError` into a skipped stage
  (`invalid structural input: ...`), never a pass; `int(n_spars)` truncation was removed.
* The three earlier regressions in `test_structure_acceptance.py` are unchanged and pass.

Recorded probes now: zero cap/web section raises `StructuralInputError`; ±1e12 loads give a
finite negative margin and `passed: False`.

Tests: `analysis/tests/test_structure_validation.py`,
`backend/tests/test_struct_stage_validation.py`.

## Verification

See the "Verification record" section appended at the end of this file.

## Verification record (2026-10-20)

Environment: source overlay on the existing native verify image `mirum-merge-verify:latest`
(the image built for `main` 511d1ce; CadQuery 2.6.1, Python 3.10, Linux). This reuses the
installed runtime; it is not a clean dependency build. Run with `--network none --read-only
--cap-drop ALL --security-opt no-new-privileges --memory 4g --cpus 2`, offline placeholder
key, `python -m pytest -q -p no:cacheprovider --tb=short -r s`.

| Run | Source | Result |
|---|---|---|
| Baseline | unchanged `main` 511d1ce | 660 passed, 1 deselected, exit 0 (681 s) |
| RED | new bracket tests on `main` | 6 failed (wall 17.5/7.5/26.5/40.75 mm vs 30/12/45/80); invalid-input cases hang the old kernel path (`wall_height=inf`) |
| RED | other new test files on `main` | collection errors: `sanitize_part_id`, `joint_contract`, `verification.fasteners`, `StructuralInputError` do not exist |
| Full #1 | first complete patch | 928 passed, 12 failed — all pre-existing fixtures building manifests the new contract rejects (unplaced second part, empty manifest, duplicate ids); fixtures given explicit placements, duplicate case now asserts boundary rejection |
| Full #2 | pre-review snapshot | 940 passed, 1 deselected, exit 0 (1090 s) |
| **Final** | post-review snapshot | **942 passed, 1 deselected, 0 skipped, exit 0 (1094 s)**; image `sha256:18bdd5c0a975…`; 17 changed source/test files byte-identical to the image copies |
| Schema gate (a) | host stdlib | `test_prompts_schema`, `test_runner_filters`, `test_md_report` pass |

The 1 deselected test is the `live` (paid provider) marker, excluded by `pytest.ini`.
Gate (b), the live eval-harness regression slice, was not run (needs paid Gemini calls); none
of these deterministic repairs calls a model.

Recorded handoff probes on the final image:

```
bracket_valid True wall_above_base_mm 30.0                      (was 17.5)
preview_thread_nut_overlap_mm3 82.02268474387722 fit False      (kept as failing-fit regression)
envelope_overlap_mm3 0.0
empty rejected: AssemblyManifest requires at least one part.    (was accepted)
duplicate_ids rejected: Duplicate part_id 'same'                (was accepted)
negative_loads_passed False bending_MS -0.99999995              (was True)
zero_section rejected: box.cap_area_m2 must be > 0, got 0       (was passed True)
```

CadQuery solver evidence that motivated replacing the constraint map (on `main`):
`Plane` kept an initial 30° spin; `Axis` kept an initial (7, -4, 10) offset; the generated
scripts for clearance 0 and 7 were identical.

### Independent review

* Security (zero-context reviewer, checklist `.claude/skills/mirum-security-review`): no
  CRITICAL/HIGH. Fixed: global 2-slot cap on joint-verification workers (MEDIUM); stage
  paths stripped from worker failure text (LOW). Pre-existing, not in this diff: `/refine`
  `original_scripts` has no `max_length`; `cad_aero` is in the sandbox module allowlist.
* Correctness/mechanics (zero-context reviewer): fixed half-turn axis tie-break under
  1e-17 normal noise; `KeyError` on a missing box field is now `StructuralInputError`; added
  chain / reversed-edge / translated-root and noise tests; reworded the freedom evidence
  (contract rank check, not swept-motion analysis) and documented the centroid-alignment
  scope limit. Accepted as stated limits: structural `passed` still means tension-cap and
  average web shear only (compression/buckling not evaluated, flagged by
  `compression_evaluated: false` and a warning — unchanged from before for positive
  moments); stricter manifest policy rejects some previously accepted planner output by
  design.

## Still open

* No qualified helical/physical thread geometry (D): thread fit is always rejected.
* SLIDER and coupling joints (C) fail closed; radial/diametral clearance is not representable
  with planar anchors; free-motion collision sweeps are not analysed.
* Wing structural compression/buckling (E) is not modelled.
* Legacy translation-only assemblies get no joint report (they declare no joints); their
  final overlap is still rejected by the existing STEP acceptance.
