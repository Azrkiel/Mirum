# Checked engineering V1 — branch overview

Branch: **engineering/checked-design-v1**  
Planning baseline: [d4189b6190c9](https://github.com/Azrkiel/Mirum/commit/d4189b6190c94d3069d9f5c364a7a722178dd775)  
Prepared: November 12, 2026.  
Current progress is tracked in STATUS.md and task handoffs. At this follow-on planning update, STATUS.md reports B01–B11 complete within task scope and B12 next. Full browser/release and live-provider qualification remain separate gates; this documentation update does not revalidate implementation evidence.

## Goal

Make Mirum turn a supported engineering request into a reviewed brief, deterministic candidate geometry, measured engineering checks and one complete evidence package. The first supported family is the existing rectangular cantilever. This branch becomes the integration home for that implementation; each later task builds on its latest completed predecessors.

The user journey is: describe or fill in requirements → review the exact brief → generate and measure candidate parts → evaluate the stated beam model → receive either an accepted package or a specific rejection/unavailability report.

## Accepted V1 end result

A form and an AI-assisted entry path feed the same checked beam workflow. A reviewed 200 mm × 10 mm beam with 50 N end load, 1 mm displacement limit and the documented illustrative material inputs rejects 5 and 10 mm thicknesses and selects 15 mm. The user can inspect the actual accepted geometry and download its STEP, preview, source, brief, final specification, attempt history, reports and manifest as one ZIP.

Acceptance means the declared digital beam checks passed under their stated assumptions. The capability does not assert physical testing, manufacturing release or standards conformity. PicoGK and glider adapters are later scoped capabilities.

## Read this package in order

| File | Purpose |
|---|---|
| [PLAN.md](PLAN.md) | Full reviewed plan and expansion roadmap. |
| [CONTRACT.md](CONTRACT.md) | Exact V1 behavior, scope, outputs, budgets and API decisions. |
| [STATUS.md](STATUS.md) | Live checkbox/state tracker and links to implementation evidence. |
| [tasks/](tasks/) | One execution card per B01–B14 and E01–E05 task. |
| [ACCEPTANCE.md](ACCEPTANCE.md) | V01–V15 release checks and evidence requirements. |
| [INTEGRATION.md](INTEGRATION.md) | How to start, verify, commit, hand off and continue safely. |
| [PROMPTS.md](PROMPTS.md) | Complete copy-ready prompts for each task. |
| [handoffs/TEMPLATE.md](handoffs/TEMPLATE.md) | Required completion/blocker record for future sessions. |
| [REVIEW.md](REVIEW.md) | Planning review findings and explicitly open prerequisites. |
| [tasks.json](tasks.json) | Machine-readable task/dependency/file index. |

Continue from the next eligible task in STATUS.md (B12 at this planning baseline), after reading prerequisite handoffs. Complete B12 → B13 → B14 before the expansion/language execution gates. Completed library/API tasks alone do not qualify the full application or live-provider extraction accuracy.

## Scope and document ownership

CONTRACT.md governs common behavior. Each task card governs its implementation scope and handoff. ACCEPTANCE.md governs release evidence. PLAN.md preserves the reviewed overall plan; its checklist is not a live completion record. STATUS.md is the live task-status source. tasks.json records identifiers/dependencies, not completion state.

If implementation reveals a necessary contract change, update the affected contract, task cards, tests and review record in one change; do not silently loosen acceptance. Changes to physics assumptions, user requirements or capability claims require the corresponding explicit reviewed inputs.

A task is complete only when its output and checks are present in this branch and its handoff records the tested implementation commit. Claims about the overall application must wait for B13/B14. E03/E04 include domain/hardware prerequisites and cannot be completed by invented evidence.

## Phase 3 — engineering framework, kernel adapters and Mirum language

The separate [Phase 3 overview](phase-3-framework-language/README.md) adds AgentCAD-inspired typed operations and transactions, a qualified multi-kernel adapter framework, and Mirum Engineering Language (.mir). See its [task guides](phase-3-framework-language/tasks/), [prompts](phase-3-framework-language/PROMPTS.md), [status](phase-3-framework-language/STATUS.md) and [acceptance gates](phase-3-framework-language/ACCEPTANCE.md).

This follows the existing V1/expansion work; it does not replace completed tasks. F01–F10 implement the runtime/language phase; F11 separately evaluates a future native geometry kernel. Kernel support requires adapters and conformance, not automatic compatibility. For current B/E progress use STATUS.md and the corresponding handoffs.
