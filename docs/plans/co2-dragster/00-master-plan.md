# CO2 dragster — master plan (v2 release)

Date: 2026-11-09. Status: approved for execution once merged. Owner: repository owner (Azrkiel).
This file is the **single source of truth** for the remaining CO2 dragster work. Every
agent reads this file in full **before** its own subplan and re-reads
[§ Invariants](#5-invariants-never-break) before opening its PR.

- Background (do not re-execute): [original scope](../2026-11-05-co2-dragster-scope.md),
  [one-session wedge plan](../2026-11-05-co2-dragster-deterministic-session.md),
  [wedge guide](../../dragsters/deterministic-wedge.md),
  [integrated workflow guide](../../dragsters/integrated-workflow.md).
- Subplans (one per agent): see [§ 7 Workstreams](#7-workstreams-and-subplans).

---

## 1. Goal

Finish the first release described in the original scope: a user picks **one of three
dragster body styles** (wedge, teardrop, narrow waist with raised rear housing), or
describes a CO2 dragster in text, edits explicit millimetre parameters, and receives a
**verified single-solid body** (STEP + GLB + spec JSON + report JSON). The body has a
rear-open cartridge pocket and two axle bores. The release also checks wheel envelopes,
optional stock-block containment, and reports body mass when a density is given.

No LLM is involved in geometry. Text only fills a typed spec; a deterministic builder
renders it (same pattern as `backend/engineering/service.py` and `backend/cem/extraction.py`).

## 2. Where we are (baseline: `main` @ `01f889e`)

| Area | State |
| --- | --- |
| Spec | `backend/dragsters/spec.py`: strict `DragsterSpec` v1, `style='wedge'` only, 13 dims, analytic envelope checks |
| Geometry | `geometry.py`: planar wedge extrude, pocket + 2 bore cylinder cuts |
| Verification | `validation.py`: reimported STEP → single solid, bounds ±0.01 mm, analytic volume, 3 void probes |
| Local tools | `cli.py` (3-file output), `local.py` (STEP→GLB, Plotly viewer), `app.py` standalone Streamlit |
| Service/API | `service.py` + `worker.py` + `api.py`: `POST /dragsters/generate` SSE, `ProcessRunner` (3 workers, 30 s, 1536 MiB), atomic 4-file publish |
| UI | `frontend/dragster_ui.py` + `dragster_state.py`; "CO2 dragster" option in `frontend/app.py:963` |
| Tests | 9 dragster test files (207 passed 2026-11-05); CI "Native CAD and offline API regression gate" green on `01f889e` |
| Deploy | Fly `mirum`/`mirum-api` built from `deploy/open-access` (894a654 = 01f889e + open-access config, PR #19 open). Live smoke passed 2026-11-09 ([D0 evidence](evidence/D0-baseline.md)) |
| Text path | Old LLM rule `rule_co2_dragster` in `backend/strategies.py:913` (keywords incl. "derby car", "race car"); classifier lists dragsters under Domain C. Text prompts **do not** reach the deterministic builder |

## 3. Remaining scope (what "done" means)

In scope for this plan:

1. **Spec v2** — versioned union over three styles + shared wheels / stock / material blocks; v1 files still load (auto-upgrade with provenance).
2. **Lofted-body kernel** — shared section math, section wires, loft, profile-law interpolation.
3. **Teardrop style** and **narrow-waist style** builders + presets.
4. **Verification v2** — style-agnostic STEP checks, reference volume from the profile law, CAD-measured wall distances.
5. **Hardware checks** — wheel envelopes + clearance, stock containment, body mass.
6. **Service/API/UI v2** — style selector, presets, grouped editor, v1 import, mass/track display, wheel envelope overlay.
7. **Text routing + extraction + refinement** — deterministic intent detector, `POST /dragsters/interpret`, unit-safe patch extraction, clarifications, narrowed old rule.
8. **Release gate** — boundary matrix, visual review, docs, eval prompts, CI, deploy (with user approval), live smoke.

Out of scope (do **not** build; from scope § Deferred): separate wheel/axle solids or assemblies,
BOM, STL/DXF, competition rule profiles, race-time/thrust/CFD/optimization, CNC/toolpaths,
strength/pressure claims, photo reconstruction, paint/decals, free-form topology, native
feature-history export, fillets beyond what the kernel produces, a new domain letter.

## 4. Architecture (target)

```text
 Text prompt ──► intent.detect() (regex, no LLM) ──no──► existing generic pipeline (unchanged)
                     │yes
                     ▼
 POST /dragsters/interpret {prompt, base_spec?} ──► extraction (Gemini LITE, flat schema,
                     │                                value+unit pairs, no geometry)
                     ▼                                  │ deterministic unit conversion
           resolved DragsterSpecV2 + provenance ◄──────┘ + merge onto preset/base spec
                     │ (user reviews in editor)
 Editor / JSON ──────┤
                     ▼
 POST /dragsters/generate (v1 or v2 JSON) ─► ProcessRunner ─► worker.py
     worker: styles.REGISTRY[style].build(spec) → STEP export → verify_step_v2(STEP, spec)
             (generic checks + hardware checks) → STEP-derived GLB → report
     parent: digest checks → atomic publish {step, glb, spec, report} → SSE complete
```

Package layout after this plan (new files marked +):

```text
backend/dragsters/
  __init__.py            # MUST NOT import cadquery/numpy/trimesh
  spec.py                # v1 DragsterSpec (frozen, unchanged) + re-exports
  spec_v2.py           + # v2 models, parse_any(), upgrade_v1(), canonical/digest helpers   (D1)
  profiles.py          + # pure-Python profile laws (stdlib only): stations, area, containment (D1)
  kernel.py            + # CadQuery section wires, loft, pocket/bore cuts                       (D2)
  tolerances.py        + # loft/verification tolerances fixed by the D2 spike (pure)           (D2)
  styles/__init__.py   + # REGISTRY: style name -> StyleDefinition
  styles/wedge.py      + # wraps existing v1 geometry for v2 wedge
  styles/teardrop.py   +
  styles/narrow_waist.py +
  validation.py          # v1 verify_step (unchanged behaviour)
  validation_v2.py     + # verify_step_v2: generic + style + hardware checks
  hardware.py          + # wheel envelopes, stock, mass (pure-math part + CAD check part)
  intent.py            + # deterministic dragster-intent detector                               (D8)
  extraction.py        + # LLM text -> patch, unit conversion, merge, clarifications            (D8)
  eval/                + # opt-in live text sample (D8)
frontend/dragster_fields.py + # labels/units/help/grouping for the editor                      (D7)
  presets/*.json       + # wedge.json, teardrop.json, narrow_waist.json (v2)
  examples/wedge.json    # v1 example — keep byte-identical
  geometry.py, local.py, service.py, worker.py, api.py, cli.py, app.py (extended, not replaced)
```

## 5. Invariants (never break)

1. **v1 compatibility.** `backend/dragsters/examples/wedge.json` (v1) must still generate via CLI, local app and `POST /dragsters/generate`, and every existing dragster test keeps passing unmodified (only additive test changes). v1 output (`generator_version: dragster_wedge_v1`) is unchanged.
2. **No LLM in geometry.** Builders, validation and the worker never import `agents`, `google.generativeai`, or call providers. Form-based generation makes **zero** provider calls (tests assert this).
3. **Sandbox untouched.** Do not edit `backend/compiler.py`, `backend/script_policy.py`, `_ALLOWED_MODULES`, `_BANNED_BUILTINS`. The dragster worker executes only repository code with a strict JSON spec on stdin.
4. **Fail closed.** Any failed/unavailable required check ⇒ no accepted result, no published artifacts, prior accepted UI state retained. Never loosen an existing tolerance to make a test pass; never weaken a check silently.
5. **Verification is independent of the builder.** `validation_v2` may import `spec_v2` and `profiles` (the spec's mathematical definition) but **never** `kernel`, `styles/*` build code, or `geometry`. It measures the reimported STEP.
6. **Frontend-light modules.** `spec.py`, `spec_v2.py`, `profiles.py`, `hardware.py` pure-math functions, `intent.py`, presets: importable with stdlib + pydantic only (frontend image has no CadQuery). CAD code is imported lazily inside functions.
7. **Budgets.** Every nominal build+verify+GLB fits the existing worker limits (30 s, 1536 MiB) with margin: **≤ 12 s and ≤ 1.0 GiB peak** in the verify image on the dev machine. Do not raise `ProcessRunner` limits.
8. **Units/frame.** mm; X nose→rear (nose at X=0), Y transverse (symmetric about Y=0), Z up, ground (wheel contact) at Z=0. Body bottom is flat at `Z = ground_clearance_mm`. GLB is metres / glTF Y-up (existing transform).
9. **No claims.** Reports keep `engineering_certified: false`. UI/docs never claim cartridge fit, competition legality, strength, or performance.
10. **Scope discipline.** Only touch files your subplan lists under *Owns*/*May edit*. Unrelated workflows (glider, CEM, regen, generic text-to-CAD for non-dragster prompts) behave identically.
11. **Security review.** Any change to `backend/main.py` endpoints, `backend/schemas.py`, or provider-key handling requires the checklist in `.claude/skills/mirum-security-review/SKILL.md` and a `mirum-security-reviewer` agent pass before merge (repo `CLAUDE.md`).

## 6. Shared contracts (frozen by D1/D2; others build against them)

### 6.1 Spec v2 (owned by D1 — full field table in [D1](D1-spec-v2.md))

```text
DragsterSpecV2 = Annotated[WedgeV2 | TeardropV2 | NarrowWaistV2, Field(discriminator='style')]
common fields (every style):
  schema_version: Literal[2]; units: Literal['mm']; style: <literal>
  length_mm, ground_clearance_mm, front_axle_x_mm, rear_axle_x_mm, axle_bore_diameter_mm,
  pocket_diameter_mm, pocket_depth_mm, pocket_center_z_mm, min_wall_mm,
  wheels: Wheels{front_diameter_mm, rear_diameter_mm, width_mm, side_clearance_mm}
  stock: Stock{length_mm, width_mm, height_mm} | None      (key required, value may be null)
  material: Material{name, density_g_cm3} | None            (key required, value may be null)
derived (not fields): front_axle_z = front_diameter/2, rear_axle_z = rear_diameter/2
parse_any(json) -> DragsterSpec (v1) | DragsterSpecV2   (dispatch on schema_version)
upgrade_v1(v1) -> (WedgeV2, provenance)                  (wheels diameter = 2*axle_z, width 6, side_clearance 1.5)
canonical_spec_v2 / specification_digest_v2              (same canonical-JSON rule as v1)
```

### 6.2 Profile law (`profiles.py` owned by D1, `kernel.py` + `tolerances.py` owned by D2)

A lofted style is described by **control stations** `Station(x, a, s, c, kind)`:
`a` = half-width, `s` = vertical side height above body bottom, `c` = crown height above the side
(total height `h = s + c`). The cross-section at X is a **flat-bottom D-section**: rectangle
`|y| ≤ a, 0 ≤ z' ≤ s` plus upper half super-ellipse crown `(|y|/a)^n + ((z'-s)/c)^n ≤ 1, z' ≥ s`
(`z' = z - ground_clearance`), exponent `n = crown_exponent ∈ [2, 4]`.
Between consecutive control stations each of `a, s, c` is interpolated by the station's `kind`:
`smoothstep` `3t²-2t³`, `ease_out` `sin(πt/2)` (rounded nose), or `hold` (constant). `profiles.py` exposes (stdlib only;
full list in [D1](D1-spec-v2.md) § 1, incl. `teardrop_stations`, `narrow_waist_stations`, `half_width_at`, `top_at`):

```text
interpolate(stations, x) -> (a, s, c)
section_area(a, s, c, n) -> float          # 2as + 2ac·Γ(1+1/n)²/Γ(1+2/n)
law_volume(stations, n, samples=4001) -> float   # composite Simpson over x
disk_margin(a, s, c, n, yc, zc, r) -> float # conservative: square of half-side r inside section; returns min slack (mm)
crown_height_at(a, s, c, n, y) -> float     # top surface z' at lateral y
```

`kernel.py` (CadQuery, [D2](D2-lofted-kernel.md)) exposes `section_wire`, `loft_body`, `cut_pocket_and_bores`;
`tolerances.py` holds the loft/verification tolerances fixed by the D2 spike (validation imports it, never `kernel`).

### 6.3 Style registry (owned by D1 — stubs; D2 fills wedge, D3 teardrop, D4 narrow waist)

```python
@dataclass(frozen=True)
class StyleDefinition:
    name: str                 # 'wedge' | 'teardrop' | 'narrow_waist'
    label: str                # UI label
    generator_version: str    # e.g. 'dragster_teardrop_v2'
    build: str                # 'dragsters.styles.<module>:build', imported lazily (cadquery inside)
    preset_path: str          # 'presets/<name>.json'
# Pure law functions (stations, half_width_at, top_at, law_volume) live in profiles.py so that
# validation can use them without importing any builder.
```

### 6.4 Report v2 (owned by D5)

Report keeps all v1 keys and adds: `schema_version: 2`, `style`, `generator_version`,
`checks{...}` (ids listed in [D5](D5-verification-v2.md) and [D6](D6-hardware-checks.md)),
`hardware{track_width_mm, wheel_envelopes[...], stock_margin_mm|null}`,
`mass{value_g|null, basis:'body_only', reason|null}`, `method_limits{...}` (text describing
any sampled check), `provenance{field: 'supplied'|'preset:<name>'|'migrated_from_v1'}` (when given).

### 6.5 HTTP (owned by D7/D8)

- `POST /dragsters/generate` — body is v1 **or** v2 spec JSON (dispatch on `schema_version`). SSE contract unchanged.
- `POST /dragsters/interpret` — `{prompt: str ≤ 2000, base_spec: v2 | null}` → `{status: 'resolved'|'needs_clarification'|'unsupported', spec?, provenance?, changed_fields?, questions?, message?}`. Never builds CAD. Rate limit 10/minute.
- `GET /dragsters/presets` — `{styles: [{name, label, preset: <v2 spec>}]}`.

## 7. Workstreams and subplans

| ID | Subplan | Agent type | Depends on | Wave | Branch | Status |
| --- | --- | --- | --- | --- | --- | --- |
| D0 | [Baseline & live smoke](D0-baseline.md) | general-purpose (ops) | — | 0 | `chore/co2-dragster-d0-baseline` | merged (PR #20) |
| D1 | [Spec v2 + registry + v1 compat](D1-spec-v2.md) | general-purpose (implementation) | D0 | 1 | `feat/co2-dragster-d1-spec-v2` | merged (PR #22) |
| D2 | [Lofted-body kernel + spike + wedge v2](D2-lofted-kernel.md) | general-purpose (implementation, CAD) | D0, D1 first commit (`profiles.py`) | 1 | `feat/co2-dragster-d2-kernel` | merged (PR #23) |
| D3 | [Teardrop style](D3-style-teardrop.md) | general-purpose (CAD) | D1, D2 | 2 | `feat/co2-dragster-d3-teardrop` | merged (PR #25) |
| D4 | [Narrow-waist style](D4-style-narrow-waist.md) | general-purpose (CAD) | D1, D2 | 2 | `feat/co2-dragster-d4-narrow-waist` | merged (PR #27) |
| D5 | [Verification v2](D5-verification-v2.md) | general-purpose (CAD verification) | D1, D2 | 2 | `feat/co2-dragster-d5-verify-v2` | merged (PR #26) |
| D6 | [Hardware checks: wheels, stock, mass](D6-hardware-checks.md) | general-purpose | D1, D2 | 2 | `feat/co2-dragster-d6-hardware` | merged (PR #24) |
| D7 | [Service/API/UI v2](D7-service-ui-v2.md) | general-purpose (full-stack) | D3, D4, D5, D6 | 3 | `feat/co2-dragster-d7-service-ui` | not started |
| D8 | [Text routing, extraction, refinement](D8-text-routing.md) | general-purpose (backend + LLM) | D1 (backend part), D7 (UI part) | 3 | `feat/co2-dragster-d8-text` | not started |
| D9 | [Release gate, docs, deploy](D9-release.md) | lead (Opus) + general-purpose | D0–D8 | 4 | `chore/co2-dragster-d9-release` | not started |

Dependency graph:

```text
D0 ──► D1 ──┬──► D3 ──┐
   │    ┆   │         │
   └─► D2 ──┼──► D4 ──┤      (┆ = D2 needs only D1's first commit, profiles.py)
            ├──► D5 ──┼──► D7 ──► D9
            └──► D6 ──┘    ▲
            D1 ──► D8(backend) ──► D8(UI, after D7) ──► D9
```

Parallelism: wave 1 = D1 ∥ D2. Wave 2 = D3 ∥ D4 ∥ D5 ∥ D6 (disjoint files by design).
Wave 3 = D7 ∥ D8 backend; D8 UI after D7 merges. Estimated effort: 6–8 engineering days total.

## 8. Agent protocol (every agent)

1. **Read** this master plan fully, then your subplan fully, then the files your subplan lists under *Read first*.
2. **Base branch.** Branch from current `origin/main` (which must already contain your dependencies' merged PRs). If a dependency PR is approved but not merged, branch from it and say so in your PR. Never base on `integration/text2cad-hardening` or any local-only branch.
3. **Worktree.** Work in a fresh git worktree, not `C:\Users\jiang\Mirum` (it is on a divergent branch). On Windows set `git config core.autocrlf false` in the worktree before editing (Docker layer caching and digests break on CRLF).
4. **Native environment.** CAD tests must run against the real kernel: the verify image (`docker build -f Dockerfile.verify -t mirum-verify .`), or a Linux venv from the lockfiles. Skipped/mocked CAD tests do not count. If Docker runs out of disk, prune **only** build cache (`docker builder prune`), never other containers/images.
5. **TDD.** Write the failing tests named in your subplan first, watch them fail, then implement.
6. **Stay in your lane.** Edit only files under your subplan's *Owns* / *May edit*. If you need a contract change from another workstream, **stop and report** with the exact proposed diff to the contract in § 6; do not change it yourself.
7. **Conflicts.** Master § 5 and § 6 override subplans. Subplan details override your own judgment. If master and subplan contradict, stop and report.
8. **Status.** In your PR, update only your own row's *Status* cell in § 7 (`in review (PR #n)`). The lead sets `merged`.
9. **PR.** Title `co2-dragster <ID>: <summary>`. Body: what changed, files, exact test commands with counts/skips, timing/memory numbers where required, deviations, open questions. End with the attribution line the repo uses. Do not merge your own PR; do not deploy.
10. **Done report** (final message to the lead): branch, commit SHA, PR number, test commands + counts, every *Exit criteria* item from your subplan marked pass/fail with evidence, follow-ups.

Launch prompt template for the lead (fill `<ID>` and `<file>`):

```text
You are the agent for workstream <ID> of the Mirum CO2 dragster plan (repo Azrkiel/Mirum).
Read docs/plans/co2-dragster/00-master-plan.md in full, then docs/plans/co2-dragster/<file>.
Follow the master's § 8 Agent protocol exactly. Execute the subplan to its exit criteria;
do not re-plan or expand scope. Stop and report only for a blocker or a contract conflict.
```

## 9. Global acceptance (checked by D9)

- All three styles: nominal preset + every boundary case in the D9 matrix generates one valid solid and passes every required check; every negative case in the matrix is rejected with the expected check/validation id.
- v1 example still produces the same measured geometry (bounds/volume within v1 tolerances).
- `POST /dragsters/generate` accepts v1 and v2; `POST /dragsters/interpret` resolves the D8 supported prompt set with mocked provider; live sample recorded separately.
- Non-dragster prompts (glider, nozzle, pinewood derby car, full-size race car, standalone CO2 cartridge) do not route to the dragster workflow.
- CI green (all jobs). Native gate count recorded.
- Side/top/iso screenshots of all three presets reviewed against the reference shape cues (tapered nose, waist, rear housing, wheel positions).
- Docs updated; this table shows every row `merged`.

## 10. Risks

| Risk | Mitigation |
| --- | --- |
| Loft self-intersection / invalid solid at boundary shapes | D2 spike establishes station count, ruled vs smooth, and tightened bounds before D3/D4 freeze ranges |
| Lofted volume ≠ law volume | Tolerance fixed by D2 measurement (default 1.0 %, hard max 2.0 %) |
| Build time over budget | D2/D3/D4 measure; reduce stations / crown points before touching limits |
| Parallel agents editing the same file | Ownership tables in each subplan; registry stubs created by D1 so D3/D4 only fill their own module |
| Deploying from `main` re-adds access gate | D9 deploy steps reconcile PR #19 first and require explicit user approval |
| LLM extraction drift | Flat schema, value+unit pairs, deterministic conversion, strict validation, clarifications; never trusted to produce geometry |
