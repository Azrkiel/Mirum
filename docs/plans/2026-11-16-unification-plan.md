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

Written 2026-11-17 by the U6 design pass against `main` = `7742497`. **Draft: the owner reviews this before any adapter starts.** Open questions are in § 5.12.

### 5.1 What the code forces

1. **The checked-design core is beam-only.** `contracts.py`, `policy.py`, `search.py`, `bundles.py`, `jobs.py`, `briefs.py` and `proposer.py` import the single `CAPABILITY` and hard-code beam shapes:
   - `BriefContent.requirements: FixedRequirements` and `REVIEW_FIELDS`;
   - `CheckResult.evidence_shape`: check ids must be beam ids, and a passed check needs `model.step`;
   - the `ArtifactIdentity.path` literal and the eight payloads of `BundleIdentity`;
   - `EngineeringRun`'s ascending-height prefix rule, and `search()` rebuilding candidates with `CandidateSpec.from_brief(brief, h)`.

   No adapter can plug in until these are generalized. That work is one shared PR, U6.0 (§ 5.2). It is the only PR that edits the shared core.
2. **The dragster v2 service is not on `main`.**
   - On `main`, `dragsters/service.generate`, `dragsters/worker.py` and `/dragsters/generate` take only v1 `DragsterSpec`. `DragsterSpecV2` appears in `backend/main.py` only as the D8 `/dragsters/interpret` `base_spec`.
   - `local.generate_result_v2`, the v2 dispatch in `service.generate`/`worker.py` and the `DragsterSpec | DragsterSpecV2` endpoint body exist only in PR #31 (D7).
   - `main` does have the v2 builders (`styles.resolve_build`) and the v2 verifier (`validation_v2.verify_step_v2`).

   The dragster adapter therefore waits for D7's backend half to merge.
3. **CEM identity depends on its source tree.** `cem/provenance.provenance()` hashes every `backend/cem/**/*.py`, `gliders/model/*.py` and three `gliders/*.py` files into `cem_version` and `output_id`. An adapter file placed there would change every CEM output id, so the glider adapter lives in `backend/engineering/`.

### 5.2 U6.0: generalize the core, beam unchanged

**Rule.** The beam v1 models, their validators and their serialized bytes do not change. The test for this is written first, on `main` before any edit: `tests/test_engineering_beam_golden.py` pins the JSON of these beam records, which must stay byte-identical:
- a `BriefDraft`, an `EngineeringBrief`, an `AttemptRecord` and an `EngineeringRun`;
- a bundle manifest;
- the beam entry of `/capabilities`.

**Changes by file.**

- **`capabilities.py`**
  - Add `GeneratorDescriptor` (shape in § 5.3) and three entries.
  - `list_capabilities()` returns the beam first. `frontend/engineering_ui.py` reads `capabilities[0]` and the top-level `review_fields` as the beam's.
  - `get_capability()` looks the id and version up in that tuple.
  - `get_adapter()` resolves the descriptor's `adapter` import string lazily. A missing module raises `UnsupportedCapability('adapter not installed')`, so U6.0 can merge before any adapter exists.

- **`contracts.py`**: add a parallel generator family, discriminated by `schema_version`.
  - `GeneratorBriefContent` (`engineering_generator_brief_v1`):
    - `capability`;
    - `specification`, validated by the descriptor's spec model and stored as `model_dump(mode='json')` with defaults included, so the reviewed brief is exactly the spec that runs;
    - `required_checks`, which must equal `descriptor.required_checks(spec)`.

    One candidate, no mode. `fixed_requirements_sha256 == brief_sha256 == digest(fixed_meaning())`.
  - `EngineeringBrief.content` becomes `BriefContent | GeneratorBriefContent` (discriminator `schema_version`).
  - `GeneratorCandidate`: `candidate_sha256` is the generator's own spec digest (§ 5.4).
  - `GeneratorCheckResult` (`engineering_generator_check_v1`, carries `capability_id`). A passed check needs measurements, limits and the descriptor's `primary_artifact`.
  - `AttemptRecord` and `EngineeringRun` accept either family. A generator run has at most one attempt. Acceptance needs every required check present once and PASSED.
  - Additive literal values:
    - `ArtifactIdentity.path` gains flat names prefixed `generator-`, so none collides with the engineering `report.json`;
    - `Measurement.unit` gains `kg`, `m`, `m4`, `Pa`, `s`, `m_s`;
    - the manifest MIME types gain `model/stl` and `application/zip`.
  - `FieldOrigin` and `ReviewAcknowledgement` also accept `specification.<leaf>` paths. `EngineeringBrief.exact_reviewed_meaning` requires the acknowledged set and the origin set to equal the capability's review fields for that content. For the beam that is still `REVIEW_FIELDS`.

