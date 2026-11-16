# D9 — Release gate, docs and deploy

Parent: [master plan](00-master-plan.md) — read it in full first; master § 9 is this workstream's checklist.
Agent: **lead (Opus)** owns judgement and approval steps; may delegate tasks 1–3 to a general-purpose agent.
Wave 4. Depends on: D0–D8 merged. Effort: 1 day. Branch: `chore/co2-dragster-d9-release`.

## Goal

Prove the whole release works together, document it, and (only with the user's explicit approval)
deploy it and smoke-test it live.

## Read first

- Every subplan's evidence doc under `docs/plans/co2-dragster/evidence/`
- `deploy/fly/README.md`; PR #19 (`deploy/open-access`) and its effect: open UI, no access token
- Memory/gotchas for this machine: Fly builds need `--remote-only --depot=false` (Avast TLS); Docker VM ~8 GB — keep containers ≤ 3 GB

## Owns

- `docs/dragsters/v2-guide.md` (new user guide), `docs/dragsters/release-v2-evidence.md` (new)
- `docs/dragsters/deterministic-wedge.md`, `integrated-workflow.md` (mark v1 sections as legacy, link the v2 guide)
- `architecture.md`, `README.md` (short capability/limits paragraph)
- `backend/tests/test_dragster_release_matrix.py` (new; the integrated matrix below)
- Master § 7 status cells (lead sets every row to `merged`)

## Tasks

1. **Integrated matrix test** (`test_dragster_release_matrix.py`, real worker through `service.generate`, verify image). Parametrised over:
   - nominal preset for each style (3);
   - for each style, min and max `length_mm`, min and max wheel diameters, min and max `min_wall_mm`, with dependent fields adjusted to stay valid (≥ 12 cases);
   - v1 example (must equal pre-release measured bounds/volume within v1 tolerances);
   - negatives that must be rejected **before** CAD with 422-equivalent validation errors: pocket breaking out (zp too high), bore/pocket overlap, axle outside body, ground clearance 0, wheel overlap (wheelbase too short), stock smaller than the body (rejected **after** CAD with `stock_containment`), NaN, `units: "in"`, v2 field in a v1 file.
   Record per-case seconds and peak RSS; all accepted cases ≤ 12 s median, none over 25 s.
2. **Full CI** on the branch; record native-gate pass count vs D0's baseline (must not drop except for intentionally removed tests — there should be none).
3. **Live text sample** — if D8 didn't run it, run `python -m dragsters.eval.run_live` once (ask the user first; small Gemini cost). Record results.
4. **Visual review (lead).** Side/top/iso screenshots of all three presets from the running app. Compare with the reference showroom photo from the original scope (ask the user for it if it isn't in the repo). Verdict per style: tapered nose, waist, rear housing, wheel positions. If a style looks wrong, open a follow-up against D3/D4 (preset or law) rather than patching here.
5. **Docs.** `v2-guide.md`: what each style is, field tables with units/ranges/meaning (generate the tables from `spec_v2` + `dragster_fields.py`, don't hand-copy), wheel/stock/material semantics, text input examples, downloads, limits (no cartridge-fit/competition/strength/performance claims). `release-v2-evidence.md`: matrix table, timings, CI run id, live sample, screenshots, known limits.
6. **Merge order check.** Every D-row merged; `main` CI green.
7. **Deploy — only after the user explicitly approves in this session.** Before asking, explain:
   - PR #19 (`deploy/open-access`) is still the only place the open-access Fly config lives. Deploying plain `main` re-enables the access-token gate on the frontend. Ask the user to choose: merge PR #19 first (site stays open), or deploy with the gate.
   - The text interpreter spends the paid Gemini key; with open access anyone can call it (rate-limited 10/min per IP).
   After approval: `fly auth whoami` (user must run `fly auth login` if expired), then from the repo root
   `fly deploy . -c deploy/fly/backend.fly.toml --dockerfile deploy/fly/backend.Dockerfile --no-public-ips --remote-only --depot=false` and
   `fly deploy . -c deploy/fly/frontend.fly.toml --dockerfile deploy/fly/frontend.Dockerfile --remote-only --depot=false`.
8. **Live smoke.** On https://mirum.fly.dev: generate each preset (3), load the v1 example and generate, run one text prompt through Text-to-CAD routing ("CO2 dragster, narrow waist, 230 mm long") and apply it, download all four files once and check the report digests match. Screenshot each. Record release ids from `fly releases`.

## Exit criteria (= master § 9)

- [ ] Matrix test green in the verify image and in CI; timings recorded.
- [ ] Visual verdict for all three styles recorded.
- [ ] Docs merged.
- [ ] Deploy done with explicit approval (or recorded as "not deployed — awaiting user").
- [ ] Live smoke evidence (or blocked note).
- [ ] Master § 7 all `merged`.
