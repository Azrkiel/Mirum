# D8 — Text routing, extraction and refinement

Parent: [master plan](00-master-plan.md) — read it in full first; § 5 (esp. invariants 2, 4, 11) and § 6.5 override this file.
Agent: general-purpose (backend + LLM integration). Wave 3.
Depends on: D1 merged for the backend part (tasks 1–4); D7 merged for the UI part (task 5).
Effort: 1.5 days. Branch: `feat/co2-dragster-d8-text` (rebase on main after D7 merges, before task 5).
**Security review required** (new endpoint, provider key use, `_pipeline` change).

## Goal

A user can type "CO₂ dragster, teardrop, 240 mm long, rear wheels 36 mm" in Text-to-CAD or in
the dragster page and get a **resolved, reviewable spec** in the editor — never geometry from the
LLM. Follow-ups ("make it 10 mm longer", "rear wheels 1.5 in") patch the current spec. Pinewood
derby cars, full-size race cars and standalone cartridge/nozzle requests keep their current paths.

## Read first

- `backend/cem/extraction.py` (pattern: flat response schema, temperature 0, 30 s timeout, size caps, clarifications)
- `backend/main.py`: `_event` (line ~253), `_pipeline` (line ~671), dragster endpoints, `_safe_error`
- `frontend/app.py` SSE handling (~lines 280–330: event validation and `step` handling)
- `backend/strategies.py:913` (`rule_co2_dragster`) and its keyword list (~line 1273)
- `backend/tests/test_classifier.py::test_c_co2_dragster`
- D1 subplan (field paths and ranges), D7 subplan (UI structure)

## Owns / may edit

- New: `backend/dragsters/intent.py`, `backend/dragsters/extraction.py`, `backend/dragsters/eval/prompts.json`, `backend/dragsters/eval/run_live.py`
- `backend/main.py`: add `POST /dragsters/interpret`; add the early route in `_pipeline` (≤ 10 lines)
- `backend/strategies.py`: only the `rule_co2_dragster` keyword list
- `frontend/app.py`: only the Text-to-CAD SSE handler for the new `route` step + workflow switch
- `frontend/dragster_ui.py`: the "Describe or refine" panel
- Tests: `backend/tests/test_dragster_intent.py`, `test_dragster_extraction.py`, `test_dragster_interpret_api.py`, `frontend/test_dragster_text_ui.py`
- `docs/dragsters/integrated-workflow.md`: a "Text input" section

Do **not** edit `backend/eval_harness/prompts.json` (its schema gate is separate); dragster prompts live in `backend/dragsters/eval/`.

## 1. Intent detector — `intent.py` (stdlib only, no LLM)

`detect(prompt: str) -> Intent(is_dragster: bool, reason: str)`.
Normalise: lowercase, `co₂`/`co 2`/`carbon dioxide`/`co2-powered` → `co2`.
- **Positive** if `co2` occurs within 4 words of any of `dragster|car|racer|racecar|race car|vehicle`, or `dragster` occurs at all — unless a negative applies.
- **Negative** (override) if any of: `pinewood`, `derby` (without `co2`), `full-size|full size|life-size|1:1|real car|top fuel|nhra|funny car`, or the prompt is about only a cartridge/holder/nozzle (`co2 cartridge|cartridge holder|nozzle|canister`) with no vehicle word.
Test table (≥ 30 rows, both directions), including: "CO2 dragster body", "co₂ car for tech class", "carbon dioxide powered racer", "balsa dragster wedge", "design a pinewood derby car", "derby car", "a full size top fuel dragster chassis", "CO2 cartridge holder", "rocket nozzle", "race car spoiler" (negative: no co2/dragster), "CO2-powered dragster with aerodynamic profile" (positive — must not go to wings).

## 2. Pipeline route

At the very start of `_pipeline`, before REA/planner:
```python
if intent.detect(prompt).is_dragster:
    yield 'data: ' + json.dumps({'step': 'route', 'status': 'done', 'workflow': 'co2_dragster',
                                 'detail': 'Routed to the deterministic CO2 dragster workflow'}) + '\n\n'
    return
```
No provider call happens for routed prompts (test it). Narrow `rule_co2_dragster` keywords to
`["co2 dragster", "co₂ dragster", "co2 car", "carbon dioxide dragster"]` (remove `"derby car"`, `"race car"`). The classifier keeps Domain C text unchanged; `test_c_co2_dragster` must still pass.

## 3. Extraction — `extraction.py`

`async def interpret(prompt: str, base_spec: DragsterSpecV2 | None) -> dict` returning master § 6.5 shapes.

