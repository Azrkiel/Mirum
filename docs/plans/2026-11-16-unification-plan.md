# Joining the deterministic solvers and text-to-CAD

Date: 2026-11-16. Owner decisions recorded here were given in the lead session on that date.
This plan sits on top of [the remaining-work plan](2026-11-16-remaining-work-plan.md); its
test tiers (§ 4) and stop points (§ 3) apply unchanged.

## 1. Goal

One prompt box. A prompt that matches a registered deterministic capability (beam, CO2
dragster, glider, regen chamber) becomes a typed brief, the user reviews that exact brief,
and a no-LLM builder and verifier produce the result, labelled **checked design**. Any other
prompt goes through the existing LLM CadQuery pipeline, labelled **unverified geometry**.

## 2. Starting point (`main` = `beabc42`)

| Piece | Where | State |
| --- | --- | --- |
| LLM pipeline `/generate` | `backend/main.py`, `backend/agents.py` | on `main` |
| Beam solver `/engineering/generate` | `backend/engineering/` | on `main` |
| Dragster v1 + v2 builders | `backend/dragsters/` | on `main`; v2 service/UI in draft PR #31 |
| Glider CEM | `backend/cem/` | screening on `main`; verification and families in PR #33 |
| Regen chamber | `backend/engineering/regen_*` | standalone app on `main`; main-app wiring on `feat/deterministic-regen-nozzle` |
| Reviewed-brief flow, capability registry, evidence gate, job store | `engineering/checked-design-v1` (`e2bd3c2`) | B01–B12 done; B13 live qualification blocked; B14 not started |
| Dragster text routing (D8) | uncommitted in worktree `C:\Users\jiang\Mirum-d8` | partial |

Owner decisions:

1. The checked-design AI draft mode may land on `main` **behind a flag, default off**, before
   B13 passes.
2. The May `/jobs` branch is dropped (tag `archive/mirum-backend-throughput-finish`). The
   checked-design job store is the only job system.
3. Live Gemini calls, deploys and merges still need the owner's yes each time, except where a
   workstream below says otherwise.

## 3. Rules for every agent

- Work only in the worktree and branch named for your workstream. Never commit to `main`,
  never merge a PR, never force-push, never deploy, never call a live model.
- **No Docker on the laptop.** It has about 3 GB free. Run pure-Python tests of the files you
  touch on the host (`python -m pytest -q -p no:cacheprovider <files>`), one run at a time.
  Native CAD tests run in CI on your pushed branch. Do not run a full suite locally.
- Do not edit `backend/compiler.py`, `backend/script_policy.py`, sandbox allowlists, any
  tolerance, floor or limit, or another workstream's files. If you need to, stop and report.
- Windows: commit text files with LF. Check with `git diff --cached --check` and
  `git ls-files --eol <file>`.
- Run gate (a) before pushing: `python backend/tests/test_prompts_schema.py`,
  `python backend/tests/test_runner_filters.py`, `python backend/tests/test_md_report.py`.
- End commit messages with the `Co-Authored-By` line the harness gives you. PR bodies say what
  was run, with counts, and what was not run.
- Report: branch, head SHA, PR number, commands with results, each exit criterion pass/fail,
  deviations, open questions. Say plainly what you could not verify.

## 4. Workstreams

Merge order is fixed because every stream edits the workflow selector in `frontend/app.py`
and the route wiring in `backend/main.py`: **U1 → U2 → U3 → U4 → U5 → U6 → U7**. Streams
U1–U4 are built in parallel; each later one rebases on `main` after the one before it merges.

### U1. Dragster D7, finish draft PR #31

Branch `feat/co2-dragster-d7-service-ui`, worktree `C:\Users\jiang\Mirum-d7`.
Follow [remaining-work plan § 6.1](2026-11-16-remaining-work-plan.md). Step 1 (merge `main`)
can be done now. Steps 2 and 3 (frontend image import, browser screenshots) need Docker and
5 GB free RAM, so they wait for the owner's laptop to be free.

Exit: D7 subplan boxes ticked, PR out of draft.

### U2. Dragster D8 backend

Branch `feat/co2-dragster-d8-text`, worktree `C:\Users\jiang\Mirum-d8`.
The worktree holds uncommitted partial work: `backend/dragsters/intent.py`, `extraction.py`,
`eval/`, two test files, and edits to `backend/main.py`, `backend/strategies.py` and
`docs/dragsters/integrated-workflow.md`. Review it against
[D8 subplan](co2-dragster/D8-text-routing.md) before building on it; keep what meets the
subplan, rewrite what does not.

1. Commit the existing work as a first WIP commit so nothing is lost. Merge `origin/main`.
2. Finish [remaining-work plan § 6.2](2026-11-16-remaining-work-plan.md) steps 1–4.
   `eval/run_live.py` is written and **not run**.