- **`policy.py`**: `assess()` dispatches on candidate type. Beam goes to today's `_validate`, untouched. Generators go to `assess_generator()` in a new `engineering/generator_policy.py` (§ 5.6).

- **`search.py`, `jobs.py`, `bundles.py`**: per-capability selection rule, payload set, report scope, title and Markdown check table. The beam branch is unchanged.

- **Budget**: the engineering `ResourcePolicy` stays the binding budget for every capability, with values unchanged: 420 s overall, 32 MiB per run, 512 MiB of storage, one `native_slot`.

- **`briefs.py`**: `BriefService.validate` dispatches on `payload['capability']['id']`. Generator issues come from the spec model's errors plus the descriptor's `refuse(spec)`.

- **`proposer.py`**: `make_proposer` returns a per-capability dispatcher.
  - The beam's `BriefProposer` is its `ai` entry, unchanged.
  - With the flag off it still returns `None`, builds no provider and reads no key.

- **`api_v1.py`**
  - `DraftCreate` gains `capability: str = 'rectangular_cantilever'` and mode `'rules'`. `'rules'` runs a stdlib proposer: no provider, allowed with the flag off, and only for descriptors that declare one.
  - `/capabilities` adds `review_fields` and `available` to each entry. The top-level keys keep their beam meaning.

- **`backend/main.py`, lifespan only**: `create_runtime(generators=...)` receives getters for `app.state.dragster_runner` and `app.state.regen_runner`, so v1 jobs share the old endpoints' runners and slots. No route changes.

U6.0 needs a security review because the `api_v1` request schema and response change.

### 5.3 Registry entry shape

**Fields.** Each `GeneratorDescriptor` has:
- `id`, `version`, `label`;
- `spec_schema` (import string, resolved lazily);
- `review_fields(spec)` and `required_checks(spec)`;
- `unsupported_features` and `refuse` (import string);
- `numerical` and `runtime_limits`, both tuples of `PolicyReference(name, module, attribute, value, source)`;
- `primary_artifact`, `payloads`, `adapter`, and `proposers` (`{mode: import string}`);
- `engineering_certified: Literal[False]`, `standards: 'not_evaluated'`, `limitations`.

`resource_policy_version` stays `engineering_resources_v1`.

**Stays provider-free.** `capabilities.py` imports only pydantic, `cem.core` and the pure-constant `dragsters/tolerances.py`. A test asserts that importing it loads none of: `google.*`, `cadquery`, `OCP`, `aerosandbox`, `trimesh`, `scipy`, `fastapi`.

**Numerical policy is read-only.** `PolicyReference` values are snapshots for display; no decision reads them. A drift test resolves each `module.attribute` and asserts equality, so a changed constant fails CI instead of drifting silently. Nothing in U6 edits any constant listed below.

**Spec schema, required checks and unsupported features:**

