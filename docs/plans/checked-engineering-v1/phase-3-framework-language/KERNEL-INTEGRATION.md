# Integrating geometry and model research with Mirum

This plan extends the checked-program workflow to one equipment-bay assembly. At the F08 baseline, MEL can compile and plan the reference beam, the program service can stage and review revisions, and the beam verifier can inspect the delivered artifacts. The bay requires a new capability. F09 owns the editor and model proposal flow, F10 owns release qualification, and F11 decides whether to fund a native geometry backend. Track completion in the [phase status](STATUS.md). The bay is a proposed capability extension with its own qualification record. [F08 handoff](handoffs/F08.md)

## Ownership and compatibility

Mirum owns the product-facing contracts and conformance tests. The companion `mirum-kernel` repository supplies research implementations and evaluation fixtures. Keep the public contract and its regression cases runnable from this repository with its pinned reference backend. Record which research implementation was used when qualifying a new adapter.

| Product responsibility in Mirum | Geometry or model research supplied through the interface |
| --- | --- |
| MEL syntax, versioned ProgramIR, operation registry, supported dimensions and capability declarations | Adapter implementation, geometry and solver experiments, conformance fixtures and measured limits |
| ProgramInterface, review, revision journal, expected-parent checks, job lifecycle and promotion | Candidate designs scoped to a revision, deadlines, tool versions, artifacts, diagnostics and measurements |
| Delivered-file verifier, policy and evidence tied to exact bytes | Reference cases, expected quantities and independent measurement methods |
| Existing CLI, HTTP, Python and MCP entry points | Offline research harness and model evaluation against deterministic search |

Use the registered OCCT adapter first. Its program declaration currently covers the rectangular cantilever. Extend the capability and its contracts explicitly. Keep existing beam sources, locks, schemas and exports replayable. Reserve any future backend ID for an implementation that passes the adapter tests. Returning an unsupported operation or format must preserve the last accepted revision. [Adapter protocol](../../../../backend/engineering/program/kernel.py) · [Program IR](../../../../backend/engineering/program/ir.py)

## Implementation order and acceptance

1. **Define the assembly contract in Mirum.** Add a versioned capability with reusable part definitions and revisions, occurrence IDs and transforms, named mounting ports, hole correspondences, allowed orientations, envelope and alignment limits. Specify the parent frame, transform direction and axis convention. Lower the reviewed brief to ProgramIR using registered operations. Preserve source spans for diagnostics. Bind the review to the capability version, catalog snapshot and expected checks.
2. **Run a unit test across the real adapter boundary.** Keep millimetres for length, MPa for pressure, N for force and radians for angle on the wire. Feed equivalent inputs of 1 m and 1000 mm through normalization, native build and independent measurement. Both must yield the same measured length within the declared tolerance. Record conversions in any SI solver adapter. Before thermal cases, version the quantity vocabulary for absolute temperatures, temperature differences and the heat quantities the solver consumes. Test that 0 °C maps to 273.15 K and a difference of 1 °C maps to a difference of 1 K. [Current units](../../../../backend/engineering/program/units.py)
3. **Build through the existing program path.** Stage the reviewed bay, run the bounded search and export hierarchical STEP, a BOM with definition revisions and occurrence counts, and interface witnesses. Record both repository revisions and software locks in the conformance run. Mirum independently rereads the exact delivered STEP, checks units, placements, envelope and one-to-one hole correspondence, and ties the result to the revision. Qualify interoperability with a separately identified second reader. [Current beam verifier](../../../../backend/engineering/program/delivered.py)
4. **Exercise one accepted revision and one rejected revision.** In a 560 × 260 mm bay, ten modules in the 90° layout should measure 480 × 250 mm. Stage a supplier substitution whose envelope is identical but whose four mounting holes have shifted by 2 mm. Record four failed correspondences against the 0.05 mm digital fixture criterion. The accepted head, its STEP and its BOM must still identify the original revision. Exercise an interrupted run, retry and stale parent. The engineering owner can separately inspect the physical article against an agreed drawing and intended use.
5. **Connect the model at F09's proposal boundary.** Establish the first reviewed head using the ordinary program workflow. Give a proposer the allowed choices, expected parent, fixed requirements digest and measured failure. Submit candidate MEL through the guarded proposal interface. Compare the newly compiled requirements with the reviewed requirements before a candidate can proceed under that brief. Exercise a stale parent, changed target and a valid geometry-only edit. Keep review and final acceptance in Mirum. Use deterministic and offline proposals for the initial comparison. [Program interface](../../../../backend/engineering/program/facade.py) · [F09 scope](tasks/F09-editor-and-ai.md)

## Implementation evidence

M1 editable part acceptance passed on the product release pin. Mirum reviewed and published both width edits, refused a valid STEP from the other intent, and recovered an interrupted edit without losing the accepted revision. [M1 evidence and offline replay](handoffs/editable-plate.md)

The M3 structural fixture has a [native conformance replay](handoffs/structural-cantilever.md) through the reviewed cantilever path.

The M3 thermal fixture passed both offline replays and the reviewed program flow. Mirum verified the nominal STEP and numerical report, refused a stale temperature field and a negative remaining gap, and reread the archived nominal revision. The structural regression also passed. [Thermal verification and replay](handoffs/thermal-bar.md)

The equipment-bay MEL fixture now exercises the real OCCT adapter and delivered verifier through the program routes. The integration tests cover recovery to the 90° layout, equivalent `1 m` and `1000 mm` inputs, nominal promotion, the four supplier mounting failures, retained STEP and BOM digests, stale-parent refusal and interrupted admission followed by rebuild. The accepted package can be archived and reread against its reviewed native lock. [Equipment-bay handoff](handoffs/equipment-bay.md) · [Replay contract](handoffs/F10.md)

## Long-lived projects and native geometry

The F08 handoff records K51: a lifetime cap of 256 programs and an append-only journal, with public enablement blocked. Before scaling to many subsystem agents, design bounded retention for active history and durable archives for accepted source, IR, locks, reviewed inputs, artifacts and evidence. Exercise expiry, archive and restore, an interrupted archival operation, and creation beyond the former lifetime ceiling. Demonstrate that a restored package resolves its original digests and decisions and that active quotas still work. Record the migration, recovery results and enablement decision in Mirum. [F08 limits](handoffs/F08.md)

Collect reference-backend failure cases, tolerance measurements, timings and memory profiles during the bay work. After F10, use those measurements for F11's go/no-go decision. A proposed native operator must demonstrate a specific advantage and qualify its own claim scope through the same adapter and independent reader. The existing backend supplies the baseline for each comparison. [F11 decision](tasks/F11-own-kernel-research.md)
