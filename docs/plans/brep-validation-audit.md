# B-rep validation audit and bounded demonstrator

## Scope

Review GitHub `Azrkiel/Mirum` main at `4501ff0`, distinguish the existing uncommitted local hardening at `C:/Users/jiang/Mirum`, preserve that checkout, and produce evidence-backed solutions for engineering CAD. No commits, push, merge, deployment or paid inference.

## Work plan

1. Map all source subsystems and audit generation, assembly, export, validation, analysis, training and UI independently.
2. Run native OpenCASCADE probes against both published and local geometry gates. Challenge false acceptance, not only crashes.
3. Build an isolated, test-first, deterministic four-hole mounting-plate example in `examples/verified_plate/`. Demonstrate real B-rep creation, dimensional feature checks, strict solid accounting, kernel tolerances, and revalidation of STEP exports. Not a general CAD acceptance gate and not certification.
4. Run adversarial native tests, independently review the demonstrator and reconcile findings.
5. Deliver a source-linked audit, evidence, generated STEP and prioritized implementation roadmap. Explicitly separate geometric validity, conformance to a bounded nominal design, manufacturing tolerances/GD&T and engineering fitness.

## Demonstrator contract

Fixed coordinate system, millimetres, axis-aligned rectangular plate, four identical cylindrical Z-through-holes in a rectangular pattern, sharp edges. Unsupported feature classes must fail closed. Required dimensions, size/position tolerances and kernel-tolerance ceiling are explicit. B-rep validation must reject extra bodies/shells, missing/blind/shifted/oversized holes, nonfinite inputs and invalid parameter combinations. Export is accepted only after rereading and revalidating STEP; output evidence binds the actual STEP bytes and spec.

## Limits

Single-plate example only; not production pipeline integration, arbitrary generated Python acceptance, ISO/ASME conformity, GD&T inspection, FEA, fatigue, aero qualification, fit stack-up, manufacturing capability or independent-kernel certification. Native tests use existing Linux Docker CadQuery/OCP runtime and no provider networking.
