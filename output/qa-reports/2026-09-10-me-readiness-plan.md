# Mirum — mechanical-engineer readiness review and plan

**Date:** 2026-09-10
**Lens:** a practicing ME evaluates Mirum as a daily-workflow tool. What makes
them walk out, what makes them distrust it, and what has to be true before a
demo.

## Context

Prior reviews targeted engineering quality (severed feedback loops, silent
bugs). This one targets *product readiness for a professional audience*. Three
parallel explorations covered CAD output fidelity, demo/daily reliability, and
engineering credibility. The findings are specific and mostly cheap to fix —
the product is closer than it looks, but its three loudest problems are all
"we computed the right thing and then threw it away."

### What genuinely holds up

Worth protecting, because it is the real differentiator:

- **NACA 4-digit generation is textbook-correct** — closed TE (the −0.1036 x⁴
  term sums to zero at x=1), correct camber slopes, perpendicular thickness
  offset, cosine spacing, built as real B-splines (`cad_utils.py:236-281`).
- **The involute gear profile is real** — `r = base_r·√(1+t²)`, standard 1.0m
  addendum / 1.25m dedendum (`cad_utils.py:71-75`).
- **ISO 4014/4032 hex tables are correct** across all 10 rows.
- **XFOIL / AVL / lifting-line are genuine solvers**, not approximations.
- Nose-cone profile families (tangent ogive, Von Kármán LD-Haack) are correct.

The gap is not uniform: this is a strong aerospace-surface tool with a thin
mechanical-CAD veneer.

---

## Tier 0 — Walk-out moments (execute now)

### T0.1 The product cannot deliver a CAD file

The only download is `.glb`, a tessellated mesh. SolidWorks, NX and Onshape
cannot import it as geometry; Fusion takes it as a mesh body. Three causes:

- The assembler is *deliberately coded* not to write assembly STEP
  (`agents.py:1321-1324`).
- The single-part STEP is deleted by the `finally` added on 2026-09-03
  (`main.py:565`) — a file-leak fix that destroys the only B-rep artifact.
- `export_formats` is computed and shipped (`main.py:1063`) and the frontend
  never reads it. `export_step`/`export_stl`/`export_dxf` have zero callers.

`README.md:261-263` promises "STEP (ISO 10303-21) … imported directly into CAM
software." Nothing delivers it.

**Fix:** persist the artifacts through the pipeline, add a `/download/{fmt}`
route serving STEP/STL/GLB from a short-lived per-run store, and render real
download buttons driven by `export_formats`. Assembly STEP gets written with
per-part names preserved (the uuid naming already avoids the race the comment
worried about).

### T0.2 A dimensionally-wrong part shows a green certificate

`degraded` and `verification` are computed, serialized, shipped — and have
**zero references in `frontend/app.py`**. The cert card's green state comes
only from the six OCC structural checks, so a 40mm bore where 30mm was
requested reads "✅ PASSED". The `# P1: never a silent success` comment at
`main.py:1058` is defeated by the UI.

**Fix:** render the verification report. Degraded ⇒ amber card, the failed
checks named in engineering terms, and the certificate headline reflects
dimensional conformance, not just topology.

### T0.3 Fake threads, and a bolt that cannot enter its own nut

`make_metric_thread` is a stack of annular washers — no helix, no lead, no
flank angle (`cad_utils.py:381-434`, docstring admits it). Worse, the bolt's
rib crest (major radius) is larger than `make_hex_nut`'s ISO minor-diameter
bore: **0.81 mm radial interference at M10×1.5**. The two parts cannot
assemble. `strategies.py:250-251` tells the model the helpers "already produce
… proper threads."

**Fix:** cosmetic threads are legitimate — SolidWorks ships them by default —
but they must be *labelled* and they must *assemble*. Correct the bore/crest
relationship so a bolt passes through its nut with real ISO clearance, rename
the concept to cosmetic in code and prompt, and surface it in the output so no
one mistakes it for cuttable geometry.

### T0.4 Failure dumps the LLM's own retry prompt at the user

