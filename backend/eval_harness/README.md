# `backend/eval_harness/` — Mirum reliability measurement floor

The eval harness is the regression net for pipeline behaviour. Every Worktree
in the implementation plan (`cached-questing-hartmanis.md`) gates its merge on
a slice of this harness — see `.claude/rules/worktree-merge-gate.md` for the
exact pre-merge contract.

## Layout

| File | Role |
|------|------|
| `prompts.json` | Curated prompt corpus. Each entry: `id`, `domain`, `difficulty`, `description`, `expected_features`, optional `regression_for`, optional `notes`. Source of truth for what we measure. |
| `runner.py` | CLI entrypoint. Loads the corpus, runs each prompt through `run_critic_loop`, scores with `compute_lvm_score`, writes JSONL + optional markdown. |
| `_prompts_loader.py` | Pure-stdlib filter helpers (`load_prompts`, `filter_prompts`). Used by `runner.py` and the schema/filter tests. |
| `_md_report.py` | Pure-stdlib markdown reporter. Renders one row per result, sorted by `(domain, id)` for diffability. |
| `runs/` | JSONL run outputs (one per `--out`-less invocation, named by epoch). Default location is `$MIRUM_EVAL_RUNS_DIR` (falls back to `/tmp/mirum/eval_runs`). |

## Schema and conventions for `prompts.json`

Every entry MUST satisfy `backend/tests/test_prompts_schema.py`. The contract:

```json
{
  "id": "<DOMAIN>_<short_slug>",
  "domain": "A" | "B" | "C" | "D" | "E",
  "difficulty": "easy" | "medium" | "hard",
  "description": "Human-readable prompt the Machinist receives.",
  "expected_features": ["measurable", "observable", "geometric features"],
  "regression_for": ["A", "C"],
  "notes": "Why this entry exists, free-form."
}
```

Conventions enforced by tests:

- **`id` prefix matches `domain`.** An entry whose `domain` is `"C"` MUST have an id beginning with `C_`. The convention exists so eyeballing a run output tells you which domain template was loaded; it does NOT encode the worktree association (use `regression_for` for that).
- **`regression_for` is a list of single-letter strings drawn from `{A, B, C, D, E}`.** Bare-string values are rejected — the schema test explicitly checks `isinstance(tags, list)` before any membership test, since Python's `"A" in "ABC"` would otherwise silently pass.
- **`regression_for` is independent of `domain`.** A prompt's domain says which strategy template fires; its `regression_for` says which Worktree's fix the case validates. The canonical example: `A_csv_apartment_building` is a Domain A prompt (box-based primitives) but its fix lives in Worktree C (planner decomposition floor), so it is tagged `regression_for: ["C"]` only — `--regression-for A` will NOT pull it in.
- **`expected_features`** must list measurable shape/dimension/topology features. "Should look correct" doesn't count — the LVM scorer can't grade vibes.

## Runner CLI

```bash
# Inside the backend conda env, with GEMINI_API_KEY_HEAVY set.
python -m eval_harness.runner [flags]
```

| Flag | Purpose |
|------|---------|
| `--concurrency N` | Parallel pipeline calls (default 3). Bounded by the Gemini quota; 4–6 is usually safe. |
| `--max-retries N` | Critic-loop retry budget per prompt (default 3). |
| `--filter <prefix>` | Only run prompts whose id startswith `<prefix>`. ANDs with `--regression-for`. |
| `--regression-for {A,B,C,D,E}` | Only run prompts whose `regression_for` array contains the given letter. The Worktree gate. |
| `--dry-run` | Print the matching ids and exit. Skips Gemini entirely — runs in any env without `google-generativeai` installed. Use to verify your filter picks up the right slice before paying for a real run. |
| `--report-md PATH` | After the run, write a markdown table to `PATH` (id · domain · difficulty · status · lvm · features matched · time_ms · regression_for · notes; sorted by `(domain, id)`). Diff two markdown files between runs to spot regressions. |
| `--baseline runs/<file>.jsonl` | Diff JSONL results against a prior run; print newly-passing / newly-failing / added / dropped ids. |
| `--out PATH` | Override the JSONL output path (default: `$MIRUM_EVAL_RUNS_DIR/<epoch>.jsonl`). |
| `--strict-lvm` | Demote a passing prompt to `fail` when its LVM score is below 3.0. Default off so existing baselines stay comparable; turn on for new baselines. |