| | `co2_dragster` 2.0.0 | `regen_chamber` 1.0.0 | `glider_cem` 1.0.0 |
|---|---|---|---|
| Spec schema | `dragsters.spec_v2:SPEC_V2_ADAPTER` (`WedgeV2 \| TeardropV2 \| NarrowWaistV2`) | `engineering.regen_spec:RegenChamberSpec` | new `GliderCapabilitySpec` in `capabilities.py` (fields below) |
| Required checks | the 14 below (pinned against a preset run in the first CI test) | the 12 `regen_mesh.verify` checks below, plus `evidence_binding` | the glider set below |
| Unsupported (refused) | `schema_version` 1; styles outside `styles.REGISTRY`; wheels/hardware as STEP parts; fillets, spoilers, decals; performance or competition-legality claims | `voxel_size_mm < 0.4` (the `BrowserSpec` bound); STEP export; gas-dynamics, thermal, structural, manufacturing analysis | thermal/ridge environment; any family not in `cem.glider.model.SUPPORTED_FAMILIES`; shape comparison; flight-test calibration updates; novel shapes; PicoGK builder; manufacturing readiness; certification |

- **`GliderCapabilitySpec`**: `requirements: GliderMissionRequirements`, `calibration_version: Sha256` (required; no implicit default), `selection_rule: Literal['max_objective_value']`.
- **Dragster required checks (14)**:
  - from `verify_step_v2`: `single_valid_solid`, `absolute_bounds`, `symmetry`, `reference_volume`, `pocket_void`, `front_axle_void`, `rear_axle_void`, `pocket_wall`, `front_bore_wall`, `rear_bore_wall`, `spec_revalidation`;
  - from `hardware.cad_checks`: `wheel_clearance_front`, `wheel_clearance_rear`, `body_above_ground`.
- **Regen required checks (12, plus `evidence_binding`)**: `components`, `edge_manifold`, `winding`, `bbox`, `volume`, `channel_count`, `hot_wall`, `cold_wall`, `rib`, `circuit_fidelity`, `circuit_coverage`, `wall_thickness`.
- **Glider required checks**:
  - always: `discovery_archive`, `candidate_selection`, `design_binding`, `closure`, `part_accounting`, `spar_enclosure`, `mass_closure`, `measured_mass_aero`, `validity_envelope`, `spar_structure`, `uncertainty_bands`, `minimum_performance`, `artifact_binding`;
  - plus `foam_core` or `segmented_structure` when `construction` asks for it. `required_checks(spec)` computes the set at draft time and the brief records it, so there is never a "not applicable" required check.

**Numerical policy referenced (never changed by U6):**

- **Dragster**
  - `tolerances.py`: `LOFT_VOLUME_REL_TOL` 0.010, `LOFT_BOUNDS_TOL_MM` 0.05, `WEDGE_BOUNDS_TOL_MM` 0.01, `WALL_DISTANCE_TOL_MM` 0.01, `VOID_TOLERANCE_MM3` 0.001, `STATION_STEP_MM` 10, `CROWN_POINTS` 33.
  - `validation_v2.py`: `CENTROID_Y_TOL_MM`, `AXIS_TOL_MM`, `RADIUS_TOL_MM`, `PLANE_TOL_MM`, `PARALLEL_TOL`, `SAMPLE_STEP_MM`, `PROBE_MARGIN_MM`.
  - Runtime: `ProcessRunner` 30 s / 1536 MiB / 1 MiB output.
- **Regen**
  - `regen_mesh.py`: `VOLUME_TOLERANCE` 0.02, `BBOX_TOLERANCE_VOXELS` 2.0, `STATION_COUNT` 7, `SPECK_VOLUME_MM3` 1e-3, the `WP_*` budgets.
  - `regen_spec.py`: `MAX_SLOPE`, `MAX_BOUNDING_VOXELS`.
  - Runtime: `regen_cli.BUILDER_TIMEOUT_S` 600; `JobRunner` 900 s / 1 GiB output; worker `RLIMIT_AS` 4 GiB.
