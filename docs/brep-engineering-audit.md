# Mirum: B-rep reliability and engineering-validation audit

## Executive conclusion

**Keep CadQuery/OpenCASCADE. Change the acceptance architecture.** Mirum can create genuine B-rep geometry, but currently cannot establish that a delivered model matches its engineering requirements, has correct assembly interfaces, or complies with manufacturing standards. A valid solid is necessary, not sufficient.

This review found actual false acceptance in native CAD tests, plus source-level gaps in assembly, design contracts, analysis provenance, evaluation, training labels and PMI. The isolated demonstrator accompanying this report proves a more defensible pattern for one deliberately narrow part family. It does not fix the live application or certify arbitrary CAD.

## 1. Baselines and scope

- Published repository: `https://github.com/Azrkiel/Mirum`, default branch `main`, HEAD `4501ff067e5e89556210611c910be0463e222d7b`.
- Existing local work: `C:/Users/jiang/Mirum`, branch `integration/text2cad-hardening`, same committed HEAD but substantial modified/untracked hardening. It was inspected read-only and preserved.
- Isolated audit/demonstrator: `C:/Users/jiang/Mirum-brep-audit`, detached Git worktree of published HEAD. New files only; no commit, push or merge.
- Reviewed areas: requirements/schema/planning; Machinist/Critic; compiler and kernel certificate; deterministic mechanical/aero builders; assembly constraints; export/PMI; structural/aero analysis; API/UI publication; eval/retrieval/training; deployment/tests.
- Method: codebase-wide architectural and critical-path review with independent engineering and evaluation audits, targeted source reconciliation, and real offline Linux native-CAD probes. This is **not an exhaustive line-by-line review of every file**, a hostile-code security qualification, or engineering sign-off. One parallel kernel-audit worker hit a provider usage limit; the parent performed the native kernel probes directly.
- Runtime used for native probes: existing `mirum-integration-verify` Docker image, CadQuery 2.6.1 / OCP 7.8.1.1, read-only root, dropped capabilities, no network, bounded memory/CPU and tmpfs. No Gemini or other CAD-generation inference calls were made.

## 2. What the native tests actually demonstrated

`examples/verified_plate/probe_existing_gates.py` exports real STEP fixtures and invokes the actual published/current-local checker. Full results are in `existing-gate-probes.log`.

| Probe | Published checker in OCP runtime | Current local checker | Meaning |
|---|---|---|---|
| Ordinary box | Fails: imports `OCC.Core`, unavailable in OCP-only image | Passes | Published binding mismatch; already repaired locally |
| Solid plus disconnected loose face | Cannot run without binding adapter | **Passes all six checks** | Presence of one solid does not establish a pure solid model |
| Two interpenetrating solids | Cannot run without binding adapter | **Passes all six checks** | Individual B-rep validity is not collision-free assembly validity |
| Box with nozzle specification | Not a direct published runtime claim | **Passes and reports spec-derived nozzle areas** | Metadata can be mistaken for measured design conformity |

For comparison only, the probe also supplies the published checker with an adapter to **real OCP operations**. Its six-check semantics then accept the loose-face and overlapping-solid fixtures too. This is clearly labelled `ocp_adapter_semantics_only`, not a claim that unmodified published main runs successfully in that image.

The current-local box volume was measured as 6000 mm³. The overlapping-solid compound reported 12000 mm³, double-counting the overlapping material as separate bodies. These are actual returned measurements, not fabricated fixture responses.

## 3. Prioritized findings

### P1 — Publication does not uniformly mean acceptance

Published `backend/main.py:801–868` aggregates part certificates and delivers a successful complete event even if certification is missing/failed. Published certificate documentation explicitly describes it as advisory (`backend/verification/geometry.py:8–10`).[1][13]

**Already improved locally:** `/generate` rejects false/unavailable certificates and incomplete part coverage. Do not overwrite that work. **Still open locally:** `/run` and `/refine` have narrower execution/GLB-existence checks; multipart certification remains an aggregate of components, not a check of the final transformed assembly.

