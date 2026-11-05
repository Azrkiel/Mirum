# Deterministic CO2 dragster: one-session implementation plan

Date: 2026-11-05. Status: ready for implementation; documentation only.

Goal: generate one parameterized wedge dragster body locally from explicit JSON, verify the exported STEP, and save a reproducible result without an LLM.

Architecture: a small Python package owns a strict spec, deterministic CadQuery builder, STEP measurements, and a local CLI. The CLI uses the same builder for every run, stages outputs privately, and publishes a new result directory only after reimport checks pass. This is a trusted local prototype; it does not attach to Mirum's API, source-execution sandbox, artifact store, or frontend.

Tech stack: Python, existing Pydantic 2.9.2 and CadQuery 2.6.1/OpenCASCADE, pytest, and the existing Linux verification image. No new dependency, provider, geometry engine, or framework.

## Source and branch

- Repository: <https://github.com/Azrkiel/Mirum>.
- Planning branch: `docs/co2-dragster-deterministic-session`.
- Verified main baseline: `36b996f91b0c17c54710a24545cce6acc131ff1f` on 2026-11-05.
- Local planning worktree: `C:/Users/jiang/Mirum-co2-plan`.
- Broader proposal: [original scope](2026-11-05-co2-dragster-scope.md). This document defines the smaller implementation target; the original proposal is background, not an additional checklist.
- Copy-paste handoff: [execution prompt](2026-11-05-co2-dragster-session-prompt.md).

The original checkout at `C:/Users/jiang/Mirum` is on a divergent integration branch. Do not implement there, merge unrelated worktrees, or replace main with that checkout. Start implementation on a new branch from this planning branch, retaining its inspected code baseline. If the environment differs, inspect its instructions and record its exact baseline before changes.

## Scope and session boundary

Included:

- One simple wedge body with constant width, a rising nose section, and a flat rear housing. Sharp planar faces are intentional; no loft or fillet work.
- Editable, versioned JSON with dimensions in millimeters. JSON is the editor for this session.
- One rear-open cylindrical pocket and two transverse through-axle bores.
- Explicit parameter validation and measured checks on a reimported STEP.
- A local command returning STEP, resolved JSON, and a machine-readable check report in a new result directory.
- Focused tests, one successful native example, one deliberately invalid example, and a short usage guide.

Excluded:

- Streamlit/browser forms, viewer, GLB, API/SSE routes, shared artifact publication, and saved UI state.
- Text interpretation, refinement agents, classification changes, and provider calls.
- Teardrop/narrow-waist styles, free-form sections, fillets, and photo reconstruction.
- Wheels/axles as separate solids, wheel-envelope validation, assemblies, material density/mass, and stock constraints.
- Manufacturing approval, cartridge standards, competition compliance, strength, pressure, or racing-performance claims.
- New subprocess infrastructure or hostile-input execution. This CLI processes trusted local JSON and executes only repository code.

A working native Python/CadQuery environment or Docker daemon is a prerequisite. This is sized for one focused agent session, not a guarantee that environment repair fits the same session. If native execution is unavailable, report that blocker; skipped or mocked CAD tests do not establish completion. Do not expand the scope to use remaining time.

## File map

All implementation paths below are proposed additions, not existing files.

| Path | Responsibility |
| --- | --- |
| `backend/dragsters/__init__.py` | Package marker; no eager imports of app/provider modules |
| `backend/dragsters/spec.py` | `DragsterSpec`, explicit units/frame, strict field and relationship checks, canonical JSON and SHA-256 digest |
| `backend/dragsters/geometry.py` | `build_body(spec)` and deterministic pocket/bore cutting tools |
| `backend/dragsters/validation.py` | `verify_step(path, spec)`: reimport, measurements, intended void checks, report; reject failures |
| `backend/dragsters/cli.py` | `python -m backend.dragsters.cli`, staging/publication, structured errors and exit codes |
| `backend/dragsters/examples/wedge.json` | Complete numeric demonstration spec; no implied hardware standard |
| `backend/tests/test_dragster_spec.py` | Schema rejection cases and canonical JSON round trip |
| `backend/tests/test_dragster_geometry.py` | Real CAD generation, STEP reimport, wrong/missing cut cases, reproducibility |
| `backend/tests/test_dragster_cli.py` | Successful run, validation failure, export/check failure cleanup, prior-output preservation |
| `docs/dragsters/deterministic-wedge.md` | Commands, field meanings, generated files, native verification evidence, prototype limits |

Read these existing seams for context, without modifying them:

- `backend/engineering/spec.py`: strict Pydantic and canonical-spec conventions.
- `backend/tests/test_acceptance_delivery.py`: native tests must exercise actual geometry.
- `backend/verification/acceptance.py`, `backend/artifacts.py`: future web integration points; not used by this CLI.
- `requirements-linux.lock`, `Dockerfile.verify`, `pytest.ini`: pinned CAD/test environment and import paths.

