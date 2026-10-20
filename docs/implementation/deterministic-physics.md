# Deterministic, physics-informed CAD: architecture review and bounded v1

## Verdict

Mirum can produce native B-reps, but the published natural-language pipeline is not a deterministic engineering-certification system. Kernel validity, design intent, assembly correctness, material/load suitability, manufactured tolerances and standards compliance are different requirements. Neither successful generated Python nor a plausible preview establishes all of them.

The implementation here adds a real opt-in no-training solver-in-the-loop path for **one bounded family**, not a universal solution. Existing natural-language routes remain available with their existing scope. No trained/fine-tuned network or PINN is used.

## Exact source baselines

| Source | Revision | Treatment |
| --- | --- | --- |
| `Azrkiel/Mirum`, GitHub main | `6db3156c8c96c9aaaaff7f8e6482318b4842a42a` | Published baseline audited |
| Mirum tracked `engineering/brep-acceptance` | `de07fdccc577099fbb65cf2469f84abb1d3a0b84` | Implementation base; already has structural STEP acceptance and hardened delivery |
| New worktree | `C:/Users/jiang/Mirum-physics`, branch `engineering/verified-physics` | Uncommitted candidate; no merge/push/deployment |
| `nimbalyst/nimbalyst` | `79b8c560c491061c39ea70d01097139055b62ae8` | App/extension integration inspected read-only |
| `nimbalyst/nimbalyst-replicad` | `c4211260044b5e1c1c620bde941dc398683869cf` | Actual CAD extension inspected read-only; MIT license |

Existing Mirum and phase1 checkouts are preserved. Findings about published main must not be attributed to the stronger branch as though its existing fixes were absent.

## Architecture and feature review

The reachable path is `FastAPI -> Planner/REA -> domain-specific Machinist/Critic -> CadQuery execution -> assembly/export -> native acceptance -> response`. Source/code generation still involves model calls, so repeated prompts cannot be promised to generate identical specifications or source. Trusted parametric helpers reduce variability but do not independently prove that requested features were built correctly.

| Capability | Evidence / gap |
| --- | --- |
| Native CAD and exports | CadQuery/OpenCASCADE produces B-rep; the stronger branch captures STEP once, validates the imported solid set and renders the delivered GLB from STEP. Native tests are needed, not mocks of the kernel. |
| Design conformity | Main/general free-form generation lacks a comprehensive typed feature IR with independently measured predicates for every requested feature. V1 below adds this for a rectangular cantilever. |
| Assembly | Existing constraint solving and structural solid-set tests are useful; they do not prove every joint's intended DoFs, clearance, manufacturing fit or kinematic behavior. |
| Domain helpers | Aero/gear/propulsion/sheet-metal/organic helpers have native and/or unit coverage, but their entire natural-language input space is not independently qualified. A solid external envelope is not automatically a physical thin-walled structure. |
| Wing structures | `analysis/structures.py` is sizing-grade analytic wing-box/flight-envelope analysis, not finite-element analysis. Inputs come from requirements and assumed sections, not a mesh of the delivered part. Two reproduced false-success cases are fixed here. |
| Aero analysis | Optional XFOIL/AVL/lifting-line paths and fixture tests are not equivalent to live solver convergence or aircraft certification. Record real solver vs mock provenance separately. |
| Surface continuity | `backend/continuity_check.py` has advisory skip/pass paths; `backend/rendering.py` can render synthetic stripes when a real renderer is unavailable. Those images cannot substantiate surface-quality acceptance. This v1 does not use VLM/synthetic imagery for acceptance. |
| PMI / standards | Annotation metadata and an exchange-format label do not establish semantic face-linked GD&T or standards conformity. Applicable standards, editions, datums and inspection methods still need explicit implementation. |
| Parasolid | `backend/exporters.py` documents a dependency-gated converter with STEP fallback. A STEP download is not native Parasolid success. |
| Retrieval / learning / research | Training, research and offline evaluation modules are not evidence of a trained production geometry/physics model or held-out engineering success rate. No training is performed here. |
| Security / lifecycle | Existing AST policy and bounded ProcessRunner are reused without relaxing imports, credentials, memory or timeouts. These are trusted-local execution controls, not a hostile-code OS sandbox. |

Coverage is a critical-path architecture review plus the actually executed suites. It is not exhaustive line-by-line verification, a live-provider benchmark, a full manual UI feature pass, or production authorization.

### Concrete remaining blockers in legacy/general generation

These are **not fixed by the bounded cantilever addition**, so the whole product must not be described as engineering-qualified:

- **Valid-but-wrong bracket:** current `backend/cad_utils.py:701-741` documents wall height above the base, but centers the upright at `z=base_thickness`. A real native probe of `make_bracket(40,60,5,30,4,n_mounting_holes=0)` returned a kernel-valid body with only **17.5 mm** above the base rather than 30 mm.
- **Fastener approximation is not mechanical thread fit:** `cad_utils.py:399-438,518-559` builds annular ribs and a plain minor-diameter nut bore. A current native coaxial/centered M10 example intersected by **82.02268474387722 mm³**. It is a visualization approximation, not verified mating screw geometry.
- **Manifest integrity:** the current real Pydantic schema accepted an empty assembly and duplicate part IDs in explicit probes. `backend/schemas.py:550-578` checks maxima and ID references, but not full uniqueness/connectivity/anchor integrity.
- **Joint semantics:** `backend/agents.py:102-109` maps FASTENED and SLIDER to a Plane constraint and REVOLUTE/CYLINDRICAL to Axis. Solver convergence alone cannot demonstrate rigid fastening, intended free DoFs, axial restraint or requested clearance. Semantic/measured assembly regressions are still required.
- **Old wing numerical API:** negative signed loads, invalid standalone section dimensions, omitted gust data and material defaults need separate input/provenance hardening. The two fixed false-positive cases do not qualify all legacy load cases.

The independent published-baseline audit also found filesystem capabilities reachable through ordinary CadQuery attributes under the older import-only scanner. Existing branch policy hardening substantially addresses that path; this task did not perform a hostile-code penetration test or claim an OS sandbox.

## What Nimbalyst actually does

The supplied app repository delegates CAD to the separate Replicad extension: `packages/electron/src/main/data/extensionRegistry.json:959-1000`. The editor uses Replicad over OpenCASCADE compiled to WebAssembly, not a mesh-only modeling kernel.

Useful source-level mechanisms:

- `src/runtime/client.ts:13-49`: one reusable kernel worker, serialized request queue; `:112-167` request ID matching, timeout/worker reset. Useful lifecycle ideas, but Mirum retains its existing process-isolated worker rather than sharing OCCT state between jobs.
- `src/aiTools.ts:200-298`: fresh disk-based compile/inspect, parameter overrides, measured metrics, multi-view screenshots and diagnostic feedback. The valuable idea is **inspect actual geometry, then correct parameters**.
- `src/runtime/worker.ts:470-481`: native kernel volume/area measurements supplement mesh statistics.
- `src/printability.ts:454+`: explicitly heuristic mesh-derived printability checks. Bed fit, overhangs and watertightness are not FEA, stress qualification or engineering standards conformity.
- `src/aiTools.ts:347+`: export uses the compile/export pipeline rather than treating a screenshot as CAD.

This change independently implements the inspection-and-correction behavior using Mirum's Python/OCP architecture. It does not copy TypeScript source, replace CadQuery with a WebAssembly kernel, or claim Nimbalyst has a standards-qualified FEA pipeline.

Do not import its weaker success semantics. In the inspected extension, `src/runtime/worker.ts:448-458` falls back to the first shape if multi-shape compounding is unavailable/fails; STEP/STL export uses that shape (`:597-608`) while preview metrics can cover all shapes. Export also recompiles independently and does not enforce inspection acceptance (`src/aiTools.ts:368-405`). These are source-established failure paths, not runtime reproductions. Mirum's complete-solid accounting, STEP reimport and exact artifact binding are stronger acceptance behaviors and are preserved.

## New deterministic engineering path

```
approved typed requirements
  -> trusted canonical CadQuery source
  -> existing AST-gated capture and native structural STEP acceptance
  -> independent STEP design measurements
  -> beam finite-element analysis, convergence, equilibrium, analytic benchmark
  -> reject or evaluate the next explicitly approved height
  -> return accepted STEP + STEP-derived GLB + revision-bound evidence
```

Endpoint: **`POST /engineering/generate`**. See [`examples/engineering/cantilever.json`](../../examples/engineering/cantilever.json).

The bounded path is reachable through the API; it is not silently inserted into the legacy natural-language planner or exposed as a new Streamlit form. The approved typed specification is the authority. No model is called and no material/load/standard is guessed.

### V1 capability envelope

- One homogeneous solid rectangular prism, positive XYZ frame, fixed at `X=0`.
- Static transverse `+Z` end force at `X=L`; no other load case.
- Length at least ten times both section dimensions.
- Explicit length, width, height, digital size tolerance, Young's modulus, allowable bending stress, end force, maximum tip deflection and user-approved material-reference string.
- Explicit acknowledgement of ideal-clamp/end-force/linear-elastic assumptions.
- Small-deflection policy: actual tip displacement at most 2% of length. Small-strain application policy: computed maximum bending strain at most 0.5%.
- No holes, notches, fillets, thin-wall idealization, nonlinear response, thermal loads, buckling, fatigue, contact, 3D solid stresses or physical material verification.