- **Glider**: the floors in `cem/evidence.validate` are inline literals, so they are recorded with `file:line` sources rather than imported:
  - Reynolds number ≥ 30,000; confidence ≥ 0.8; mass relative error ≤ 0.05;
  - spar stress ≤ allowable × (1 + 1e-5); 200 uncertainty samples;
  - parts 3–16 (32 when segmented); 64 MiB artifacts.

  Their binding is `implementation_sha256` in every glider report, which covers `cem/evidence.py`. `verified_gain` and `manufacturing_ready` stay false.

### 5.4 Finalized brief → existing service call (builders and verifiers unchanged)

| | `candidate_sha256` | Call in `<x>_adapter.evaluate()` | Bytes captured (`generator-*`) |
|---|---|---|---|
| Dragster | `spec_v2.specification_digest_v2(spec)`, which equals `report['spec_sha256']` | `dragsters.service.generate(spec, runner=dragster_runner())` (D7 version). Then read each format with `artifacts.artifact_path(run_id, fmt)`, then `artifacts.discard(run_id)` so no orphan legacy run is left | `spec.json`, `report.json`, `body.step`, `preview.glb` |
| Regen | `regen_spec.specification_digest(spec)` | Re-validate as `regen_web.BrowserSpec` (lazy import; it needs fastapi). Then `regen_runner().submit(spec)` and poll `get(job_id)` until terminal. A `409` (busy) means unavailable | accepted: `cleaned.stl`, `report.json`, `spec.canonical.json`. Rejected: `job['report']` re-encoded exactly as `regen_cli` writes it |
| Glider | `cem.core.digest(requirements.model_dump(mode='json'))`, which is CEM's `requirements_sha256` | `cem.api.jobs.start(dict(kind='discovery', requirements=..., calibration=calibration.load(v)))` and await its task. Select `max(candidates, key=objective_value)` (the `GliderCEM.size` rule). Then `jobs.start(kind='design', ..., elite=...)`, await, `jobs.completed(key, 'design')`. A `429` (busy) means unavailable | `bundle.zip`, `result.json` |

How each adapter handles imports, cancellation and stale results:
- All three import `fastapi`-bound modules (`regen_web`, `cem.api`, `solver_slot`) inside `evaluate()` only. That keeps the mapping code testable on the host.
- **Cancellation.**
  - Dragster: `service.generate` already cleans up its runner.
  - Glider: cancels `jobs.task` and awaits it, shielded, as `Jobs.close` does.
  - Regen: `JobRunner` has no per-job cancel. The v1 job is cancelled and its result discarded, but the PicoGK worker runs on to its own 900 s limit (Q8).
- **Stale or pruned results**: if the old runners prune a finished job before its bytes are read, the hash checks in § 5.6 make the result unavailable, never accepted.

### 5.5 Verifier report → shared evidence contract

- **Dragster**: one `GeneratorCheckResult` per `report['checks'][name]`, all against `body.step`.
  - Measurements are the evidence numbers the check reports (`measured`, `deviation_*`, `residual_body_mm3`, distances), copied verbatim.
  - Limits come from that check's own `threshold`/`tolerance_*`, which the assessor requires to equal the referenced constant.
  - `verify_step_v2` raises on any failed check and writes no rejected report. So a failure reaches v1 only as `GenerationError`, which maps to UNAVAILABLE with no package. The reason names the failing checks only if stderr matches `STEP checks failed: <ids>` and every id is a required check. Otherwise the reason is fixed text.
- **Regen**: one result per check, against `cleaned.stl`.
  - Each check's `state` `'pass'`/`'fail'` maps to PASSED/FAILED.
  - Measurements are the check's `minimum`, `per_station` extremes, `relative_error`, violation counts; limits are its `threshold_mm`/`tolerance`.
  - `evidence_binding` records the acceptance test `JobRunner._run` already applies:
    - `passed`, `geometry_source == 'picogk_builder'`, `engineering_certified is False`;
    - the spec digest and the STL digest match.