Do not edit `main.py`, `compiler.py`, `script_policy.py`, `classifier.py`, `strategies.py`, the material table, or the frontend for this slice. Do not route around the existing sandbox for web requests; no web request uses this prototype.

## Flow, including failures

```text
[Trusted local JSON]
        |
        v
[Parse + validate DragsterSpec] --bad fields/relations--> [stderr; exit 2]
        |
        v
[Build fixed-topology wedge + pocket + axle cuts]
        |                                     |
        |                                  CAD failure
        v                                     v
[Export STEP into private staging] ------> [clean own staging; exit 1]
        |
        v
[Reimport that STEP + measure + probe intended voids]
        |                                     |
        |                                failed/missing check
        v                                     v
[Write resolved spec + report/digests] --> [clean own staging; exit 1]
        |
        v
[Rename staging into a fresh run directory]
        |                                     |
        |                                storage failure
        v                                     v
[Print directory; exit 0]                 [error; exit 1]

Every attempt owns a unique staging/run name. Never overwrite a prior result.
Changing JSON creates a new run; existing files retain their original meaning.
```

## Frozen prototype contract

`DragsterSpec` uses `ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)`. All fields below are required. Use `model_validate_json` for files, preserve explicit values, and reject booleans as dimensions. Do not silently fill hardware dimensions or convert units.

```json
{
  "schema_version": 1,
  "style": "wedge",
  "units": "mm",
  "length_mm": 200.0,
  "width_mm": 42.0,
  "height_mm": 32.0,
  "nose_height_mm": 12.0,
  "ground_clearance_mm": 3.0,
  "front_axle_x_mm": 40.0,
  "rear_axle_x_mm": 175.0,
  "axle_z_mm": 9.0,
  "axle_bore_diameter_mm": 3.0,
  "pocket_diameter_mm": 20.0,
  "pocket_depth_mm": 52.0,
  "pocket_center_z_mm": 23.0,
  "min_wall_mm": 2.0
}
```

These are demonstration dimensions chosen for this prototype, not a verified cartridge fit or competition preset. `pocket_diameter_mm` is the actual cut diameter, with no hidden fit allowance. Ground clearance positions the body; there are no wheels in this slice. Axle and pocket Z coordinates are absolute above Z=0.

Frame: X=0 nose, X=length rear; Y=0 symmetry plane; Z=0 ground. The bottom is Z=ground_clearance. The top rises linearly from nose height to full height over the first half of X, then remains flat. Height values are measured above the bottom.

Scalar bounds, deliberately conservative for the local prototype:

| Fields | Inclusive supported range, mm |
| --- | --- |
| `length_mm` | 160 to 260 |
| `width_mm` | 32 to 60 |
| `height_mm` | 28 to 45 |
| `nose_height_mm` | 8 to 18 |
| `ground_clearance_mm` | 0 to 10 |
| `front_axle_x_mm`, `rear_axle_x_mm` | 10 to 250 |
| `axle_z_mm` | 4 to 24 |
| `axle_bore_diameter_mm` | 2 to 5 |
| `pocket_diameter_mm` | 12 to 24 |
| `pocket_depth_mm` | 30 to 80 |
| `pocket_center_z_mm` | 15 to 45 |
| `min_wall_mm` | 2 to 5 |

Version/style/units are exact literals `1`, `wedge`, `mm`; reject boolean version values explicitly. Define `L,W,H,N,g,xf,xr,za,ra,rp,D,zp,t` from the fields; `ra` and `rp` are half the corresponding diameters. Require these relationships, with errors naming the offending fields:

```python
N < H
xf < xr
xr - xf >= 2 * ra + t
L - D >= L / 2 + t  # pocket stays in the flat rear housing
W / 2 >= rp + t
zp - rp >= g + t
zp + rp <= g + H - t
za - ra >= g + t
za + ra + t <= zp - rp  # conservative separation, even without X overlap

# For each x in (xf, xr):
x - ra >= t
x + ra <= L - t
za + ra + t <= g + N + (H - N) * min(2 * (x - ra) / L, 1)
```

These analytic constraints apply only to the fixed wedge and cylindrical tools. They are not whole-body thickness certification. Reject otherwise plausible geometry outside this supported envelope rather than adding more shapes or solvers.

Public functions, defined by the implementation tasks:

```python
def canonical_spec(spec: DragsterSpec) -> str:
    return json.dumps(spec.model_dump(mode='json'), sort_keys=True,
                      separators=(',', ':'), allow_nan=False)

def specification_digest(spec: DragsterSpec) -> str:
    return hashlib.sha256(canonical_spec(spec).encode('utf-8')).hexdigest()

# geometry.py: build_body(spec: DragsterSpec) -> cq.Shape
# validation.py: verify_step(path: Path, spec: DragsterSpec) -> dict
# cli.py: main(argv: list[str] | None = None) -> int
```

