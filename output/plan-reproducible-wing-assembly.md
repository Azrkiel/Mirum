# Plan: Reproducible NACA-2412-class Wing Assembly via `/generate`

Status: **Planning — multi-session work.**
Owner: Kevin (Mirum lead).
Last updated: 2026-05-28.

---

## 1. Goal & Definition of Done

A single natural-language prompt to `POST /generate` reproduces the
full structural wing assembly currently locked inside the standalone
`output/wing_assembly_NACA2412.py` script:

> *"NACA 2412 wing, 1200 kg MTOW, 60 m/s cruise at 3000 m, span 10.7 m,
> tapered 0.5, AL 2024-T3, with front and rear spars, 7 ribs with
> lightening holes, upper/lower skin panels, 4 stringers, root fitting.
> Run aero and structural analyses."*

**Done when:**

1. The above prompt yields a valid multi-part STEP + GLB through the
   live pipeline (no hand-crafted script needed).
2. `geometry_certificate.passed = True` for every part.
3. `aero_report` and `struct_report` both populated.
4. The output is byte-stable-ish (mating positions deterministic, part
   count fixed) under re-runs.
5. An eval-harness `--regression-for D` slice contains this prompt and
   gates it on (1)–(4).

---

## 2. Current state (what already exists vs. what's missing)

### Exists & works
- Domain D classifier routes "wing / spar / rib / NACA 2412" correctly.
- `make_naca_airfoil(...)` builds OCC B-spline airfoil sections.
- `make_co2_void(...)` for internal voids.
- `analysis/airfoil.py` runs XFOIL polars (Cl, Cd, Cm, L/D, stall).
- `analysis/structures.py` (this PR series) runs load-factor envelope +
  bending/shear margins.
- Pipeline wiring for both: `run_aero` and `run_struct` opt-in flags
  on `/generate` (this PR series).

### Missing — the actual gap
| Gap | Symptom | Why it matters |
|---|---|---|
| No `make_spar(...)` in `cad_utils.py` | Machinist authors spar geometry ad-hoc per prompt | Failure-prone; explains the hand-iterated standalone script |
| No `make_rib(...)` | Same — and OCC `extrudeLinear` chokes on the TE cusp | Forces solid-rectangle fallback, not a true OML rib |
| No `make_stringer(...)` | Ad-hoc | — |
| No `make_wing_skin_panel(...)` | Ad-hoc; current "skin" is a flat box-beam panel | Not a true curved OML skin |
| No `make_root_fitting(...)` | Ad-hoc | — |
| Planner prompt doesn't know wing-assembly canonical decomposition | Part lists drift between runs | Non-deterministic part count |
| REA doesn't extract `aircraft_spec` / `wing_box_spec` fields | `run_struct` silently skips on real prompts | The pipeline wiring sits unused |
| Frontend has no card for `struct_report` | Field arrives but isn't rendered | UX gap |

---

## 3. Phased plan

Each phase is one branch, one PR, gated tests. Tier 1 unlocks the
rest; everything else can run in parallel after that.

### Phase 1 — Parametric Domain D builders in `cad_utils.py`

**Branch:** `feature/cad-utils-wing-structural-builders`
**Touches:** `backend/cad_utils.py`, `backend/tests/test_cad_utils_wing.py` (new)
**Security gate:** YES — `cad_utils.py` is in `_ALLOWED_MODULES`. Any
import inside `cad_utils.py` is trusted by every LLM-generated script.
Must not import `os`, `subprocess`, `pathlib`, `importlib`, etc.

#### Functions to add

```python
def make_spar(
    *, airfoil_points: list[tuple[float, float]],   # normalised (x/c, z/c)
    span_m: float,
    root_chord_m: float,
    tip_chord_m: float,
    x_frac: float,                                  # 0..1 location along chord
    web_thickness_m: float,
    twist_tip_deg: float = 0.0,
    dihedral_deg: float = 0.0,
    n_stations: int = 7,
) -> cq.Workplane: ...

def make_rib(
    *, airfoil_points: list[tuple[float, float]],
    chord_m: float,
    y_station_m: float,
    thickness_m: float,
    front_spar_x_frac: float, rear_spar_x_frac: float,
    n_lightening_holes: int = 3,
    twist_deg: float = 0.0,
    use_oml_outline: bool = False,                  # see Phase 2
) -> cq.Workplane: ...

def make_stringer(
    *, span_m: float,
    x_frac: float,
    z_frac: float,                                  # ±1 = upper/lower cap
    section: Literal["L", "Z", "T", "hat"] = "L",
    leg_mm: float = 12.0, thickness_mm: float = 1.5,
    twist_tip_deg: float = 0.0,
) -> cq.Workplane: ...

def make_wing_skin_panel(
    *, airfoil_points: list[tuple[float, float]],
    span_m: float,
    root_chord_m: float, tip_chord_m: float,
    upper: bool,                                    # True = suction surface
    thickness_root_m: float, thickness_tip_m: float,
    front_spar_x_frac: float, rear_spar_x_frac: float,
    twist_tip_deg: float = 0.0,
    dihedral_deg: float = 0.0,
    n_stations: int = 11,
) -> cq.Workplane: ...

def make_root_fitting(
    *, root_chord_m: float,
    thickness_to_chord: float,
    front_spar_x_frac: float, rear_spar_x_frac: float,
    fitting_thickness_m: float,
    bolt_pattern: Optional[list[tuple[float, float]]] = None,  # (x_frac, z_frac)
) -> cq.Workplane: ...
```

