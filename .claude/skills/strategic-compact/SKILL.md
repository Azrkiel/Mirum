---
name: strategic-compact
description: Use when a Mirum work session approaches context pressure or crosses a phase boundary (exploration → planning → implementation → verification → next feature). Suggests `/compact` at logical breakpoints rather than letting auto-compaction interrupt mid-task. Pairs with `.claude/rules/token-optimization.md`.
---

# Strategic Compact (Mirum-tuned)

Adapted from [everything-claude-code](https://github.com/affaan-m/everything-claude-code) `strategic-compact` skill, retuned for Mirum's actual phase boundaries.

## When to suggest `/compact`

Suggest compaction at the end of a phase, when the next phase needs little of the prior context:

| Phase transition | Compact? | Why |
|------------------|----------|-----|
| Codebase exploration → planning | **Yes** | Plan is the distilled output; raw `Read`/`Grep` results are ballast. |
| Planning → implementation | **Yes** | Plan lives in TodoWrite or a plan file; free context for code edits. |
| Critic-loop debugging → next feature | **Yes** | Tracebacks and prompt iterations pollute context for unrelated work. |
| Eval-harness run analysis → next iteration | **Yes** | Per-run JSON outputs are bulky; keep only the conclusion. |
| RLAIF prep → training script edit | **Yes** | Reward-design discussion is one-shot; the code is the artifact. |
| Mid-implementation of a multi-file change | **No** | You'll lose variable names, file contents, and partial state. |
| During verification (pytest, eval, manual UI test) | **No** | You need the test output to compare against expectations. |
| After a failed approach, before retry | **Yes** | Clear the dead-end reasoning. Save the lesson to memory or a comment. |
| After dispatching a subagent that returned a long report | **Maybe** | If you've extracted the actionable items into TodoWrite, yes. |

## How to compact correctly

1. **Save first.** If conversation context contains decisions or findings not yet in code/memory/files, write them to:
   - `output/<topic>.md` for design decisions
   - Memory (`~/.claude/projects/.../memory/`) for cross-session preferences
   - A code comment ONLY if it's a non-obvious *why* (per repo style)
2. **Use `/compact` with a focus message.** Plain `/compact` summarises everything; `/compact Focus on the constraint-based assembler refactor` keeps relevant detail.
3. **Confirm verification has finished.** If `superpowers:verification-before-completion` is active, do not compact until the verify step is complete and acknowledged.

## What survives, what is lost

| Persists | Lost |
|----------|------|
| Project rules (`.claude/rules/*.md`) | Tool-call results and tool transcripts |
| Memory (`~/.claude/projects/.../memory/`) | File contents you previously `Read` |
| TodoWrite list | Intermediate reasoning |
| Git state | Subagent narrative |
| Files on disk | Verbal preferences not captured to memory |

## Mirum-specific anti-patterns

- **Don't compact mid-Critic-Loop debugging.** The traceback chain and the Machinist's iterative scripts are the whole context — compacting loses the failure pattern you were tracking.
- **Don't compact while editing `compiler.py` or `schemas.py`.** These are the security-critical files; losing the surrounding context (allowlists, validators, callsites) risks regressions.
- **Do compact after running `eval_harness/runner.py`.** The per-prompt JSON results are large and you only need the aggregate conclusion to plan the next iteration.

## Token-cost levers in this repo

Beyond compaction, these reduce per-turn token cost:

- Use `Grep` / `Glob` instead of blanket `Read` (see `.claude/rules/token-optimization.md`).
- For Mirum's orchestrator (single `GEMINI_API_KEY_HEAVY`), prefer `gemini-3.1-flash-lite-preview` for any new classifier/extractor/structured-output work and reserve `gemini-3-flash-preview` for Machinist, Planner, REA, Refiner, and eval. Model-tier separation, not key-tier — both call sites use the same env var. This is a runtime cost lever, not a Claude-Code one, but worth noting when adding new agent calls in `backend/agents.py`.

## Hooks (intentionally not installed)

ECC's upstream skill ships a Node hook (`suggest-compact.js`) that fires on every `Edit`/`Write`. We did **not** install it because:

1. It runs on every tool call — uses tokens itself.
2. Third-party Node script in a Python/CAD project; security-audit before adoption.
3. Strategic compaction works better as a model-side heuristic than a hook reminder.

If you want the hook later, audit `suggest-compact.js` and add it via `update-config` skill.

## Related

- `.claude/rules/token-optimization.md` — repo-wide read/grep discipline
- `.claude/skills/mirum-security-review/SKILL.md` — different concern, but referenced when compacting after security work
- `superpowers:verification-before-completion` — must complete before any compaction
