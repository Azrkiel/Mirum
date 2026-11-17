# Phase 3 integration workflow

Read parent [INTEGRATION.md](../INTEGRATION.md), current repository instructions and prerequisite handoffs. Work on engineering/checked-design-v1 using an isolated worktree when needed. Preserve existing implementations and user changes; never reset to the original planning commit.

## Gate and order

F01 executes after B14 and E05; F03 also requires the actual E01/E02 adapters. E03/E04 are conditional only for claims requiring them. Existing B12–B14 work remains the active next work until status/evidence demonstrates completion. Do not mistake this new plan for completed implementation.

F01 freezes interface decisions; F02 owns generic IR/registry; F03 owns backend adapters; F04 owns lexical syntax/AST; F05 owns semantic compilation/review; F06 owns revisions; F07 owns delivered-artifact evidence; F08 owns transports; F09 owns UI/AI; F10 qualifies release. F11 is a later own-kernel feasibility gate.

## Compatibility and migration

Do not relax existing beam models to accept every family. Use a versioned generic program envelope and translators preserving legacy contract meaning and digests. Add explicit bundle/API versions; old clients retain their existing format and route behavior. A new representation/backend must qualify independently.

One-way import of an old brief into a new program preserves its original identity, records translation provenance, and creates a new reviewed execution lock. Regenerating with new native/compiler versions is a new run. Migrations show semantic changes and never change prior accepted records. Keep old artifact downloads readable through their retention period.

Use feature flags for new language/editor/backend selection, with disabled paths not affecting existing generation. Rollback disables new entry paths; retain old lock/IR readers for existing results or return an explicit unsupported-version result. Do not silently execute old source under new semantics.

## Completion and handoff

Each task updates this phase's STATUS.md and writes handoffs/Fxx.md from [TEMPLATE.md](handoffs/TEMPLATE.md). Record tested implementation SHA, exact commands, native versus mocked/provider scope, generated artifacts, public interfaces, migrations, rollback, unresolved issues and next dependency-eligible task.

Phase tasks.json and parent tasks.json contain matching task metadata; update both on dependency changes. Parent STATUS contains a link to this phase rather than duplicate F checkboxes. Parent V acceptance remains independent from this phase P acceptance.

Use a focused commit/push for the authorized task, safe fast-forward integration and rerun affected tests if the remote moves. No force-push, main merge or deployment is implied. Unavailable physical data, provider access or licensing evidence blocks the dependent part only; record it rather than inventing results.