#### Tests (`backend/tests/test_cad_utils_wing.py`)
- OCC-gated (skip if `cadquery` not importable — same pattern as
  `test_geometry_verification.py`).
- For each builder: shape is valid, volume > 0, bounding box within
  expected envelope, BRepCheck_Analyzer clean.
- Cross-check that `make_spar` + `make_wing_skin_panel(upper=True/False)`
  + `make_rib` × 7 + `make_stringer` × 4 + `make_root_fitting` *assembled*
  produces the same final mass (±5%) as the standalone script's
  `build_assembly()`.

#### Security checklist for this phase
- [ ] No new imports in `cad_utils.py` beyond `cadquery`, `cq`, `math`.
- [ ] No `getattr` / `eval` / `exec` / file IO.
- [ ] Dispatch `mirum-security-reviewer` agent against the diff.

---

### Phase 2 — OCC trailing-edge cusp recipe

**Branch:** `feature/oml-rib-cusp-recipe`
**Touches:** `backend/cad_utils.py` (`make_rib`'s `use_oml_outline=True`
branch), `backend/tests/test_oml_rib_cusp.py` (new).
**Depends on:** Phase 1.

#### Problem statement
`cq.Workplane.polyline(airfoil_points).close().extrude(t)` produces an
invalid B-rep on NACA 4-digit profiles at the trailing-edge cusp.
Symptom in the standalone script's comment:

> *"the full-airfoil contour + offset2D path is avoided because OCC
> extrudeLinear produces invalid B-reps on complex non-convex polygons
> near the trailing-edge cusp."*

#### Three candidate fixes — pick by test outcome
1. **Trim the TE.** Crop the airfoil points to x/c ≤ 0.995 and close
   with a small straight segment. Cheap; loses 0.5% chord.
2. **Loft between two identical offset wires** at z = ±t/2 with
   `ruled=True`. Bypasses the planar polygon → solid promotion.
3. **B-spline-fit the contour** with `cq.Workplane.spline(...)` (already
   used in `make_naca_airfoil`) then extrude. Treats the cusp as a
   smooth curve.

Test that builds NACA 0012, 2412, 4415 ribs at three thicknesses each
and confirms `BRepCheck_Analyzer` clean + volume positive for every
combination. Winner becomes the documented recipe; losers stay as
escape hatches with a parameter flag.

---

### Phase 3 — Domain D strategy updates

**Branch:** `feature/domain-d-wing-strategy`
**Touches:** `backend/strategies.py`, `backend/tests/test_domain_d_strategy.py`
**Depends on:** Phase 1.

The Domain D system prompt today only mentions `make_naca_airfoil` and
`make_co2_void`. It needs to teach the Machinist:

- Which builder to use for which part-name pattern
  (`*spar* → make_spar`, `*rib* → make_rib`, etc.).
- Canonical structural-box decomposition: front spar, rear spar,
  N ribs, upper skin, lower skin, M stringers, root fitting.
- A minimal worked example using the new builders.

