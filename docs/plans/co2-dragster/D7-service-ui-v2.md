# D7 — Service, worker, API and UI for v2

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 and § 6 override this file.
Agent: general-purpose (full stack). Wave 3. Depends on: D3, D4, D5, D6 merged.
Effort: 1.5–2 days. Branch: `feat/co2-dragster-d7-service-ui`.
**Security review required** (touches `backend/main.py` endpoints) — master invariant 11.

## Goal

Make all three styles usable end-to-end in the main app and API, keep v1 working byte-for-byte in
behaviour, and show the new evidence (checks, hardware, mass) clearly.

## Read first

- `backend/dragsters/service.py`, `worker.py`, `api.py`, `local.py`, `cli.py`
- `backend/main.py` around `/dragsters/generate` (grep `dragster`), `_safe_error`
- `frontend/dragster_ui.py`, `frontend/dragster_state.py`, `frontend/app.py:963`
- Tests: `backend/tests/test_dragster_api.py`, `test_dragster_integrated.py`, `frontend/test_dragster_*.py`
- `frontend/Dockerfile`, `deploy/fly/frontend.Dockerfile` (they COPY individual dragster files today)
- `.claude/skills/mirum-security-review/SKILL.md`

## Owns / may edit

- `backend/dragsters/local.py` — add `generate_result_v2(spec, root)`; leave `generate_result` unchanged
- `backend/dragsters/worker.py`, `service.py`, `api.py` — dispatch v1/v2
- `backend/dragsters/cli.py` — accept v2 (`parse_any`); v1 output contract unchanged
- `backend/dragsters/app.py` (standalone) — style selector, minimal
- `backend/main.py` — only the dragster endpoints block
- `frontend/dragster_ui.py`, `frontend/dragster_state.py`, new `frontend/dragster_fields.py` (labels/help/grouping)
- `frontend/Dockerfile`, `deploy/fly/frontend.Dockerfile` — copy the dragster package
- `docs/dragsters/integrated-workflow.md` — API/UI sections
- Tests: new `backend/tests/test_dragster_v2_service.py`, `backend/tests/test_dragster_v2_api.py`, `frontend/test_dragster_v2_ui.py`; existing dragster tests may only gain cases, never lose/relax.

Must not touch: spec/profiles/kernel/styles/validation/hardware (report gaps to the lead), sandbox, other workflows.

## Backend tasks

1. **`local.generate_result_v2(spec, root)`** — same staging/atomic-rename structure as `generate_result`: `resolve_build(spec.style)(spec)` → STEP export → `verify_step_v2` → `export_preview` → write `spec.json` (`canonical_spec_v2`), `report.json` (with `preview` block as v1) → rename to `dragster-<uuid>`. Files: `body.step`, `spec.json`, `report.json`, `preview.glb`.
2. **worker.py** — read ≤ 16 KiB stdin, `parse_any`; v1 → `generate_result`, v2 → `generate_result_v2`.
3. **service.py** — accept either model; canonicalise with the matching function; evidence/digest checks unchanged; `coordinate_frame` unchanged. Runner limits unchanged.
4. **api.py** — progress message names the style (`'Building and checking the teardrop body…'`).
5. **main.py**
   - `POST /dragsters/generate`: body type `DragsterSpec | DragsterSpecV2` (strict). Unknown/invalid → 422 with details (keep existing 422 behaviours and tests). Rate limit unchanged (5/min).
   - `GET /dragsters/presets` → `{styles: [{name, label, preset}]}` from the registry (no CAD import). 30/min.
   - No other endpoint changes.
6. **Tests (real worker, verify image)**: generate each preset via the API test client (SSE complete `done`, four digests verify, report `schema_version == 2`, `generator_version` per style, all check ids present and passing); v1 example still returns a v1 report identical in keys to before; invalid v2 → 422 (extra key, wrong style for fields, containment failure); presets endpoint; `generate` makes zero provider calls (patch `google.generativeai` to raise on import/use); timing: each preset end-to-end ≤ 12 s median (record actual; assert ≤ 25 s in CI).

## Frontend tasks

1. **Style selector**: radio *Low wedge / Smooth teardrop / Narrow waist + raised rear housing*. Switching style loads that preset into the draft (confirm if the draft has unsaved edits: show a "Discard edits and load preset" button rather than silently replacing).
2. **Grouped editor** from `dragster_fields.py` (one source of labels, units, help, group): *Body & shape* (style fields), *Axles & wheels*, *Cartridge pocket*, *Stock block* (checkbox → fields or `null`), *Material* (checkbox → name + density or `null`). Help text states frame conventions (heights above body bottom; axle Z = wheel diameter/2; ground = wheel contact).
3. **Draft validation** in the frontend with `spec_v2` (pure); show the first error with field names; Generate disabled while invalid.
4. **JSON load**: v2 → load; v1 → `upgrade_v1` with an info banner listing provenance (`wheels.width_mm: preset default 6 mm` etc.); upgrade failure → message saying "this v1 file still generates as v1" and offer **Generate as v1** (sends the v1 JSON unchanged).
5. **Result panel**: checks table (id, pass/fail, measured, threshold); hardware (wheelbase, track widths, stock margins); mass (`— (no density supplied)` when null); viewer = existing `render_preview` plus an optional **Show wheel envelopes** toggle drawing the four envelopes from `hardware.wheel_envelopes(accepted spec)` as translucent cylinders labelled "envelope — not in STEP".
6. **State**: `DragsterState` semantics unchanged (attempt tokens, accept only matching attempt, digests); draft/result mismatch label compares canonical JSON of either version.
7. **Docker**: both frontend Dockerfiles copy `backend/dragsters/` as a directory (plus `backend/__init__.py` if needed for the import path). Verify the frontend image imports `dragster_ui` without CadQuery: `docker build -f frontend/Dockerfile -t mirum-frontend-d7 . && docker run --rm mirum-frontend-d7 python -c "import dragster_ui"`.
8. **Tests** (`frontend/test_dragster_v2_ui.py`, Streamlit AppTest + mocked backend like existing tests): style switch loads preset; edit-then-switch shows discard button; invalid draft disables Generate; v1 upload → banner + upgraded fields; v1 upgrade failure → Generate-as-v1 path; accepted result survives a failed attempt; mass/hardware rendering; wheel-envelope toggle adds traces.

## Browser check

Run backend + frontend locally (integrated-workflow.md § Setup, or `docker compose up --build`) and
with a real browser: generate each preset, toggle wheel envelopes, load v1 example, cancel an
attempt, make an invalid edit. Save screenshots under `docs/plans/co2-dragster/evidence/d7-*.png`
and note any JS errors.

## Exit criteria

- [ ] All new and existing dragster tests pass in the verify image, zero skips; CI green on the PR.
- [ ] Frontend image imports without CadQuery.
- [ ] Browser check screenshots for all three styles.
- [ ] `mirum-security-reviewer` report attached to the PR with no unaddressed CRITICAL/HIGH.
- [ ] Status cell updated.
