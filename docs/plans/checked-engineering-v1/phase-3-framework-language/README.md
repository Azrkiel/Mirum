# Phase 3 — Mirum engineering framework and language

Branch: engineering/checked-design-v1. Added against 603b0e4b7b860ea39e81ceed1c5b265599697ce3. This phase is a plan; all F tasks start not_started.

## Goal

Build Mirum's own engineering execution framework and **Mirum Engineering Language (MEL)**: a versioned declarative .mir language describing supported objects, physical requirements, design choices and requested evidence. Compile it into typed engineering programs executed through registered geometry backends.

Own engineering runtime: yes. Own parser, type checker, compiler and language semantics: yes, in F04/F05. New mathematical solid-modeling kernel: a separate optional research decision in F11. “Any kernel” means an extensible adapter protocol; a backend becomes usable only after its specific operations, representations, tolerances, runtime and claims have been qualified.

## Position in the existing plan

Phase 1 is B01–B14. Phase 2 is the E capability expansion. This phase follows B14 and E05 for execution; F03 specifically depends on the E01 PicoGK and E02 glider integration. E03/E04 remain mandatory whenever a new structural family or physical/manufacturing claim relies on them, but hardware experiments do not block geometry-only language tooling.

Do not restart or relabel completed B work. Parent [STATUS.md](../STATUS.md) is authoritative for B/E progress. This phase has its own [status](STATUS.md), tasks and P-series release gates. F11 is optional research after the F01–F10 release.

## Accepted end result

A user can write, edit or ask AI to propose a .mir program; inspect its normalized requirements and chosen backend; review the exact plan; execute it; see measured results and revision differences; and download source, IR, lock, reports and native artifacts. The existing beam produces the same bounded verdict through form and language input. A real PicoGK program produces explicitly geometry-only evidence. CLI, Python, HTTP and MCP use the same runtime. Rejected/unsupported/unavailable outcomes remain distinct.

The release includes two qualified geometry backends, not an untested universal compatibility claim. The glider CEM is a domain engineering capability, not a geometry kernel. Physics solvers are a separate adapter/service responsibility.

## Files

| File | Purpose |
|---|---|
| [FRAMEWORK.md](FRAMEWORK.md) | Concrete runtime, IR, operation and backend contracts |
| [LANGUAGE.md](LANGUAGE.md) | Proposed language syntax, semantics and staged compiler |
| [AGENTCAD-REUSE.md](AGENTCAD-REUSE.md) | Source-specific reuse decisions and boundaries |
| [ACCEPTANCE.md](ACCEPTANCE.md) | P01–P14 release checks |
| [STATUS.md](STATUS.md) | F-task tracker |
| [PROMPTS.md](PROMPTS.md) | Standalone starter prompts |
| [INTEGRATION.md](INTEGRATION.md) | Version migration, rollout and handoffs |
| [tasks/](tasks/) | Task-specific goals, outputs, steps and acceptance |
| [examples/beam.mir](examples/beam.mir) | Proposed source fixture; no interpreter exists by this planning commit |
| [REVIEW.md](REVIEW.md) | Gap review and unresolved prerequisites |

Order: F01 → F02 → F03 → F04 → F05 → F06 → F07 → F08 → F09 → F10, then optional F11. F04 can be implemented after F02 without F03, but F05 requires both. [tasks.json](tasks.json) is the dependency index.

Planning does not satisfy any native, provider, physics or physical-test gate. Full V1 release qualification and existing capability scopes remain intact.
