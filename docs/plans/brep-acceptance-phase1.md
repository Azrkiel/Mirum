# Phase 1 continuation: structural acceptance slice

## Baseline and isolation

Working branch `engineering/brep-acceptance` in `C:/Users/jiang/Mirum-brep-phase1` is overlaid with the exact current local `Mirum` working source, including prior uncommitted hardening. `.hermes/inherited-baseline.json` records hashes of 191 inherited files. The original checkout, audit worktree and other repositories remain unchanged. No commits, push, merge or paid inference.

## Target for this slice

Create a shared fail-closed **structural** native acceptance service, reject pure-topology and gross interference failures demonstrated by the audit, and use actual final positioned native geometry in accepted delivery. Apply to generate, edited-source run, refinement and direct STEP/Parasolid-source export where feasible. Bind reported evidence to actual delivered artifact bytes. Preserve legitimate multi-body parts/assemblies and old schema compatibility where it does not weaken acceptance.

## Implementation sequence

1. Capture inherited baseline and inspect actual test/deployment conventions.
2. Real-kernel TDD for a bounded OCP solid-set inspector: valid/pure/closed solids, finite positive per-body measurements, topology tolerance budget, loose topology rejection and final-position interference. Multiple separated/touching solids remain valid. Native inspection occurs in a bounded owned worker, not unbounded kernel calls in the API event loop.
3. TDD for shared script-capture/acceptance/publication helper and routes. Generated code executes through the existing AST/resource/lifecycle gate. Final accepted GLB is derived from validated STEP in a trusted worker where the native importer supports correct assembly preservation. Otherwise retain explicit, narrower evidence scope; do not invent equivalence proof. No repeated user-code execution for a separate certificate.
4. Native API tests with deterministic model fixtures for successful and rejected generate/run/refine/export paths. Existing compiler/cancellation/assembly tests remain regression gates.
5. Full native offline pytest gate, source identity checks and independent security/logic review; fix concrete findings with RED/GREEN tests, then fresh review.

## Verification commands

Use existing local `mirum-integration-verify:latest` image with source tree bind-mounted read-only over `/workspace`, `PYTHONPATH=/workspace:/workspace/backend`, tmpfs scratch, no network and read-only root. Run `python -m pytest` using the inherited `pytest.ini`; paid/live tests remain excluded. Rebuild immutable verification image for final qualification when relevant.

## Scope exclusions

This slice does not complete the full audit roadmap. Typed feature/requirement conformity, joint semantic redesign, GD&T/PMI standards compliance, process DFM, physical validation, aerospace units/skin fixes, comprehensive retrieval/training provenance and arbitrary hostile-code isolation remain separate work. Kernel tolerances and interference volume thresholds are declared numerical application policies, not manufacturing tolerances or mathematical guarantees. Labels must state structural scope and no engineering certification.