## Implementation tasks

Use test-first cycles within each task: add the named failing assertion, run it and observe the expected failure, add the implementation, then rerun. Small commits after coherent green steps are enough; do not create a commit for every assertion.

### 1. Establish the environment and package

- [ ] Record `git rev-parse HEAD` and `git status --short`; create `feat/co2-dragster-wedge` from the planning branch in an isolated checkout. Leave unrelated working trees untouched.
- [ ] Read applicable `AGENTS.md` instructions and the existing context files listed above.
- [ ] Add package marker and spec tests. First assertion: importing `DragsterSpec` fails before implementation. Native preflight: `python -c "import cadquery, pydantic; print(cadquery.__version__, pydantic.__version__)"`; expected versions in the pinned environment are `2.6.1 2.9.2`.
- [ ] If native Python is absent, build `docker build -f Dockerfile.verify -t mirum-co2-session .`. Do not install a new geometry engine or claim CAD success from mocks.

### 2. Implement the strict spec and fixture

- [ ] Write `test_dragster_spec.py`: accept the exact example; reject missing/unknown fields, invalid version/style/units, boolean dimensions/version, NaN/Infinity, each scalar bound violation, and each relationship violation above. Parameterize negative cases so failures identify which contract broke.
- [ ] Implement the required Pydantic fields, relationship validator, `canonical_spec`, and `specification_digest` in `spec.py`; keep it independent of CadQuery and provider/app imports.
- [ ] Add the exact example JSON above to `backend/dragsters/examples/wedge.json`. Assert `canonical_spec(DragsterSpec.model_validate_json(canonical_spec(spec))) == canonical_spec(spec)` and equal digests after reload.
- [ ] Run `python -m pytest -q backend/tests/test_dragster_spec.py`; expected: exit 0, all selected tests passed, no skips.

### 3. Build the wedge and real cutting tools

- [ ] Add native tests for one valid solid, absolute bounds, volume, and the three cuts. Import CadQuery directly; a missing native kernel must fail this suite, not skip it.
- [ ] Implement `build_body` with low-level CadQuery faces/prisms and cylinders, avoiding workplane-axis ambiguity. The core construction is:

```python
points = [(0, -W/2, g), (L, -W/2, g), (L, -W/2, g+H),
          (L/2, -W/2, g+H), (0, -W/2, g+N), (0, -W/2, g)]
wire = cq.Wire.makePolygon([cq.Vector(*p) for p in points])
body = cq.Solid.extrudeLinear(wire, [], cq.Vector(0, W, 0))
pocket = cq.Solid.makeCylinder(
    rp, D + 1.0, cq.Vector(L - D, 0, zp), cq.Vector(1, 0, 0))
body = body.cut(pocket)
for x in (xf, xr):
    bore = cq.Solid.makeCylinder(
        ra, W + 2.0, cq.Vector(x, -W/2 - 1.0, za), cq.Vector(0, 1, 0))
    body = body.cut(bore)
```

- [ ] `geometry.py` must assign all symbols from the validated spec, return the cut shape, and reject nonvalid/multisolid/zero-volume output. No `eval`, generated code, provider imports, or new sandbox permissions.
- [ ] Assert the expected wedge volume before cuts is `W * L * (N + 3 * H) / 4`. The supported relationships make both bore cuts and the pocket disjoint and fully contained except at their intended openings, so expected finished volume subtracts `2 * math.pi * ra**2 * W + math.pi * rp**2 * D`.

### 4. Verify the exported STEP, including negative geometry

- [ ] Write `verify_step` tests first. Export/reimport a correct body, an uncut wedge, a wedge with only one bore, a wrong-depth pocket, and an X-shifted complete body. Only the correct body passes. Add a disconnected second solid case.
- [ ] Reimport using `cq.importers.importStep(str(path))`; require exactly one valid solid, finite positive volume, and all six absolute bounds within `0.01 mm` of the spec. Compute geometric volume independently from the reimported solid; allow `max(0.1 mm^3, expected_volume * 1e-6)` deviation.
- [ ] Intersect the reimported body with separate expected pocket and bore probe cylinders bounded to their in-body lengths. Each intended void must contain at most `0.001 mm^3` of body material. Require the pocket at `(L-D,0,zp)` along +X and bores at `(xf,-W/2,za)` / `(xr,-W/2,za)` along +Y. Combine probes with expected volume and bounds; schema checks alone are insufficient.
- [ ] Record `schema_version`, `spec_sha256`, `step_sha256`, `generator_version='dragster_wedge_v1'`, CadQuery version, measured bounds/volume/solid count, individual check results, and `engineering_certified=false`. Identify wall/separation results as analytic fixed-wedge constraints; do not describe them as sampled manufacturing proof.
- [ ] Failed checks raise a clear validation error and never return a report with `passed=true`. STEP parse/I/O failures are also failures.
- [ ] Rebuild/reimport the same spec twice and compare bounds and volume within the same tolerances. Do not require equal STEP bytes or artifact SHA across separate runs.
- [ ] Run `python -m pytest -q backend/tests/test_dragster_spec.py backend/tests/test_dragster_geometry.py`; expected: exit 0, no skipped native cases.

