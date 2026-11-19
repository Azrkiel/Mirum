# Execution and integration guide

## Branch workflow

Use engineering/checked-design-v1 as the sequential integration branch. This documentation commit creates its plan; application work starts at B01. Work on one task at a time unless a later explicit instruction authorizes a different workflow.

1. Fetch and inspect the latest remote branch. Use a clean isolated worktree when the current workspace has unrelated work. Preserve all user changes.
2. Read repository instructions, README.md, CONTRACT.md, the task card, STATUS.md and prerequisite handoffs. Check that prerequisite code exists at the current head.
3. Record your starting commit, mark the task in_progress, and implement its smallest coherent scope.
4. Add meaningful behavior tests alongside implementation. Follow applicable repository review instructions for touched sensitive modules.
5. Run the task checks, including actual native/API/browser behavior when the task changes that path. A mocked seam is labelled as mocked.
6. Create handoffs/<ID>.md from TEMPLATE.md and update STATUS.md truthfully. Include tested source revision, exact commands, results, produced files, limitations and integration notes.
7. Commit and push completed task changes to engineering/checked-design-v1 only when the pasted task prompt authorizes it. Use a focused commit or small coherent series. Never force-push or overwrite unrelated changes. If the remote moved, inspect and integrate safely, then rerun affected checks.
8. Report the resulting commit, task status and next dependency-eligible task. Do not merge main, publish or deploy.

## Completion states

STATUS.md is authoritative for completion state:
- not_started: no implementation attempt recorded;
- in_progress: active work with unfinished checks;
- blocked: a concrete prerequisite/evidence requirement is missing;
- complete: task output and task acceptance checks are demonstrated in this branch;
- superseded: replaced by a documented decision, never silently counted as passed.

A task card's narrower completion is not overall release qualification. Intermediate library tasks may use a documented caller/publisher seam; the end-to-end native/API/browser integration is checked when the relevant consumer exists and again in B13. Never claim a stub verifies a downstream component.

## Dependency and interface discipline

Recommended V1 sequence: B01 → B02 → B03 → B04 → B05 → B07 → B08 → B09 → B10 → B06 → B11 → B12 → B13 → B14.

B05 owns brief preparation service methods and persistence interface; B09 owns durable storage; B11 owns HTTP and B12 owns UI. B07 owns deterministic search with cancellation/progress hooks; B09 owns execution and B10 owns atomic bundles. This keeps dependencies acyclic.

Before starting a downstream task, read its predecessor's interface names, assumptions, fixtures and unresolved issues. An interface change must update its producers, consumers, examples and tests. Preserve existing API contracts, candidate ordering, solver checks and unrelated workflows.

CONTRACT.md defines common behavior. If repository reality requires a change, make a concrete documented adjustment in the same task and update related cards/acceptance checks. Do not silently weaken claims, skip mandatory checks, broaden geometry scope or add new external services.

## Evidence and commits

Use native/runtime commands established in B01. A targeted pattern is python -m pytest with the relevant existing or newly created tests; do not claim a suggested filename exists until implemented. The canonical native verification setup lives in Dockerfile.verify and its documentation, subject to B01 reproduction.

For runtime changes retain an evidence manifest with command, environment/image/dependency versions, implementation SHA, exit code, counts, artifact hashes and limitations. Keep bulky/generated files in appropriate local evidence storage; commit concise reports and source-controlled fixtures. Never commit credentials, .env contents, unrelated logs or private prompts.

To avoid a handoff/commit self-reference, identify the tested implementation commit in the handoff; a subsequent docs-only handoff commit can record it. No test is implied for code edited after the tested revision. If validation cannot finish, record blocked with the exact cause and do not mark complete.

## Main integration and next steps

The branch is eligible for a release integration review only after B14 and its evidence record. Prepare a final change summary and migration/rollback notes at that point. The user decides when to merge or deploy.

E01/E02 preserve geometry-only/screening limits. E03 starts with a domain-reviewed family decision; E04 needs real specimens/equipment/operator evidence. E05 may route existing qualified geometry/screening capabilities after E01/E02; new structural or manufacturing claims additionally depend on E03/E04 as applicable.

## Task-session instruction

The complete prompts in PROMPTS.md authorize that individual task's commits/push to this branch. They do not authorize implementing later tasks automatically. If required domain decisions, credentials, provider budget or physical measurements are unavailable, finish independent work, record the blocker, and request only the missing input.

## Integration with the separate language/framework phase

[Phase 3 integration](phase-3-framework-language/INTEGRATION.md) governs F01–F11. Keep existing B/E interfaces and evidence compatible. The phase adds generic versioned program envelopes through explicit translators rather than weakening existing strict beam models. Its status/P-gates live separately; the parent tasks.json indexes all tasks. Kernel swapping, compilation, migrations and rollback never silently upgrade physical acceptance claims.
