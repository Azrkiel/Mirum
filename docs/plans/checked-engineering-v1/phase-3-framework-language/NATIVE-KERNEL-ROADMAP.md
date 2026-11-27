# Native geometry kernel roadmap

Owner decision, 2026-11-24: **go.** Mirum will build its own solid-modeling kernel. This document is the separately scoped roadmap that the [F11 task](tasks/F11-own-kernel-research.md) requires for a go decision. [F11 decision record](handoffs/F11.md)

## Basis of the decision

The go is a strategic owner decision: Mirum wants to own its geometry stack. It is not a finding that OCCT failed. The six measured equipment-bay cases showed no OCCT defect, and the F11 analysis recommended no-go on that evidence alone. Neither this roadmap nor any stage below may claim that OCCT is insufficient unless a stage measures it.

Consequences:

- OCCT stays the reference backend and the differential oracle. Disagreement with OCCT is a triage signal. Settle it with an analytic value or an independent construction before naming a winner.
- The native backend inherits no acceptance. Every capability it serves is qualified from scratch through the same adapter conformance suite, delivered-file verifier and second STEP reader as OCCT.
- Promotion of any capability to the default backend is a separate owner decision with measured evidence (see [Promotion](#promotion-to-default)).

## Ownership

| Repository | Owns |
|---|---|
| `Azrkiel/mirum-kernel` | Kernel implementation for G0–G4, its unit and differential tests, fixtures and benchmarks. The [G-stage plan](https://github.com/Azrkiel/mirum-kernel/blob/main/docs/kernel.md#building-a-native-geometry-kernel) defines the stage contents. |
| `Azrkiel/Mirum` | Backend registration, the adapter, conformance and delivered-file verification, program review, revisions and publication. Changes to shared schemas and product interfaces are made here. |

Keep a process boundary between Mirum and the native engine, as the OCCT and PicoGK workers already have. Mirum never links the kernel in-process.

## Fixed choices

- **Implementation language:** Rust, behind a worker process with a JSON or CBOR request protocol. Reasons: memory safety for untrusted geometry input, a usable open reference (Truck, Apache-2.0) for study, and a WASM path for in-browser preview later. G0 may overturn this only with a written reason in its decision record.
- **Licensing:** original code. Study open implementations, but copy nothing from LGPL OCCT. OCCT is used only as an external oracle process.
- **Units and tolerance:** millimetres and radians on the wire, matching [units.py](../../../../backend/engineering/program/units.py). Linear model tolerance of 1e-6 mm, recorded in every result. Inspection keeps Mirum's existing criteria (0.05 mm for the digital mounting fixture).

## Stages and gates

Each stage starts only after the previous gate passes and the result is recorded in [STATUS](STATUS.md). The bounded prototype is N0 + N1. N2 needs a recorded gate pass. N3 and N4 need targets written and approved before they start.

### N0: topology core (kernel G0)

Implement the geometry/topology split: points, oriented curves, parameterized surfaces, vertices, oriented edge uses, wires, trimmed faces, shells, solids, error bounds and units.

Gate (all required):

| Case | Target |
|---|---|
| Box, through-hole block, nested loops | Euler–Poincaré `V − E + F = 2(S − G)` holds; face, edge and vertex counts equal the analytic counts |
| Box and through-hole volume | Relative error ≤ 1e-12 against the analytic value |
| Open shell | Closure check rejects it with a typed diagnostic |
| Reversed face | Orientation check detects it; signed volume of valid solids is positive |
| Fuzzing | 10,000 random malformed topology inputs: no crash, no hang beyond 1 s each, every rejection typed |

### N1: analytic modeling and the first Mirum capability (kernel G1)

Implement planes, lines, circles, cylinders, 2D sketch constraints, profiles, extrude, revolve and rigid transforms. Serve the existing `editable_plate@1.0.0` capability (the M1 four-bore plate) and nothing else.

Gate (all required):

| Check | Target |
|---|---|
| Plate volume | Relative error ≤ 1e-9 against the analytic value, for the nominal plate and both M1 width edits |
| Bore positions and axes | Absolute error ≤ 1e-6 mm against analytic positions |
| Topology | Face, edge and vertex counts equal the OCCT build of the same plan |
| Exchange | The native AP214 STEP is read by OCCT XDE and by FreeCAD 1.0; Mirum's delivered verifier passes with unchanged criteria |
| Edit survival | All four bore references survive both width edits (4/4 IDs stable). The wrong-intent STEP is still refused |
| Sketch constraints | Under-, over- and redundantly constrained sketches return typed diagnostics; none silently picks a solution |
| Adapter conformance | Passes the same P04/P05 suite as OCCT. Unsupported plans return `unsupported` and preserve the last accepted revision; no fallback to OCCT |
| Measurement (recorded, not gated) | p50/p95 wall time and peak RSS over 30 cold and 30 warm runs, native versus OCCT, same machine and cases |

### Mirum registration (during N1)

- The backend ID `mirum-native` needs `mirum_kernel_v0_2` records. `BackendId` in [kernel.py](../../../../backend/engineering/program/kernel.py) is frozen at `occt | picogk` in v0_1. Add a new records version and a new registry version, and leave the frozen v0_2 registry byte-identical. Existing locks and archives must replay unchanged.
- Declare only `editable_plate@1.0.0`, with the same claim scope as the OCCT reference (`geometry_checks_only`). The constant `NATIVE_CLAIMS` pins it, and a test checks every completed native result.
- The backend is selected only by an explicit plan. It is never a default and never a fallback. It is not exposed on public routes; K51 still blocks public enablement.
- The changes touch the program API surface, so each one gets the independent security review before merge.

### N2: Booleans (kernel G2)

Broad-phase rejection, surface intersection, trim curves, face splitting, region classification, stitching and validity checks.

Gate:

| Check | Target |
|---|---|
| Differential corpus | At least 200 fixed cases, including tangency, coplanar overlap, periodic seams, tiny edges, coincident faces and edits near model tolerance. The case set and its hash are frozen before the run |
| Correctness | Relative volume error ≤ 1e-9 where an analytic value exists, otherwise agreement with an independent construction |
| Failure rate | No higher than OCCT's on the same corpus |
| Validity | Zero invalid solids emitted silently. Every failure returns a diagnostic witness |
| Product | The equipment-bay capability builds natively and reproduces all six fixture outcomes, including the four supplier failures at 2 mm |

### N3 and N4 (kernel G3, G4)

N3 covers NURBS evaluation, trimmed surfaces, lofts, sweeps and blends. N4 covers feature history, persistent references, selective rebuild, native STEP and optional GPU batching. Write measurable targets for each, in the same form as above, before the stage starts. Record them in STATUS.

## Promotion to default

A capability moves its default from OCCT to `mirum-native` only when all of these hold, and the owner approves it:

1. Its stage gate passed on the release pin, with conformance, the delivered verifier and the second reader.
2. Edit survival is equal to or better than OCCT's on the same edits.
3. One measured benefit: an operation OCCT cannot deliver, or p95 latency or peak memory at least 25 % better on the same cases.
4. A clean release E2E on `main` with the native default.

## Out of scope

- No production use, public exposure or inherited acceptance before a gate passes.
- No general-purpose CAD kernel claim. Each claim is limited to the capabilities qualified.
- Physical acceptance stays a separate owner record.