3. Push, open a draft PR into `main`. CI is the native run.

The extraction output must be a draft `spec_v2` plus per-field origin, not a generated
result: U6 will feed it into the shared reviewed-brief flow. Do not build a second review UI.

Exit: D8 subplan exit criteria except UI and live sample; security review of the endpoint
and key path attached (the lead arranges it).

### U3. Regen chamber in the main app

Branch `feat/deterministic-regen-nozzle`, worktree `C:\Users\jiang\Mirum-regen-nozzle`.
One commit (`e6a0daf`), 47 commits behind `main`. Merge `origin/main` and resolve the two
conflicts (`backend/main.py`, `frontend/app.py`): keep every route and workflow option from
both sides. Re-run the focused host command recorded in
`docs/plans/2026-11-05-regen-nozzle-integration.md`. Push and open a PR.

Exit: PR mergeable, CI 4/4, regen workflow option present alongside the existing four.

### U4. Checked design onto `main`, AI drafts behind a flag

Branch `engineering/checked-design-v1`, worktree `C:\Users\jiang\Mirum-checked-design-v1`.

1. Merge `origin/main` (clean as of `b308034`).
2. Add `MIRUM_ENGINEERING_AI_DRAFTS` (default off, read once in `backend/config.py`). When
   off: `POST /engineering/v1/drafts` in AI mode returns a clear "not enabled" error without
   constructing a provider or reading a key; `GET /engineering/v1/capabilities` reports the
   mode as unavailable; the "Checked beam" UI shows form mode only. When on: behaviour is
   exactly today's. Tests for both states, written first.
3. Make a failed proposal validation keep what B13 lost: the error locations and types, and
   the raw reply, written to the proposer's audit ledger (never to logs, never returned to
   the client).
4. Commit `docs/plans/2026-11-12-finish-b13-b14.md` (currently untracked) with a note that
   steps needing provider calls wait for the owner.
5. State in README and `STATUS.md`: typed path on `main`; AI draft mode off by default and
   not live-qualified.
6. Push, open a PR. `backend/main.py` changes, so a security review is required (the lead
   arranges it).

Not in scope: any live call, the 30-case evaluation, B14 release images.

Exit: PR mergeable, CI 4/4, flag tests pass in both states, no behaviour change with the
flag on.

### U5. B13 live qualification (owner approval needed)

After U4 merges. One diagnostic call with the U4 capture in place, fix the schema mismatch it
shows, then the 30-case run (at most 60 calls) from the finish-B13 plan. Spends the owner's
Gemini key. Not started without a yes.

### U6. Register dragster, glider and regen as capabilities

After U1–U4 merge. Design first (one architect pass, written into this file as § 5), then
one adapter per PR:

- `backend/engineering/capabilities.py` gains an entry per generator: spec schema, required
  checks, unsupported features, numerical policy. The registry stays provider-free.
- Each adapter maps a finalized brief to the generator's existing service call and maps its
  verifier report into the shared evidence contract. Builders and verifiers are not changed.
- D8's extraction becomes the dragster proposer behind the same draft → review → job flow.
- Acceptance per adapter: the same spec gives the same verdict and the same artifact hashes
  through the old endpoint and through `/engineering/v1`; a missing or failed check blocks
  the package; an unsupported request is refused, never approximated.

### U7. Capability routing in `/generate`, then the workspace UI

- An early step in `_pipeline` asks the registry whether the prompt matches a capability
  (stdlib intent rules first, as in D8; no model call to route). A match emits a `route`
  event carrying a draft brief; no match continues to the LLM pipeline unchanged.
- Every result carries `assurance: "checked_design" | "unverified_geometry"`; the UI shows it.
- `feat/professional-workspace` is redone on top of the final selector and merged last.
- Acceptance: the existing 82-prompt evaluation set routes exactly as before except the
  prompts that name a registered capability; a routed prompt never reaches the Machinist.

## 5. Capability adapter design

To be written by the U6 design pass.

## 6. Status

| Stream | State | PR |
| --- | --- | --- |
| U1 D7 | draft, waits for Docker steps on the owner's laptop | #31 |
| U2 D8 backend | **merged** 2026-11-17 (security review fixed; D8 UI waits for U1) | #37 |
| U3 regen wiring | **merged** 2026-11-17 | #36 |
| U4 checked design + flag | **merged** 2026-11-17 (two security rounds) | #38 |
| — client key behind proxy | **merged** 2026-11-17 (limits were one shared bucket) | #39 |
| U5 B13 | unblocked by U4; waits for owner approval (spends Gemini key) | |
| U6 adapters | **ready to start** on the code; D7 not merged, so the dragster adapter targets the v2 service on `main` | |
| U7 routing + workspace | waits for U6 | |

Handoff for the next session: [`.claude/handoffs/2026-11-17-1130.md`](../../.claude/handoffs/2026-11-17-1130.md).