1. **Style (deterministic first):** `wedge` → wedge; `teardrop|tear drop|tear-drop` → teardrop; `narrow waist|waisted|coke bottle|raised housing|raised rear` → narrow_waist. Several styles named → `needs_clarification`. None named: use `base_spec.style` if given, else `needs_clarification` ("Which body style: low wedge, smooth teardrop, or narrow waist with raised rear housing?").
2. **LLM** (`GEMINI_MODEL_LITE`, temperature 0, `max_output_tokens` 2048, 30 s timeout, response ≤ 16 KiB). Flat schema only (no anyOf/exclusiveMinimum/$ref — see PR #8):
   ```json
   {"changes": [{"field": "string", "op": "set|add|scale", "value": 0, "unit": "mm|cm|in|ratio|g/cm3|kg/m3|none"}],
    "unsupported": ["string"], "questions": ["string"]}
   ```
   System instruction: map only **explicitly stated** quantities to the allowlisted dotted field paths for the chosen style (list them with units and meanings); never compute, never invent a value for a qualitative word ("wider" → a question asking how much); requests outside the parameter set (fillets, spoilers, decals, "fastest", wheels as parts, competition legality) go to `unsupported`; never output code.
3. **Server-side, deterministic:** reject unknown field paths, ops not allowed for a field (`scale` only for lengths/widths/heights; `ratio` unit only for `*_frac`/`crown_exponent`), > 20 changes, non-finite values. Convert units (`in` × 25.4, `cm` × 10, `kg/m3` ÷ 1000). Apply ops to `base_spec` (or the style preset), validate as `DragsterSpecV2`.
4. **Outcomes:** any `unsupported` → `{'status': 'unsupported', 'message': ..., 'supported_fields': [...]}` and nothing applied. Any `questions` → `needs_clarification`. Validation error → `needs_clarification` with the validator message (field names included). Otherwise `resolved` with `spec`, `changed_fields` (`{path: [old, new]}`), `provenance` (`supplied` for changed, `preset:<style>` or `base_spec` for the rest).
5. **Errors:** provider timeout/error/malformed JSON → raise a typed `ExtractionUnavailable`; endpoint returns 503 `{'message': 'Text interpretation is unavailable right now; the parameter form still works.'}` (via `_safe_error`). Missing API key → same 503.

## 4. Endpoint

`POST /dragsters/interpret`, rate limit 10/minute, body `{prompt: str (1–2000), base_spec: DragsterSpecV2 | null}` (strict, extra forbidden). Returns JSON (not SSE). Never builds CAD, never calls the worker.

Tests (all with a **mocked** `google.generativeai`; no network):
- ≥ 12 supported prompts → expected `resolved` specs (incl. `in`/`cm` conversion, CO₂ alias, refinement `add`/`scale` on a base spec, material density in kg/m³).
- qualitative-only ("make it wider") → `needs_clarification`; unsupported ("add a spoiler") → `unsupported`; two styles → clarification; no style + no base → clarification.
- LLM returns unknown field / `scale` on a frac / 50 changes / NaN / 20 KiB text → rejected safely.
- timeout and provider exception → 503 with safe message; missing key → 503.
- `/generate` with "design a CO2 dragster" yields the `route` event and makes zero provider calls; "design a pinewood derby car" does not route.

## 5. UI (after D7 merges)

- Text-to-CAD (`frontend/app.py`): on a `route` event with `workflow == 'co2_dragster'`, store the prompt in `st.session_state['dragster_text_prompt']`, set the workflow radio to **CO2 dragster**, `st.rerun()`. The event validator must accept the new step.
- Dragster page: **Describe or refine** text area (pre-filled from a routed prompt) + **Interpret** button → calls `/dragsters/interpret` with the current valid draft as `base_spec` (or `null` if the draft is invalid and no style chosen). Show: resolved style, a table of changed fields (old → new, provenance), clarification questions, or the unsupported message. **Apply to editor** loads the resolved spec into the draft (does not generate). A 503 shows the message and leaves the form usable.
- AppTest coverage for: routed prompt pre-fill, apply-to-editor, clarification, unsupported, 503.

## 6. Live sample (opt-in, not CI)

`backend/dragsters/eval/prompts.json`: 15 prompts with expected style and key field values.
`python -m dragsters.eval.run_live` (requires `GEMINI_API_KEY_HEAVY`) calls `interpret` for each, writes a markdown table (resolved / clarification / unsupported / mismatch) to `output/qa-reports/<date>-dragster-text.md`. Run once; paste the summary line into the PR. Cost is small (15 LITE calls) but tell the lead before running.

## Exit criteria

- [ ] All tests pass (backend in the verify image; intent/extraction tests also in plain Python), zero skips; existing classifier and pipeline tests green.
- [ ] Gate (a) schema tests from `.claude/rules/worktree-merge-gate.md` pass (`python backend/tests/test_prompts_schema.py` etc.).
- [ ] Live sample summary in the PR (or "not run: no key" — then D9 runs it).
- [ ] `mirum-security-reviewer` report attached; no unaddressed CRITICAL/HIGH.
- [ ] Status cell updated.
