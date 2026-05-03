# Worktree merge gate

The pre-merge contract for any worktree spawned from the implementation
plan `cached-questing-hartmanis.md` (Worktrees A, B, C, D, E, F). A
worktree's PR / fast-forward to `main` MUST NOT land until every applicable
gate below has been cleared.

## The three gates

### (a) Schema tests pass — always required

```bash
# Pure stdlib, < 1s. Runs in any env including bare Python (no pytest).
python backend/tests/test_prompts_schema.py
python backend/tests/test_runner_filters.py
python backend/tests/test_md_report.py

# Equivalent if pytest is installed:
pytest backend/tests/test_prompts_schema.py \
       backend/tests/test_runner_filters.py \
       backend/tests/test_md_report.py
```

All three must pass. They pin the prompts.json contract (`id` prefix
matches `domain`, `regression_for` is `list[str]` from {A,B,C,D,E},
no duplicate ids, every CSV-sourced failure prompt is represented),
the filter contract (`--regression-for X` ≠ filtering by `domain X`),
and the markdown reporter columns / sort order.

### (b) Worktree's regression slice completes with no new failures — always required

```bash
# Run the slice this worktree gates, write a markdown report:
python -m eval_harness.runner \
  --regression-for <letter> \
  --report-md output/qa-reports/$(date +%F)-<letter>.md
```

`<letter>` is the worktree's letter (A/B/C/D/E). For Worktree F itself,
substitute `--report-md output/qa-reports/$(date +%F)-F-full.md` and run
the full corpus (no `--regression-for` filter) — F is the harness, so it
gates on the entire suite passing.

The run is acceptable if and only if the markdown report shows **no
newly-failing ids** vs. the most recent baseline run on `main`. Diff
the two markdown files with `diff` (or eyeball the table). Newly-passing
ids and added prompts are fine; newly-failing ids block the merge.

If you cannot run the live harness (no Gemini key, no conda env),
`--dry-run` at minimum verifies that `--regression-for <letter>` picks
up the right slice — but a dry-run alone does not satisfy gate (b).

### (c) Security review — required only when the diff touches the listed surfaces

Required when the worktree's diff touches any of:

- `backend/compiler.py` (the CadQuery sandbox)
- `backend/main.py` endpoints (any `@app.post` / `@app.get`)
- `backend/schemas.py` (Pydantic models exposed via the API)
- Anything that reads or writes `GEMINI_API_KEY_HEAVY`
- `backend/cad_utils.py` (sandbox-allowlisted module)

Dispatch:

```
Agent({
  description: "Security review of <worktree>",
  subagent_type: "mirum-security-reviewer",
  prompt: "Audit the diff between <baseline-sha> and HEAD on the <worktree> branch. Worktree purpose: <one line>. Touched files in scope: <list>."
})
```

The agent must return a report whose `CRITICAL` and `HIGH` sections are
empty (or contain only findings the worktree owner has explicitly
justified inline in the report's reply). `MEDIUM` findings should be
addressed unless rationale is captured.

## Worktree → gates map

| Worktree | Letter | (a) schema | (b) regression slice | (c) security review |
|----------|--------|------------|----------------------|---------------------|
| backend-throughput | A | required | `--regression-for A` | required (touches `main.py` endpoints) |
| cadquery-validation | B | required | `--regression-for B` | required (touches `compiler.py`) |
| scale-and-decomposition | C | required | `--regression-for C` | conditional (only if `schemas.py` changes) |
| visual-verifier | D | required | `--regression-for D` | not required by default |
| domain-escape-hatch | E | required | `--regression-for E` | conditional (only if `schemas.py` `MateType` changes) |
| qa-harness | F | required | full corpus | not required (no production surface) |

## Operating notes

- The gates exist so a worktree's fix cannot regress an unrelated worktree's
  guarantees. The `regression_for` field on prompts is what makes (b) cheap:
  each Worktree only runs the slice it owns, plus the full-corpus baseline
  on `main` after merge.
- The Stage-3 zero-context reviewer (per-task) and the Stage-4 qa-stress
  agent (per-feature) are different gates — they fire DURING the worktree's
  Task → Do → Verify loop, not at merge time. The merge gate above is the
  final integration check.
- Skipping a gate "because it's just a docs change" is not allowed. Docs
  changes that touch CLAUDE.md or `.claude/` files do not need the
  regression slice (b), but still run schema tests (a) since those are
  cheap and catch accidental changes to the prompts file.
- A failing gate is a fix, not a waiver. Don't `--no-verify` past it.