On final failure the user sees `build_retry_context` output: the previous
script in a fenced block, the active-rule list, and a raw OCCT traceback
(`failure_analysis.py:307-380` → `main.py:636` → `app.py:288`). The payload
contains its own ``` fences, so the outer code block terminates early and the
rest renders as broken markdown.

**Fix:** split operator detail from user-facing text. The engineer gets a
plain-language diagnosis and a concrete suggestion; the script and traceback
move behind an expander.

### T0.5 "Run" and "Apply Parameters" are 404 in the shipped config

A regression from the 2026-09-02 security work: `MIRUM_ENABLE_RUN_ENDPOINT=0`
disables `/run`, which is what both buttons call. Surfaces as "Script returned
no geometry. Backend response: Not Found."

**Fix:** the original threat model no longer applies — the backend is
loopback-only behind an API key and Caddy basic auth, and the same sandbox
already runs LLM-authored code on every request. Default the endpoint on when
authentication is configured, keep the kill switch, and make the frontend
report a disabled endpoint honestly.

### T0.6 The README misleads on setup and capability

- Quick Start exports `GEMINI_API_KEY_LITE`; `main.py:64-70` hard-requires
  `GEMINI_API_KEY_HEAVY` and raises at import. **Following the README crashes
  the backend.**
- "under 60 seconds" — measured Machinist-only p50 is 27s, p95 98s, and
  planning is paid twice on top (`2026-05-26-full-litesub.md`).
- "> 90% first-attempt compile success" — the harness measures
  pass-after-3-retries.
- No "what it cannot do" section anywhere.

**Fix:** correct the key, replace claims with measured numbers, and add an
explicit limits section. A demo survives modest claims; it does not survive a
claim the engineer disproves in the room.

---

## Tier 1 — Credibility under probing (execute now)

### T1.1 Bill of materials
`group_parts_by_description` already computes exactly the quantity data a BOM
needs and discards it. Emit a real parts list (qty, description, material,
mass) in the payload and UI, and as CSV.

### T1.2 Mass properties are half-computed
`GProp_GProps` carries `CentreOfMass()` and `MatrixOfInertia()`; only `.Mass()`
is read (`verification/geometry.py:190-198`). Centre of mass and the inertia
tensor are free — and are the first things an ME checks on a real part.

### T1.3 The structural report is spec echo, not analysis
`_run_struct_stage` (`main.py:385-490`) never opens the STEP. Margins of safety
are computed from numbers Gemini read out of a sentence — a user can be shown
`MS = +0.34 PASSED` for a wing whose generated caps are 3× thinner. The
underlying module is honest ("sizing-grade estimate, not an FEA result") but
the presentation is not.

**Fix:** cannot add FEA here. Make provenance unmistakable in the payload and
the card: inputs are *stated*, not measured, and the report is sizing-grade.

### T1.4 Interpenetration is never enforced
Two paths: `subagents/assembler.py:152-165` imports a function name that does
not exist and its `except` returns "clean" unconditionally; the real check is
hardcoded `passed=True` (`agents.py:1664-1669`). Parts can occupy the same
space with a green report.

**Fix:** repair the dead import, and surface overlap as a named warning rather
than swallowing it.

### T1.5 Sheet-metal and housing builders have real geometry bugs
- `make_bent_bracket`: flange lands outside the base plate, `rect(centered=
  False)` overhangs half its length, bend fillet selects the opposite edge and
  is wrapped in `except: pass` (`cad_utils.py:1004-1029`).
- `make_enclosure_panel`: same class — unused offset, overhanging flanges,
  asymmetric ± sides.
- `make_bearing_housing`: documents a press fit, bores exactly `bearing_OD`
  (zero interference), and `bearing_width` is accepted and never used.
- `make_nose_cone` / `make_nozzle_profile`: silent clamps and `except: pass`
  around the hollow cut — "2mm wall" can silently return solid.

**Fix:** correct the placement math, honour `bearing_width`, add a real fit
allowance, and replace silent swallows with explicit failure.

### T1.6 Stale cards over fresh geometry
`run_script` and the refine handler clear some session keys but not
`geometry_certificate`/`struct_report` (`app.py:321-323`, `899-914`). Edit,
run, and the cert card keeps the old volume and mass under a green check. An
ME will catch this.

---

## Tier 2 — Documented, not executed this pass

Real work, but either needs the user's environment or is a project in itself.
Listed so it is not lost:

| Item | Why deferred |
|---|---|
| Job queue / persistence / history (Worktree A) | Architecture change; the never-pushed `mirum/backend-throughput` branch already did much of it |
| `/plan` → `/generate` manifest reuse | Approved plan ≠ built plan; needs endpoint + security review |
| Client/server timeout alignment (180 vs 195), cancel button, progress during planning | Best done with the job queue |
| 2D drawings, dimensioned views, title block | Substantial feature |
| Real XCAF PMI / GD&T in STEP AP242 | Current implementation is unreachable and would write one string to the document root; needs pythonocc-core, which is absent |
| True helical threads | Repo's own `docs/propulsion/helix-ban-falsification.md` says the ban is stale — but verifying real helices needs CadQuery in Docker |
| Domain C deterministic helpers | 20/83 eval prompts, zero helpers; the largest single quality lever left |
| Imperial units | No unit field exists anywhere; cross-cutting |
| FEA / stress from geometry | Out of scope for this codebase |

---

## Verification

```bash
# Deterministic — runs here
python backend/tests/test_prompts_schema.py
python backend/tests/test_runner_filters.py
python backend/tests/test_md_report.py
python -m pytest backend/tests/ -q     # expect no NEW failures vs the
                                       # 29 live-API/CadQuery baseline

# Needs Docker (CadQuery + OCC) — the user must run these
docker compose --profile production up -d --build
#  1. generate a single part  -> a STEP download button appears, file opens in CAD
#  2. generate an assembly    -> assembly STEP has per-part names; BOM lists qty
#  3. ask for a 100mm plate, receive 60mm -> card is AMBER, names the mismatch
#  4. M10 bolt + nut          -> nut passes over bolt, no interference
#  5. force a failure         -> plain-language message, no traceback in the body
```

The geometry-touching fixes (T0.1, T0.3, T1.2, T1.5) cannot be verified in this
container — no CadQuery, no OCC. Unit tests pin the arithmetic and the wiring;
the Docker pass above is the real gate.