**Solution:** one acceptance service used by generation, refinement, edited-source execution, assembly and every export. A failed or unavailable required check must prevent accepted publication. An optional preview may still be shown, but must be labelled **unverified**.

### P1 — Six kernel checks are not a complete solid or design contract

`verification/geometry.py:267–275` asks only whether at least one solid exists. It does not require every delivered body to be a closed valid solid, reject loose faces/wires/edges, enforce expected body count, check collision or measure requested features. Degenerate-face measurement exceptions are skipped (`227–253`). No feature-level tolerance budget is enforced.[1]

The OCCT analyzer checks defined shape/topology/geometric consistency criteria, not the application’s requested feature dimensions, manufacturing process or engineering loads.[4]

**Solution:** explicit allowed shape classes; complete body accounting; per-body closure, orientation and finite positive mass checks; geometric BRepCheck plus appropriate self-interference diagnostics; edge/face/vertex tolerance audits; explicit expected counts and measured feature assertions. A small absolute face-area threshold is not a universal degeneracy definition. Singular/degenerated edges can also be legitimate in some analytic surfaces.

### P1 — Requirements are prose, not executable geometric predicates

`schemas.py:38–104` puts part geometry in free-form description and anchor strings. The requirements structure has no complete typed feature graph, datum/feature identity, toleranced measurements, applicability rules or output-bound load cases. Existing manifest validators do not enforce all documented invariants.[7]

**Solution:** an approved typed design IR with units, feature IDs, feature type, nominal parameters, limits, datum frames, dependencies, material/process intent and provenance. Validate unique IDs, references, connectivity, finite bounded numbers and contradictions before model execution. Unsupported requirements must produce abstention/clarification, not an approximate success.

### P1 — Joint names do not faithfully implement their mechanical semantics

Published `agents.py:1237` uses `str(rule.mate_type)` against bare enum-value keys. This can map a requested joint to fallback `Plane`. Published code catches solve exceptions and then exports (`1252–1258`). Local hardening fixes enum normalization and gates native solver outcome, but the coarse `MATE_CONSTRAINTS` mapping (`102–109`) and ignored clearance remain inadequate.[2]

An axis-direction relation alone does not locate a hinge axis in space. A plane relation is not a complete fastened joint. Existing collision checks examine isolated parts at the origin, rather than the final solved transforms (`1103–1144`, `1279–1287`).[2]

**Solution:** explicit frame-to-frame joint definitions, independent residuals, intended remaining degrees of freedom, offset/clearance enforcement, final-position interference and contact checks. Distinguish static assembly pose verification from kinematic motion, coupling, swept-volume and dynamic verification.

### P1 — Certificate and downloaded geometry are not one bound revision

Single-part generation can reexecute original source for certification separately from parameterized GLB generation. Multipart evidence is combined from individual parts. Neither establishes that the final delivered STEP/GLB is the exact geometry checked.[13]

**Solution:** retain one native result per revision; validate it, export it, reread the exported artifact, compare the actual geometry, then publish a manifest binding specification, source, kernel/build versions, body identities, STEP/GLB hashes, all measurements and acceptance decisions. A checksum binds bytes; it is not a digital signature or trust authority.

### P1 — Aerospace geometry has conflicting intent and units

Planner guidance requests a hollow shell around an inner void, while domain strategy instructions also prohibit hollowing and call a fully solid outer mold line a skin (`agents.py:549–569`; `strategies.py:480–510`). This can make internal ribs/spars occupy already solid material.[2][15]

`cad_aero.make_spar` passes metre-valued numbers directly to CadQuery coordinates (`cad_aero.py:313–349`), whereas the standard pipeline works in millimetres. The recommended assembly examples do not impose one consistent boundary conversion.[9][15]

**Solution:** one canonical CAD unit and frame; explicit SI conversion only at analysis boundaries; native scale tests; one authoritative thin-wall workflow. Measure skin thickness, spar caps/webs, rib clearances and joins from the delivered geometry. Resolve camber, twist and pivot conventions with actual cross-section tests.