### 5. Add the local CLI and safe result lifecycle

- [ ] Write `test_dragster_cli.py` for success, bad JSON/spec, forced export failure, forced check failure, forced report-write failure, and forced publication failure. Ensure previous output bytes remain unchanged after each failed new attempt.
- [ ] Implement the CLI with required `--spec PATH` and `--output-root PATH`. Load/validate the spec before importing/building native geometry. Use only repository code and the actual parsed JSON; never interpret JSON as Python.
- [ ] Allocate a unique staging directory under the output root. Export exactly one `body.step`, call `verify_step` on that file, then write canonical `spec.json` and `report.json`. Hash the same STEP bytes that will be published.
- [ ] Publish by renaming that staging directory to a fresh `dragster-<uuid>` sibling. Never replace a prior result or accept a caller-supplied overwrite target. Print the absolute accepted directory only after success. Clean only the staging path owned by the current attempt on failure.
- [ ] Return exit 2 for JSON/spec/argument errors; exit 1 for generation, checking, or storage errors; exit 0 only after all three outputs exist. Print concise errors on stderr, with no successful-result path on failures. Catch ordinary exceptions at the CLI boundary, retaining their useful reason.
- [ ] Rebuild the verification image after all source/test edits. The preflight image does not contain later files.
- [ ] Run `python -m pytest -q backend/tests/test_dragster_spec.py backend/tests/test_dragster_geometry.py backend/tests/test_dragster_cli.py`; expected: exit 0 and zero skips. In the pinned image use the same arguments after `docker run --rm --network none mirum-co2-session python -m pytest -q`.

### 6. Exercise the real command and document the result

- [ ] Run `python -m backend.dragsters.cli --spec backend/dragsters/examples/wedge.json --output-root output/dragsters`. Expected: exit 0; a new `dragster-<uuid>` directory containing nonempty `body.step`, `spec.json`, and `report.json`; report check status true with the matching spec/STEP digests. Save/reload that `spec.json` in a second run and compare measured geometry.
- [ ] For Docker-only native execution, run `docker run --rm --network none --tmpfs /tmp:rw,size=512m mirum-co2-session python -m backend.dragsters.cli --spec backend/dragsters/examples/wedge.json --output-root /tmp/dragsters`. This is an ephemeral smoke test; use a host bind mount for retained exports and verify the files from the host before claiming a retained deliverable.
- [ ] Run an invalid input with `pocket_center_z_mm=40.0` and all other example fields unchanged. Expected: exit 2 with the pocket-height relationship error and no new accepted output. Do not alter the committed example to run this case.
- [ ] Write `docs/dragsters/deterministic-wedge.md` with both local and Docker usage, editable fields, file meanings, demonstration-only dimensions, analytic-check limitations, and actual test counts/skips. Describe STEP as a solid import, not native CAD feature-history editing.
- [ ] Run `git diff --check`, inspect the changed-file list, and rerun focused tests after fixes. If only isolated new package/tests/docs changed and these pass, broader full-repository tests are optional; run them if inspection reveals integration changes. Do not spend the session repairing unrelated failures.
- [ ] Commit the prototype on its implementation branch. Report the branch/commit, exact commands, test counts/skips, example output location, limitations, and any blocker. Do not merge, deploy, or add another style in this session.

## Completion checklist

- [ ] Example JSON produces one measured single-solid STEP without a provider call or API key.
- [ ] Editing a supported dimension and rerunning changes the measured geometry as intended.
- [ ] Saved JSON regenerates equivalent geometry within the declared tolerances.
- [ ] Incorrect inputs and missing/misplaced geometry fail for demonstrated reasons.
- [ ] Every successful report refers to its exact saved spec and STEP bytes.
- [ ] Failed attempts publish no new accepted directory and preserve previous results.
- [ ] Focused tests include actual native CAD execution with zero skips; actual results are recorded.
- [ ] The local usage guide explains prototype limits and how to find/edit/export the result.

The next separate session can add a Streamlit form and STEP-derived GLB preview, then reuse the shared accepted-artifact pipeline. Those are follow-up work, not conditions for finishing this prototype.