- **Glider**: the adapter first calls `cem.evidence.validate` exactly as `Jobs.run` does (it is all-or-nothing). Then it splits the validated report into the named checks, copying values verbatim:
  - `closure.mass_relative_error` and `measured_mass_kg`;
  - `reanalysis.validity_envelope.min_station_reynolds` and `min_confidence`;
  - `measured_spar_stress_pa` and `measured_spar_root_inertia_m4`;
  - uncertainty band P10/P50/P90; per-part counts and hashes.

  Limits are the documented floors. If discovery finds no candidates, `candidate_selection` is FAILED with `count = 0` against `≥ 1`.

**Verdict mapping.** This is the "same verdict" the identity test asserts:

| Old endpoint | `/engineering/v1` run |
|---|---|
| Dragster SSE `complete`, `status: done` / regen job `accepted` / glider design `status: screened` | `completed` / `accepted`; full package |
| Regen job `rejected` (exit 2) / glider design `status: rejected` / no discovery candidate | `completed` / `rejected`; diagnostic package only (brief, attempts, reports, generator report), no geometry |
| Dragster SSE `status: error` / regen `unavailable` / glider job `failed` / a limit or deadline exceeded | `unavailable` / `not_evaluated`; no package |

### 5.6 A missing or failed check blocks the package

`assess_generator()` returns `available=False` (job ends unavailable, no package) unless all of these hold:
- the report's checks equal the brief's `required_checks` exactly: no missing, extra or duplicate ids;
- every check state is pass or fail;
- the report's own pass flag agrees with the checks;
- the report's spec digest equals `candidate_sha256`;
- every captured byte string hashes to the digest the generator report states:
  - dragster: `step_sha256`, `spec_sha256`, `preview.glb_sha256`;
  - regen: `stl_sha256`;
  - glider: `bundle_sha256`, plus each part's `sha256` read from inside the zip.

Then:
- **Any FAILED check**: outcome rejected; `bundles.publish_diagnostics` only.
- **Accepted**: requires every check PASSED, enforced by `GeneratorCheckResult`, `AttemptRecord` and `EngineeringRun`.
- **At publish time**, `bundles.publish` reassesses every attempt, as it does for the beam.

Each adapter PR adds three fault-injection tests: a dropped check gives unavailable with no package directory; one flipped check gives rejected with only the diagnostic payloads; one changed byte gives unavailable.

### 5.7 An unsupported request is refused, never approximated

Refusal happens at these points:
1. **Draft.** The strict spec model (`extra='forbid'`) plus the descriptor's `refuse(spec)` produce `InputIssue(state='unsupported')`. `EngineeringBrief.from_draft` then refuses to finalize: "Unresolved clarification prevents final review". Cases:
   - glider: thermal or ridge; any family outside `SUPPORTED_FAMILIES`. `model.configuration` would silently drop such a family, so v1 refuses first;
   - regen: voxel size below 0.4;
   - dragster: `schema_version` 1.
2. **Proposers never fill a value the user did not give.** Preset or default values are marked `origin: 'policy'` and must be acknowledged in review. Extraction's `unsupported` list becomes unsupported issues.
3. **Job time.** `adapter.candidates()` re-validates. Unknown ids or versions raise `UnsupportedCapability`, which the API returns as 422.
4. **Limits.** A run that exceeds 420 s or 32 MiB is unavailable, never truncated or downsampled.

### 5.8 D8 extraction as the dragster proposer

New file `engineering/dragster_proposer.py`, declared in the descriptor's `proposers`. Both modes feed the same flow: draft → `PATCH` edits → `POST /briefs` review → `POST /jobs`. There is no second review UI.