### P1 — Physics reports can describe assumptions or a substitute model

Structural analysis uses extracted specification values rather than measured STEP section properties. `analysis/structures.py:410–421` treats zero stress as infinite margin; missing/invalid geometry or load cases must not become a successful physical check. XFOIL extraction can fall back to an analytic requested NACA profile (`analysis/airfoil.py:839–873`) without equivalent provenance in the public report.[10][11]

**Solution:** explicit `passed / failed / not_evaluated / not_applicable` states; require valid loads, material allowables and geometry-derived sections; bind every analysis to a geometry hash. Preserve extraction/fallback provenance. An analytic airfoil study is useful, but must not be labelled analysis of the delivered wing. Preliminary beam/XFOIL calculations are not FEA, fatigue, buckling, aeroelasticity or flight approval.

### P1 — PMI/AP242 output is scaffolding, not standards conformity

`postprocessing.py:350–368` sets document-root name metadata rather than feature-linked dimension, datum and geometric-tolerance entities. Its fallback copies STEP unchanged and writes a JSON sidecar (`405–448`), yet the API can label the output as AP242/PMI.[12][13]

**Solution:** separate unvalidated annotation proposals, drawing metadata, presentation PMI and semantic PMI. Implement and verify actual face/feature-linked XCAF entities and selected STEP schema, then test import in target CAD/inspection software. Do not claim GD&T compliance from a sidecar or filename.

### P1 — Benchmarks measure execution more than CAD acceptance

`eval_harness/runner.py:97–124` invokes the critic with `save_step=False`, discards certificate evidence and marks returned code as a pass. Strict LVM mode does not reject an unavailable numeric score. The semantic score is based on truncated prompt/code, not measured B-rep. These metrics cannot establish engineering-model reliability.[3]

**Solution:** executable feature oracles and retained native artifacts, with separate rates for execution, topology, dimensional intent, assembly and engineering checks. Freeze retrieval and hold out source/part families before augmentation. Include missing-feature, wrong-unit, wrong-hole, collision, stale-evidence and unavailable-analysis adversaries. Missing evidence must fail required gates.

### P1 — Unavailable/synthetic evidence and early training labels

Rendering fallback produces unrelated synthetic stripes (`rendering.py:126–150`); continuity/render failures can return a pass. A fabricated visualization must never become production surface-quality evidence.[14]

`agents.py:844–858,940–964` records execution success and retrieval insertion before complete export/certificate/final acceptance. Local retrieval ranking fixes do not repair that provenance problem.[2]

**Solution:** synthetic fixtures are test-only; unavailable analysis remains unavailable. Quarantine attempts, then promote only final accepted immutable source/artifact/specification pairs into retrieval/training. Keep benchmark data out of that promotion path.

### P2 — Engineering approximations need explicit supported scope

Mechanical helpers include annular-rib thread approximations and plain-bore nuts rather than functioning helical engagement (`cad_utils.py:381–425,500–541`). Gears/nozzles and several builders contain approximations or request clamping. A kernel-valid visualization is not a standards-conforming manufactured part.[8]

**Solution:** label visualization-only helpers; use trusted catalog/vendor parts or validated feature implementations for standards-critical components. Reject unsupported requests rather than silently changing them. Keep research/training pipelines out of production claims until their labels, schema compatibility and held-out evaluation are qualified.

## 4. Recommended architecture

```text
Natural-language request
  -> typed requirements + explicit assumptions
  -> user-approved bounded design IR
  -> trusted feature compiler / catalog parts
  -> native B-rep + stable semantic feature identities
  -> kernel / topology / tolerance-budget checks
  -> independent geometry-vs-requirements measurements
  -> final assembly pose / joint / clearance verification
  -> process-specific DFM and bounded physics checks
  -> STEP export -> reimport -> geometry comparison
  -> immutable artifact/evidence manifest
  -> accepted delivery, or explicit rejection/abstention
```