### Independent native geometry checks

Reimport the exact delivered STEP; transfer all roots; require the existing native solid-set gate plus exactly one solid, six planar faces, twelve edges, eight vertices and orthogonal face pairs. Measure optimal native bounds, positive datum frame, complete prism volume/surface area and the root-face area/centroidal second moment. These measurements are taken from OCP, not echoed builder inputs.

Kernel numeric tolerance must be at most `min(1e-6 mm, approved dimension tolerance / 10)`. Dimensional comparisons include a guard of twice the measured topology tolerance. This is an application numerical policy, not a manufacturing uncertainty/GD&T model.

Adversarial native tests include kernel-valid wrong dimensions, translation, a bore, extra bodies, rotation and an oversized part whose endpoints are each within half the nominal size budget. Physics never runs for a failed design-intent check. A separately labelled reader-report seam tests inflated numeric uncertainty; it is not presented as a STEP-preserved defect.

### Real beam finite elements, not a renamed formula

`backend/engineering/beam_fea.py` assembles and solves the Euler–Bernoulli element stiffness matrix with two DOFs per node. Rotation is length-scaled for conditioning. The verifier uses **measured** section inertia/dimensions, solves meshes of 4, 8 and 16 elements, checks free-DOF residuals, root force/moment equilibrium and compares against an independent cantilever closed form. Both mesh change and benchmark relative error must be at most `1e-7`.

A uniform prismatic end-loaded Euler–Bernoulli beam has a cubic displacement field, represented exactly by the element interpolation. Very small mesh-change/benchmark errors are therefore expected; they do **not** demonstrate convergence of a 3D stress mesh or physical validation of the assumptions.

### Physics-informed correction without training

`candidate_heights_mm` is an optional ordered list of at most four unique, explicitly approved heights. All candidates are schema-validated before native work. The service builds, measures and solves each in the supplied order; it returns the first passing candidate or rejects the entire request. It does not silently clamp geometry, alter length/material/load, infer a new allowable, or treat an unavailable worker as a pass. This is bounded deterministic search, not a global optimizer.

### Evidence and delivery semantics

Accepted responses bind canonical specification, original approved search request, final executable source, STEP, GLB, native/solver versions and implementation-source hashes. Rejected responses contain evidence/reasons but no STEP/GLB. Missing/malformed/stale/incomplete worker reports fail closed. Native/FE tasks have a 30-second deadline, 1.5 GiB address-space cap and bounded output through the existing ProcessRunner. Requests are limited to 2/minute and one active engineering request per API process.

`passed: true` means **the declared digital geometry and this idealized beam model meet the supplied requirements**. `engineering_certified` remains `false`; `standards.state` remains `not_evaluated`. A reference string is provenance supplied by the caller, not proof of actual material condition/allowables.

Determinism means identical canonical source/specification and tolerance-bounded native/solver outputs in the pinned runtime. STEP files can contain timestamps; identical STEP bytes are not promised. Do not reuse an engineering report after editing geometry, loads, material or requirements.

## Existing wing-analysis fixes

Two tests reproduced false positives in `analysis/structures.py`:

1. Absent span and no explicit root moment used zero as a placeholder; a sufficiently strong section could pass. The bending result is now explicitly unevaluated/failed and the overall result cannot pass. Existing numeric fields remain compatible; the warning remains visible.
2. Cruise `CL > CLmax` produced only a warning while overall `passed` depended on section margins. The operating condition must now be feasible for an overall pass.

Explicit supplied root loads remain supported without a span. These changes do not convert wing-box formulas into geometry-bound FEA or make the legacy optional structural stage a mandatory model-delivery gate.

## Verification and remaining work

See the separate verification evidence/handoff for actual command outputs and final review status. Native focused tests are exercised without provider/network calls. A real local HTTP request and repeated identical request produce retained STEP/GLB and numerical evidence. The preview is rendered from actual GLB triangles, not synthetic geometry.

The clean `Dockerfile.verify` dependency rebuild was attempted but blocked by PyPI TLS certificate-chain failure. TLS was not bypassed. A local source-overlay verification image reuses the installed native CAD runtime; that is source-bound native testing, **not** a clean dependency/release build.

Before claiming broader engineering/standards support: add useful typed feature families with independent native predicates, stable semantic face/load identities, real meshing and 3D elasticity, mesh-quality and convergence studies, validated material/BC inputs, qualified solver benchmarks, standards-specific manufacturing/GD&T rules and geometry-bound assembly verification. Extend through vertical tested slices. Do not expand a certificate label faster than its evidence.