### Common invocations

```bash
# Worktree A (backend throughput) gate, bash / Git Bash form:
python -m eval_harness.runner \
  --regression-for A \
  --report-md output/qa-reports/$(date +%F)-A.md

# Same in PowerShell (this repo's default shell on Windows):
# python -m eval_harness.runner `
#   --regression-for A `
#   --report-md "output/qa-reports/$(Get-Date -Format yyyy-MM-dd)-A.md"

# Verify what --regression-for would run before paying for it
python -m eval_harness.runner --regression-for B --dry-run

# Compare a Worktree C run against the previous baseline
python -m eval_harness.runner \
  --regression-for C \
  --baseline runs/<prior-epoch>.jsonl \
  --report-md output/qa-reports/$(date +%F)-C.md
```

### Schema test (CI-fast)

```bash
# Pure-Python, no Gemini, < 1s.
python backend/tests/test_prompts_schema.py
python backend/tests/test_runner_filters.py
python backend/tests/test_md_report.py
```

The three tests together pin the contract: schema validity, filter
semantics (especially the `regression_for` ≠ `domain` distinction), and
markdown reporter columns / sort order. Any change to `prompts.json` or
the loader / reporter must keep them green.

## Agent dispatch patterns

The harness pairs with two Claude Code subagents that complete the
Task → Do → Verify loop documented in plan §3.

### `zero-context-reviewer` (Stage 3)

Dispatched immediately after a TDD task is committed. Reads the diff with
no session memory, asks the four Stage-3 questions, returns `PASS` /
`FAIL`.

```
Agent({
  description: "Zero-context review of <task>",
  subagent_type: "zero-context-reviewer",
  prompt: "Review commit <SHA> in <worktree-path>. Task claim: <one line>."
})
```

The reviewer reads only the named commit + the files it touched. It does
not run tests (the worktree owner already did) and does not modify
files. Its sole output is the PASS/FAIL verdict with file:line citations.

### `qa-stress-tester` (Stage 4)

Dispatched after the reviewer says PASS. Mutates a base prompt across
scale/material/feature axes, runs the mutations through this harness
(usually with `--regression-for <letter>` and `--report-md`), captures
real failures back into `prompts.json` with the right `regression_for`
tag.

```
Agent({
  description: "QA stress <base-id>",
  subagent_type: "qa-stress-tester",
  prompt: "Stress-test <base-id> for Worktree <letter>. Append failures to prompts.json."
})
```

The agent appends only — it never deletes existing entries and never
violates the schema test. The merge gate runs the schema test to confirm.

## Adding a new regression entry by hand

Most new entries come from the `qa-stress-tester` agent or from CSV-sourced
user feedback. To add one manually:

1. Pick the domain (which strategy template should fire).
2. Pick the worktree letter(s) whose fix this case validates → `regression_for`.
3. Write `expected_features` as a list of measurable features (no "should
   look correct").
4. Run `python backend/tests/test_prompts_schema.py` — it will tell you
   exactly which contract you violated if any.
5. Run `python -m eval_harness.runner --filter <new-id> --dry-run` to
   confirm the filter picks it up.

## Out of scope for this harness

- **Visual rendering of the GLB.** That ships with Worktree D's
  `render_preview.py` and the Gemini vision call. The harness reads
  `compute_lvm_score`'s output but does not render anything itself.
- **Live SSE behaviour.** The harness calls `run_critic_loop` directly,
  bypassing the `/generate` SSE endpoint. Worktree A's job-queue tests
  cover the HTTP layer.
- **Multi-process sandbox enforcement.** The harness inherits whatever
  sandbox `compiler.py` provides; on Windows the resource-limit guards
  don't apply (Linux-only). For a real sandbox check, run inside the
  Docker container.