The LLM selects supported features and proposes bounded repairs. It does **not** decide whether its own geometry passes. Do not start by replacing OCCT with a mesh generator, training another model, or buying another kernel. A second CAD kernel can later strengthen import/interoperability qualification, but cannot supply a missing design specification.

### Start with a restricted useful capability set

1. Plates, blocks, turned shafts, flanges and simple brackets.
2. Analytic holes/through-bores, counterbores, pockets, patterns, chamfers and bounded fillets.
3. Catalog hardware for actual threads, bearings and standardized interfaces.
4. Static assemblies with verified joints/clearances.
5. Only then one explicitly defined thin-wall wing-box family.

Unrestricted arbitrary Python may remain an expert research mode, but should be labelled outside the verified capability envelope.

## 5. Tolerances and standards: what must be separated

| Concept | Required representation | What it does not prove |
|---|---|---|
| OCCT topology tolerance | Actual vertex/edge/face tolerances and a bounded kernel budget | Manufacturing precision |
| Numerical comparison budget | Measurement algorithm, units, error/guard-band policy and failure behavior | A physical inspection uncertainty model |
| Size limits | Explicit upper/lower dimension limits on identified features | Datum-relative form/orientation/location |
| GD&T | Datum systems, tolerance-zone semantics, applicable modifiers and verification procedure | Material/process capability or strength |
| Assembly fit | Interface limits, clearances and tolerance-stack extremes | Motion/dynamics or fatigue life |
| Engineering fitness | Approved loads, boundaries, material condition, allowables and validated analysis | Certification or as-manufactured conformity |

ASME’s own Y14.5 description concerns the rules and interpretation of GD&T on drawings, digital models and related documents. It is not a generic B-rep quality flag.[5]

Choose the intended market, process, standards family and editions first—for example ASME-based dimensioning versus an appropriate ISO GPS scheme, plus applicable fits, fasteners, materials and product-specific requirements. Do not mix defaults or claim compliance with unspecified “engineering standards.” Licensed normative requirements and qualified engineering review are needed before implementing a compliance claim. The ISO page attempted during this audit was blocked by an access challenge; no ISO clause or edition was verified from it.

For a nominal CAD generator, the normal target is the nominal geometry **plus** a correct toleranced product definition. Checking the digital nominal model within 0.01 mm does not establish that a machined part will achieve that tolerance. CNC capability, inspection and metrology remain separate.

## 6. Working demonstrator delivered

`examples/verified_plate/plate.py` constructs an 80 × 50 × 6 mm sharp rectangular plate with four Ø6 mm Z-through-bores on a 60 × 30 mm rectangular pattern. The benchmark uses explicit 0.01-mm scalar size/radial-location budgets and a 0.001-mm maximum kernel-topology tolerance budget. These are **example policies**, not a manufacturing/GD&T specification.

The validator independently measures analytic B-rep surfaces and checks:

- Exactly one solid, no loose body-level topology, valid B-rep and closed shells.
- Finite positive volume and bounded actual topology tolerances.
- Precise tessellation-independent bounds, actual size limits and datum-plane identities.
- Six required axis-aligned exterior planes and four distinct cylindrical bores.
- Bore diameter, radial location, Z-axis direction and full-thickness extent.
- Exported STEP read/transfer, repeated feature validation, and source-vs-reimported Boolean difference supplemented by independent mass and measured-feature comparisons. A zero Boolean result alone is not exact-equivalence proof.
- SHA-256 binding for specification, STEP bytes and validator source, plus kernel versions and explicit limitations.

The preview is tessellated from the **reimported STEP**, not from a substitute illustration. The validator is intentionally conservative and representation-sensitive: a different surface decomposition of equivalent geometry may be rejected. It is not a complete arbitrary-B-rep or GD&T verifier, and uses the same OCCT kernel for construction and checking. It is not integrated with the application.

