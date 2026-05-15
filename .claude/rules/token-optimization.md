# Token Optimization Rules — Mirum / T2CIII

These rules apply to all Claude Code work in this repo. They exist because Mirum's backend has large generated artifacts (telemetry JSONL, eval-harness runs, GLB outputs, RLAIF training logs) that will silently blow out the context window if read whole.

## Never read these directories

| Path | Why | Use instead |
|------|-----|-------------|
| `backend/__pycache__/` | Bytecode, no source value | Skip entirely |
| `backend/telemetry_logs/` | Rotating JSONL, can be 10MB+ | `Grep` with `head_limit`, or pipe through `jq` in a Bash one-liner |
| `/tmp/mirum/telemetry/*.jsonl` | Critic-loop telemetry, append-only | Same as above |
| `/tmp/mirum/work/*.glb` / `*.step` | Binary artifacts | Never read; only check existence with Bash if needed |
| `output/*.json` (eval-harness runs) | Per-run results, large | `Read` with `offset`/`limit` or `Grep` for specific keys |
| `backend/eval_harness/runs/` | Harness output, large | Same as above |

## Read patterns for known-large files

| File | Default approach |
|------|-----------------|
| `output/mirum-implementation-todo.md` (~42K tokens) | Always `Grep` for headers (`^##? `) first to find the relevant tier, then `Read` with `offset`/`limit` for that section |
| `output/baseline-followup-todo.md` | Same pattern |
| `output/wiki-*.md` | Same pattern |
| `backend/main.py` (~900 lines) | Full read OK if making cross-endpoint changes; `Grep` for endpoint name otherwise |
| `backend/agents.py`, `backend/strategies.py` | Use `Grep` for the agent/strategy name; full read only when refactoring shared infrastructure |

## Exploration discipline

1. **Grep before Read.** For any "find X" task, start with `Grep` (with `output_mode: "content"` and `head_limit`) — don't read whole files to find a function.
2. **Glob before listing.** Use `Glob` with a pattern, not `ls -R` or unbounded `find`.
3. **Parallelize independent reads** in a single message. Sequential reads waste turn round-trips.
4. **Telemetry/log inspection:** always `head_limit: 50` or `tail` via Bash. Never load a whole rotating log.
5. **One subagent per question, max 3 in parallel.** Each subagent starts cold; don't dispatch more than the task warrants.

## Compaction triggers

Suggest `/compact` to the user (or self-compact via the `strategic-compact` skill) when:

- Phase transition: Mirum exploration → Mirum planning → Mirum implementation
- After a long Critic Loop debugging session, before moving to a new agent
- After running an eval-harness pass (those results clutter context fast)
- Before switching from `backend/` work to `data_pipeline/` or `training/` work

Do **not** compact:
- Mid-implementation of a multi-file change
- During verification (you'll lose the test output you're checking against)
- When `superpowers:verification-before-completion` is active

## What survives compaction

| Persists | Lost |
|----------|------|
| This file (loaded as a project rule) | Files you previously `Read` |
| `CLAUDE.md` (if added later) | Tool-call results and stdout |
| Memory files in `~/.claude/projects/.../memory/` | Intermediate reasoning |
| TodoWrite list | Conversation back-and-forth |
| Git commits and branch state | Subagent transcripts |

Save important findings to a file or memory **before** compacting if they aren't already in code or git.