Includes few-shot examples in `backend/retrieval.py`'s FAISS index
(via `add_to_index` after Phase 1's tests prove the builders work).

---

### Phase 4 — Planner + REA prompt updates

**Branch:** `feature/wing-assembly-planning`
**Touches:** `backend/agents.py` (Planner + REA prompts),
`backend/eval_harness/prompts.json` (regression slice).
**Security gate:** YES — REA produces `DesignRequirements`; new fields
(`aircraft_spec`, `wing_box_spec`, `wing_spec`) must validate against
their Pydantic bounds. Reject hallucinated nonsense.

#### Planner
Detect "wing assembly" intent and emit a canonical 12-part manifest:

```
parts = [
  front_spar, rear_spar,
  rib_00, rib_01, rib_02, rib_03, rib_04, rib_05, rib_06,
  skin_upper, skin_lower,
  stringers, root_fitting,
]
```

Mating rules: every rib mates `Coincident(rib.front_face, front_spar.y_face_at_rib_station)`,
etc. (See `MateType` enum — pick the right ones.)

#### REA
When the prompt mentions weight + cruise + airfoil, extract
`aircraft_spec`, `wing_spec`, `wing_box_spec` per the new schemas
landed in `feature/wing-structures-pipeline-wiring`. Add three to four
in-prompt examples to the REA system prompt.

---

### Phase 5 — End-to-end regression + golden artifact

**Branch:** `feature/wing-assembly-eval-regression`
**Touches:** `backend/eval_harness/prompts.json`,
`backend/eval_harness/runner.py`, `output/qa-reports/...`
**Depends on:** all prior phases.

Add a `--regression-for D` prompt with `id: D-WING-FULL-ASSEMBLY`
that exercises the entire chain. Acceptance:

| Check | Pass criterion |
|---|---|
| Pipeline returns `complete` | success status, valid GLB |
| Part count | exactly 12 |
| Geometry cert | `passed=True` |
| Aero report | `Cl_max > 1.0`, `stall_alpha ∈ [12°, 20°]` |
| Struct report | `n_ultimate_pos ≈ 5.7`, `bending_passed=True` (after sizing fix) |
| Mass | within 10% of standalone-script reference (67.6 kg box weight × 2) |

The standalone script becomes the golden artifact; the pipeline output
is diffed against it.

---

### Phase 6 — Frontend rendering for `struct_report`

**Branch:** `feature/frontend-struct-card`
**Touches:** `frontend/app.py`
**Depends on:** none (uses fields already shipped in
`feature/wing-structures-pipeline-wiring`).
**Security gate:** Streamlit-only, low surface.

- Render a "Flight Envelope" card with a V-n diagram (matplotlib):
  - x-axis = airspeed (m/s)
  - y-axis = load factor n
  - draw V_stall, V_corner, V_NE markers
  - shaded maneuvering envelope (n_man_pos / n_man_neg)
  - shaded gust envelope (n_gust_pos / n_gust_neg)
  - heavy line at the limit envelope
- Render a "Structural Margins" card: bending MS, shear MS, pass/fail
  badges, material, root loads.

---

## 4. Sequencing

```
Phase 1 (builders) ─┬─► Phase 2 (cusp recipe)
                    ├─► Phase 3 (strategy)
                    └─► Phase 4 (planner+REA)
                              │
Phase 6 (frontend) ───────────┴─► Phase 5 (e2e regression)
```

Phase 1 is the unblocker. Phases 2/3/4/6 can run in parallel once
Phase 1 lands. Phase 5 is the integration gate.

Estimated effort (rough):

| Phase | Effort |
|---|---|
| 1. Builders | 1–2 working days |
| 2. Cusp recipe | 0.5–1 day (3 fixes to test) |
| 3. Strategy + few-shot | 0.5 day |
| 4. Planner + REA | 1 day (prompt engineering + tests) |
| 5. E2E regression | 0.5 day |
| 6. Frontend card | 0.5 day |
| **Total** | **~5 working days** |

---

## 5. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| OCC cusp problem unsolvable for all NACA profiles | M | Phase 2's escape hatch: `use_oml_outline=False` keeps the solid-rectangle fallback as an opt-out |
| Planner emits the wrong part count under prompt variations | H | Lock the 12-part decomposition in `strategies.py` as a hard rule, not a soft prompt suggestion |
| REA hallucinates structural numbers (e.g. `cap_area_m2`) the user didn't supply | H | Pydantic bounds reject nonsense; if REA returns null for missing inputs, `run_struct` skips with a clear message rather than guessing |
| Eval harness regression noise (5-min XFOIL run) | L | Gate Phase 5 on the cheap parts (part count, geom cert) — XFOIL is opt-in |
| Mass discrepancy from idealised vs OML ribs | M | Document the expected ±10% band; tighten in a later phase if needed |
| Security: a `cad_utils.py` builder accidentally imports something dangerous | L | Phase 1 checklist + `mirum-security-reviewer` agent dispatch |

---

## 6. Out of scope (explicit)

- **3-D aero (lifting-line / VLM / AVL).** XFOIL is 2-D sectional; a
  whole-wing C_D / induced-drag computation is a separate Tier 1
  initiative. Tracked separately.
- **Sweep, anhedral, washout beyond ±5°.** Stick to the GA-wing
  envelope first; advanced planforms are a later phase.
- **Composite materials.** The metallic allowables table is Tier 1
  enough; composites need a layup schema first.
- **Multi-cell wing boxes.** Single-cell only in this milestone.

---

## 7. References

- Standalone artifact: `output/wing_assembly_NACA2412.py`
- Analysis modules: `analysis/airfoil.py`, `analysis/structures.py`
- Pipeline wiring: `feature/wing-structures-pipeline-wiring` (this
  branch series)
- Worktree merge-gate process: `.claude/rules/worktree-merge-gate.md`
- Security checklist: `.claude/skills/mirum-security-review/SKILL.md`