The test suite was developed in executed RED/GREEN cycles. Logs retain failures for missing functionality, loose-edge acceptance, invalid specifications, tilted-bores acceptance, wrong exterior planes, export-path absence and doubled size tolerance. Adversarial tests also include blind/missing/shifted/oversized holes, wrong thickness, shells, extra bodies, excessive kernel tolerance and an export changed within the dimensional budget.

Exact final test count, review status and delivered file hashes are recorded in the adjacent final verification note. Do not interpret this bounded test suite as a production success-rate estimate.

## 7. Implementation roadmap and exit gates

### Phase 1 — Make acceptance honest

- Preserve/integrate existing local hardening deliberately; do not reset it.
- Centralize acceptance across all endpoints; distinguish preview from accepted output.
- Fail closed on missing required native checks, wrong shape classes and unexpected bodies.
- Validate final transformed assembly and bind all evidence to delivered bytes.
- Stop synthetic/unavailable evidence and preacceptance retrieval promotion.

**Exit:** every deliberate bad fixture is rejected by generate/run/refine/export, while valid supported fixtures pass. No stale/foreign evidence can accompany a new artifact.

### Phase 2 — Make design intent executable

- Introduce approved typed feature IR, dimensions/limits, datums and parameter provenance.
- Compile a small feature library deterministically.
- Measure features independently from B-rep and reimported exports.
- Give repairs bounded feature-specific diagnostics; abstain outside supported scope.

**Exit:** held-out plates/shafts/flanges/brackets meet explicit independent feature oracles; valid-but-wrong models reliably fail. Publish both false acceptance and abstention, not just success.

### Phase 3 — Correct assemblies and manufacturing definition

- Implement frame-based joints, intended DoFs, residuals and final-position collision/clearance checks.
- Add interface limit/stack-up handling and process-specific DFM.
- Add real semantic PMI only with selected standards and importer tests.

**Exit:** each supported joint and interface has adversarial/native fixtures; manufacturing definitions survive target-CAD import with verified semantics, not just visible text.

### Phase 4 — Bounded engineering analysis

- Measure actual structural geometry and tie loads/materials to revision hashes.
- Make unavailable/missing/invalid analysis fail required gates.
- Validate analysis methods against known solutions and independent tools where appropriate.
- Qualify buckling, fatigue, joints, thermal/pressure and aero methods only for stated supported domains.

**Exit:** independently reviewed engineering cases agree within declared validation limits. Product/regulatory sign-off remains a human engineering responsibility.

### Phase 5 — Measure and improve generation quality

- Run a native artifact-retaining benchmark with frozen retrieval and uncontaminated held-out families.
- Measure first-attempt and bounded-repair performance, false acceptance, abstention, cost and latency by supported family.
- Only then compare prompts/providers, constrained decoding or accepted-only fine-tuning.

**Exit:** reproducible quality gains on independent geometric/engineering oracles—not an improved code-inspection score or execution-only percentage.

## 8. Immediate recommendation

**Implement Phases 1 and 2 first, with plates/brackets/flanges/shafts as the initial verified envelope.** Keep aerospace and general mechanisms in clearly labelled research mode until units, shell intent, interface geometry and analysis provenance are fixed. The attached native plate demonstrates feasibility; it is not a substitute for that integration work.

## Sources

[1] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/verification/geometry.py
[2] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/agents.py
[3] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/eval_harness/runner.py
[4] https://dev.opencascade.org/doc/refman/html/class_b_rep_check___analyzer.html
[5] https://www.asme.org/codes-standards/find-codes-standards/y14-5-dimensioning-tolerancing
[7] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/schemas.py
[8] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/cad_utils.py
[9] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/cad_aero.py
[10] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/analysis/structures.py
[11] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/analysis/airfoil.py
[12] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/postprocessing.py
[13] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/main.py
[14] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/rendering.py
[15] https://github.com/Azrkiel/Mirum/blob/4501ff067e5e89556210611c910be0463e222d7b/backend/strategies.py
