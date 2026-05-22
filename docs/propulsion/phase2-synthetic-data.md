# Plan: Propulsion Phase 2 — Synthetic Prompt / CAD Data Generation

> **Scope:** Draft only. Phase 1 (capability) is implemented in branch
> `worktree-mirum+propulsion-domain-f`. Phase 2 layers synthetic-data
> generation on top of that capability so the Machinist (and downstream
> fine-tuned models) can be trained on a diverse Domain F corpus.
> **Status:** Scoping. Implementation has not started.
> **Companion:** `jazzy-exploring-sunset.md` (Phase 1 plan) and
> `jazzy-exploring-sunset-handoff.md`.

---

## 1. Goal

Produce a large (~10k+) corpus of `(prompt, propulsion_spec, STEP, GLB,
GeometryCertificate)` tuples covering the full validator-supported envelope
for Domain F. The corpus seeds:

1. **Machinist fine-tuning** (per architecture.md): preference pairs of
   `(prompt -> generated CadQuery code)` with the spec as the ground truth.
2. **REA evaluation**: regression set for the prompt -> `PropulsionSpec`
   extraction.
3. **Planner evaluation**: regression set for the deterministic
   `_planner_from_propulsion_spec` topology branch.
4. **Coverage diagnostics**: which envelope regions (eg `epsilon < 10`,
   `length_fraction = 1.0`, helix-only injectors) are under-represented.

## 2. Pipeline shape

```
spec sampler --> templated prompt builder --> Gemini rewriter -->
  pipeline (REA -> classifier -> planner -> machinist -> assembler -> cert) -->
  (prompt, spec, STEP, GLB, cert) tuple --> jsonl shard --> retrieval index
```

Each stage is independently re-runnable; failures are logged but do not
poison the corpus.

## 3. Implementation steps

### Step 1 -- Spec sampler

* New module `data_pipeline/propulsion_sampler.py`. Pure-Python; no Gemini.
* Sample `PropulsionSpec` instances by:
  * Pick a topology profile: `nozzle_only`, `chamber_plus_nozzle`,
    `full_assembly_no_regen`, `full_assembly_with_regen`, `injector_only`.
  * Within the profile, sample sub-spec fields uniformly inside the
    envelope `propulsion.validation.validate_all` accepts (epsilon in
    [5, 100], Lf in [0.6, 1.0], etc.).
  * For each sample, call `validate_all`; **reject** failing samples.
    Track rejection rate per profile; this surfaces sampler bugs and
    envelope gaps.
* Output: stream of valid `PropulsionSpec` instances.

### Step 2 -- Templated prompt builder

* New `data_pipeline/propulsion_prompt_template.py`.
* For each spec, emit a deterministic NL prompt using a small template
  library (`"A {nozzle.type} bell nozzle: throat radius {r_t} mm,
  expansion ratio {eps}, ..."` etc.).
* Templates cover ~6 stylistic variations (terse spec sheet, narrative
  engineer-to-engineer, third-person dispatch from a fictional client, ...).
* This gives a baseline prompt that is by construction lossless against
  the spec.

### Step 3 -- Gemini rewriter (controlled paraphrase)

* New `data_pipeline/propulsion_rewriter.py` -- invokes the existing
  Gemini Lite model on each templated prompt with the instruction
  *"rewrite this in a different professional register; do not invent or
  drop numerical specifications."*
* Use Mirum's existing single-key + per-(model, key) bucketing -- DO NOT
  introduce multi-key rotation. See `feedback_gemini_quota`.
* Validate by re-running REA on the rewrite; if the resulting
  `propulsion_spec` differs from the input spec (any numeric field drift
  > 1 %), drop the rewrite. Record drop rate.

### Step 4 -- Pipeline execution + tuple capture

* Re-use the existing `/generate` SSE pipeline; capture every `complete`
  event into a JSONL shard.
* Each tuple: `{prompt, propulsion_spec, planner_manifest, part_scripts,
  glb_path, step_path, geometry_certificate}`.
* Shard size cap: 1k tuples per file (so re-runs are atomic).
* All geometry artifacts stored under `data_pipeline/output/propulsion/`
  with sample-uuid filenames.

### Step 5 -- Coverage diagnostics + retrieval index

* New `data_pipeline/propulsion_coverage.py` -- compute histograms over
  the corpus (epsilon, length_fraction, channel_count, injector pattern,
  intent) and write a markdown report. Identifies envelope gaps for the
  next sampling round.
* Index the corpus into the existing retrieval store (`backend/retrieval.py`)
  with `domain="F"` and `technique` set from
  `extract_techniques(part_script)`.

### Step 6 -- Fine-tuning hand-off

* Out-of-scope for Phase 2 implementation: the actual RLAIF / SFT runs
  belong to `data_pipeline/training/`. Phase 2 stops at producing the
  corpus + index entries; Phase 3 picks them up.

## 4. Quotas, time, and storage

* Gemini Lite: ~0.3 s per call; at 30/min rate-limit a 10k corpus takes
  ~6 h wall-clock (rewriter dominates).
* CadQuery compile per tuple: ~5 s for the heaviest case (regen-cooled
  Rao bell with 60 channels). At 3-way concurrency this dominates total
  wall-clock at ~5 h.
* Storage per tuple: ~250 KB STEP + 100 KB GLB + 5 KB JSONL row -> 350 MB
  for 1k tuples; 3.5 GB for 10k.

## 5. Risks parked

* **Sampler bias.** Uniform-in-envelope is not uniform-in-realistic-engine
  design. Phase 2.1 can layer a "realistic engine" prior (e.g., pump-fed
  LOX/Kerosene vs pressure-fed LOX/CH4) on top of the uniform sampler.
* **Rewriter drift.** Gemini will sometimes drop a numerical
  specification despite the system instruction. The
  re-run-REA-and-compare validation in Step 3 catches this but inflates
  cost ~2x.
* **Helix-cut throughput.** 60-channel regen samples are the slowest.
  If Phase 2.1 needs them to dominate the corpus, profile
  `make_helical_cooling_channels` and consider the
  one-channel-then-array-then-cut optimisation (see Phase 1 plan, risk
  list).

## 6. Acceptance criteria

A Phase 2 run is "successful" when:

* >= 5000 valid tuples in the shard set.
* Validator-rejection rate < 10 % (sampler is producing physical specs).
* REA round-trip pass rate >= 90 % (the rewriter is not dropping specs
  too often).
* All four reference prompts from Phase 1 are recoverable from the
  retrieval index by description-substring match.

---

## 7. Out of scope

* Multi-physics: combustion CFD, thrust prediction, ISP calculation. Stays
  geometric.
* Material density / cost: the existing `verification/material.py` covers
  what we need for mass estimates.
* Refactoring Phase 1 code. Phase 2 lands on top of `main` after the
  Phase 1 branch merges; if Phase 1 needs changes they go in a separate
  fix branch, not in Phase 2.