- **`rules` mode** (stdlib, no model, allowed with the flag off; U7's router will call it):
  - Not a dragster per `dragsters.intent.detect(prompt)`: unsupported.
  - Pick the style with `extraction._styles(prompt)` (a pure, private helper; a test pins its behaviour). No style or several styles gives a clarification, with the same wording as `interpret`.
  - Otherwise the draft is `styles.load_preset(style)`, with every field `origin: 'policy'`, reference `preset:<style>`.
  - If the prompt contains any digit, add a blocking clarification: "Stated values are not read without AI drafts; enter them in the form." Stated values are never silently ignored.
- **`ai` mode** (built only when `MIRUM_ENGINEERING_AI_DRAFTS` is on):
  - Reserve one call in the engineering `AiCallBudget`, then call `dragsters.extraction.interpret(prompt, base_spec)` unchanged. Its own `DRAGSTER_INTERPRET_DAILY_CAP` also applies.
  - Status mapping: `resolved` becomes the draft; `needs_clarification` becomes clarification issues; `unsupported` becomes unsupported issues; `ExtractionUnavailable` becomes the existing 503 report.
  - Provenance mapping: `supplied` → `ai_proposed`, with reference `request sha; dragster_interpret_v1; model; unverified proposal`; `preset:<style>` → `policy`; `base_spec` → `user_supplied`.
  - `ProposalAudit` gains the literals `dragster_interpret_v1` and `dragster_patch_v1`.
  - Gap: `interpret` swallows raw replies, so this mode cannot write U4's validation failures to the ledger (Q5).

### 5.9 Identity rule and how each test is built

**Rule.** For the same spec, the old endpoint and `/engineering/v1` give the same verdict (§ 5.5 table) and the same artifact hashes.

**How every identity test works:**
- Both paths run in the same test process, on the same runner, in the same CI job.
- They are never compared with a hash stored from another machine, so runner-to-runner float differences cancel out.
- The v1 path goes form draft → review → finalize → job → download.
- Each test's exclusion list is a fixed set, reviewed in its PR, and never contains the verdict, check states, failures, measurements or spec digests.
- No `pytest.approx` is used on any identity or receipt value.

**Per generator:**
- **Dragster** (CI native gate, three presets plus one boundary spec). The old path runs twice through `/dragsters/generate`, as `test_dragster_api.py` drives it.
  - Compare `spec.json` and `preview.glb` bytes, and `report.json` canonical JSON minus `revision_id` (`service.generate` sets it to a random run id).
  - `body.step` must be byte-equal if the two old runs agree. If they differ (OCCT writes a timestamp in the STEP header), compare with only the `FILE_NAME` header line masked, and require the verifier's `volume_mm3`, `bounds_mm` and checks to be equal exactly (Q7).
- **Regen** (CI native gate; there is no PicoGK in CI).
  - Accepted path: both runs use the same fixture worker command `test_regen_web.py` uses.
  - Rejected path: a fake `REGEN_BUILDER` writes a fixed STL that real `regen_mesh.verify` rejects.
  - Compare `report.json` or the rejected report, `cleaned.stl` and `spec.canonical.json` bytes. Exclude only the job id, URLs, `updated` and `message`.
  - A real-builder identity run is manual on the builder image (Q6).
- **Glider** (CI native gate).
  - Spec: the `test_cem_pipeline.example()` mission with `families: ['conventional']`, `budget_evals: 8`, `top_k: 1`.
  - Old path: `/cem/glider/discover` → pick the elite by the same rule → `/design` → `/bundle`.
  - Compare discovery `result.json`, design `result.json` and `bundle.zip` bytes, and per-part STL hashes. Exclude job ids only.
  - If a stored digest is ever added, guard it like `test_cem_window_coverage.py`: strict only when `_runtime_matches(recorded)` holds, otherwise skip that one assertion with a reason. The in-run old-versus-v1 comparison is never skipped, and no receipt or floor is loosened.

**Skips.** Identity tests skip only when `os.name != 'posix'`, never because a package is missing. A missing CI dependency then fails the test instead of hiding it.

### 5.10 Order, file ownership, parallelism

| PR | After | May touch | Security review |
|---|---|---|---|
| U6.0 core | U1–U4 merged | `engineering/{capabilities,contracts,policy,search,bundles,jobs,briefs,proposer,api_v1}.py`, new `engineering/generator_policy.py`, `backend/main.py` (lifespan only), `tests/test_engineering_{beam_golden,generator_contracts,capabilities,api_v1}.py` | yes (`api_v1` schema) |
| U6.1 regen | U6.0 | new `engineering/regen_adapter.py`, `tests/test_engineering_regen_adapter.py`; own block in `capabilities.py` only if the check list needs correcting | no (no endpoint or key change) |
| U6.2 glider (parallel with U6.1) | U6.0 | new `engineering/glider_adapter.py`, `tests/test_engineering_glider_adapter.py`; own block in `capabilities.py` as above. Nothing under `backend/cem/` or `backend/gliders/` | no |
| U6.3 dragster | U6.0 and the D7 backend | new `engineering/{dragster_adapter,dragster_proposer}.py`, `tests/test_engineering_dragster_{adapter,proposer}.py`; own block in `capabilities.py` | yes (AI mode reaches the `GEMINI_API_KEY_HEAVY` path through `extraction`) |

- **Order.**
  - Regen goes first: flat spec, a three-way verdict already, and a CI identity test that needs no native CAD.
  - Glider runs alongside it: disjoint files and a heavier runtime.
  - Dragster goes last: blocked on D7, and it adds the proposer.
- **Parallel limit.** At most U6.1 and U6.2 run in parallel. Their only shared file is `capabilities.py`, in separate blocks; the merge train resolves that.
- **Excluded from all adapter PRs:** `backend/main.py`, `api_v1.py`, `frontend/`, CI workflows, `Dockerfile.verify`.
- **Test file naming.** Tests are named `test_engineering_*`, not `test_regen_*`: `Dockerfile.verify` ignores `test_regen_*.py`, while `test_engineering_*` runs in the native gate without a workflow edit.
- **Frontend.** No frontend edits in U6. The v1 UI for the three generators is U7's job, and the beam UI keeps working because the beam stays `capabilities[0]`.

### 5.11 Test plan: host versus CI

**Host venv** (`C:\Users\jiang\Mirum-ui\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider <files>`, one run at a time):
- What the venv has: pytest, pydantic, numpy, scipy. It has no fastapi; CadQuery is installed but fails to import (VTK), and there is no POSIX `killpg` or `resource`. `engineering.store.SQLiteStore` refuses non-Linux hosts.
- What runs there:
  - U6.0: beam golden, generator contracts, capabilities import-hygiene and drift (for pure modules), briefs, search, policy.
  - Adapters: descriptor, refusal, report→evidence mapping from recorded report fixtures, `assess_generator` fault injection with fake `evaluate` results.
  - Proposer: `rules` mode, and `ai` mode with `interpret` monkeypatched. Assert that with the flag off no provider is built and no key is read.
- No float or hash assertions run on the host: its numpy differs from CI's.

**CI only:**
- `test_engineering_api_v1.py` (TestClient).
- Every identity test.
- The regen `JobRunner` and fake-builder tests.
- Glider discovery and design, which need aerosandbox, trimesh and the `analysis` package.
- Dragster CadQuery builds.
- Drift tests for modules that import CadQuery (`validation_v2`).

**Before each push:** gate (a). The PR body lists host counts, CI counts, and the identity tests as passed, not skipped.

### 5.12 Decisions (owner delegated them to the lead, 2026-11-17)

The owner told the lead to finish the work and decide these. No decision changes a limit, tolerance, floor or receipt.

| Q | Decision |
| --- | --- |
| 1 | **Split D7's backend half** into PR `u6/d7-backend` (v2 dispatch in `service.py`, `worker.py`, `local.generate_result_v2`, `cli.py`, the `/dragsters/generate` body type). CI tests it now; #31 keeps the UI half and the Docker steps. |
| 2 | **Accept.** Runs past 420 s / 32 MiB are `unavailable` through v1; the old endpoints still serve them. No budget change. |
| 3 | **Automatic** selection by the existing `GliderCEM.size` rule, recorded in the reviewed brief. |
| 4 | **Yes**, U6.0 lands first. |
| 5 | **Accept the gap** for now; AI drafts are off. Revisit with U5. |
| 6 | **Fixtures in CI only**; the manual real-builder run waits for Docker on the laptop. |
| 7 | **Masked `FILE_NAME` header** comparison accepted, with exact volume, bounds and check equality. |
| 8 | **Accept.** |
| 9 | **Refuse** v1 dragster specs through v1. |

**Ownership change from § 5.10.** U6.0 does **not** edit `backend/main.py`, so it can run in parallel with `u6/d7-backend`, which owns the dragster endpoint body in `main.py`. The `create_runtime(generators=...)` lifespan wiring moves to the first adapter that needs a shared runner (U6.1 regen), as a lifespan-only `main.py` edit made after `u6/d7-backend` merges.

Original questions, kept for the record:

1. **Dragster is blocked on D7.** The v2 service isn't on `main`. Wait for U1 (Docker-bound), or split D7's backend half (`service.py`, `worker.py`, `local.generate_result_v2`, the endpoint body type) into its own PR that CI can test now? Recommended: split.
2. **Limits versus generator runtimes.** The v1 job controller caps a run at 420 s and 32 MiB. Regen's existing limits are 900 s and 1 GiB of output; CEM's are 1800 s per stage and 64 MiB bundles. U6 changes none of these, so long or large runs come back "unavailable" through v1 while the old endpoints still serve them. Accept that, or approve a separate per-capability budget change later?
3. **Glider elite selection.** Recommended: v1 selects automatically using the existing `GliderCEM.size` rule (highest objective value), recorded in the reviewed brief. Alternative: a second reviewed brief that pins `elite_id` after discovery.
4. **Extra core PR.** Approve U6.0 as a PR in addition to "one adapter per PR"?
5. **D8 failure capture.** `extraction.interpret` swallows raw replies, so the dragster AI mode can't record validation failures in the ledger as U4 requires for the beam. Accept the gap, or allow a small additive change to `extraction.py`?
6. **Regen real-builder identity.** CI has no PicoGK builder, so identity is tested with fixtures only. Is one manual run on the builder image acceptable?
7. **Dragster STEP bytes.** If two old runs give different STEP bytes (an OCCT header timestamp), is the masked-header comparison acceptable? Exact byte equality would need a builder change, which is out of scope.
8. **Regen cancellation.** Cancelling a v1 regen job leaves the PicoGK worker running to its 900 s limit, because `JobRunner` has no per-job cancel. Acceptable for U6?
9. **Dragster v1 specs.** Proposed: refuse them through v1 (they stay on the old endpoint) rather than offering a reviewed `upgrade_v1`. Agree?

## 6. Status

| Stream | State | PR |
| --- | --- | --- |
| U1 D7 | draft, waits for Docker steps on the owner's laptop | #31 |
| U2 D8 backend | **merged** 2026-11-17 (security review fixed; D8 UI waits for U1) | #37 |
| U3 regen wiring | **merged** 2026-11-17 | #36 |
| U4 checked design + flag | **merged** 2026-11-17 (two security rounds) | #38 |
| — client key behind proxy | **merged** 2026-11-17 (limits were one shared bucket) | #39 |
| U5 B13 | **owner approved** 2026-11-17 (≤ 60 calls + one diagnostic case); evaluator evidence fix first; runs when Docker has ≥ 5 GB free | #44 |
| U6 adapters | § 5 design done; § 5.12 decided by the lead (owner delegated). Next: U6.0 core ∥ D7 backend split, then regen ∥ glider, then dragster. Correction: the dragster v2 *service* is not on `main` (only in #31), so the dragster adapter waits for D7's backend | this PR |
| U7 routing + workspace | waits for U6 | |

Handoff for the next session: [`.claude/handoffs/2026-11-17-1130.md`](../../.claude/handoffs/2026-11-17-1130.md).
