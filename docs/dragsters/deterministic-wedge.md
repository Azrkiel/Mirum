# Deterministic local wedge prototype

Generate one sharp, planar wedge body from explicit JSON using CadQuery. This
trusted local CLI does not use the application, providers, generated Python,
API keys, a browser, or shared artifacts. STEP imports as a solid; it does not
preserve native CAD feature history.

## Run and edit

From the repository root in a native environment with CadQuery 2.6.1,
CadQuery-OCP 7.8.1.1.post1, and Pydantic 2.9.2:

```sh
python -m backend.dragsters.cli \
  --spec backend/dragsters/examples/wedge.json \
  --output-root output/dragsters
```

The command prints the absolute accepted directory on success. Copy/edit the
example or a saved `spec.json`, then run again. Every success creates a fresh
`dragster-<uuid>` directory. Exit codes: 0 success, 2 invalid JSON/spec/arguments,
1 CAD, verification, file access, or storage failure. Errors go to stderr.
Invalid input is rejected before native geometry imports. Each attempt owns a
private staging directory, which is removed on failure; previous results are
never overwritten or swept.

For Docker, build **after** source/test changes and retain output with a bind
mount (an export left only inside a `--rm` container is ephemeral):

```sh
docker build -f Dockerfile.verify -t mirum-co2-session .
mkdir -p output/dragsters
# The image runs as UID 10001. Make the bind mount writable by that UID.
# On Linux, for a newly created output directory:
sudo chown 10001:10001 output/dragsters
docker run --rm --network none \
  --mount type=bind,src="$(pwd)/output/dragsters",dst=/results \
  mirum-co2-session python -m backend.dragsters.cli \
  --spec backend/dragsters/examples/wedge.json --output-root /results
```

The Docker command prints the container path; find the same run name under the
host's `output/dragsters`. On systems without POSIX ownership, configure the
mount's write permissions using the host's Docker file-sharing controls.

## Parameter meanings

Every field is required. Unknown fields, strings/booleans used as dimensions,
nonfinite values, and unsupported versions/styles/units fail. There are no
implicit dimensions, unit conversions, or fit allowances. The version is the
integer `1`; style and units are exactly `wedge` and `mm`.

X=0 is the nose; X=length is the rear. Y=0 is the symmetry plane; Z=0 is ground.
The bottom is at ground clearance. Heights are measured **above the bottom**;
axle/pocket Z coordinates are **absolute above ground**. The top rises linearly
through the first half of X and stays flat through the rear half.

| Fields | Meaning | Inclusive scalar range (mm) |
| --- | --- | --- |
| `length_mm` | Nose to rear | 160–260 |
| `width_mm` | Constant body width, centered on Y=0 | 32–60 |
| `height_mm` | Full rear height above bottom | 28–45 |
| `nose_height_mm` | Nose height above bottom | 8–18 |
| `ground_clearance_mm` | Bottom Z | 0–10 |
| `front_axle_x_mm`, `rear_axle_x_mm` | Transverse bore X positions | 10–250 |
| `axle_z_mm` | Shared bore center Z | 4–24 |
| `axle_bore_diameter_mm` | Actual transverse through-cut diameter | 2–5 |
| `pocket_diameter_mm` | Actual rear-open cylindrical cut diameter | 12–24 |
| `pocket_depth_mm` | Blind pocket depth from rear along −X | 30–80 |
| `pocket_center_z_mm` | Pocket axis Z, centered on Y=0 | 15–45 |
| `min_wall_mm` | Analytic wall/separation lower bound | 2–5 |

Scalar bounds alone do not guarantee acceptance. The
[frozen parameter contract](../plans/2026-11-05-co2-dragster-deterministic-session.md#frozen-prototype-contract)
also checks axle ordering, bore/end/top clearances, pocket containment in the
flat rear housing, side/top/bottom walls, and conservative vertical separation
between bores and pocket. Dimensions outside that envelope are rejected.

## Saved files and checks

Each accepted directory contains exactly:

- `body.step`: the exported body that was reimported and measured.
- `spec.json`: canonical resolved JSON, with every explicit field preserved.
- `report.json`: spec/STEP SHA-256 digests, schema/generator/CadQuery versions,
  measured absolute bounds, volume, solid count, individual check evidence,
  `passed=true`, and `engineering_certified=false`.

Verification requires one valid solid, finite positive volume, all six absolute
bounds within 0.01 mm, and agreement with the independent analytic cut volume
within `max(0.1 mm³, expected_volume × 1e-6)`. Separate cylinders probe the entire
intended in-body pocket and both bores; each permits at most 0.001 mm³ of residual
body material. Missing cuts, wrong depth, displaced geometry, and disconnected
solids fail. Even equal-volume misplaced cuts fail the probes.

Spec hashes refer to the exact saved canonical UTF-8 JSON; STEP hashes refer to
the exact saved STEP bytes. Regeneration compares measured geometry, not STEP
byte equality (export metadata may vary).

## Native verification evidence — 2026-11-05

Implementation starts at planning commit
`7c9e5fd73ad4b6264b7dcc245b8b012f7082df55`, from the clean baseline
`36b996f91b0c17c54710a24545cce6acc131ff1f`, in isolated worktree
`/workspace/work/co2-dragster`, branch `feat/co2-dragster-wedge`. No applicable
`AGENTS.md` files were found in the checkout or ancestor directories.

Native execution used Python 3.12.14 with CadQuery 2.6.1,
CadQuery-OCP 7.8.1.1.post1, Pydantic 2.9.2, and pytest 8.3.3 in
`/workspace/work/co2-native`. This used the real local OpenCASCADE kernel;
Docker was not needed and the full Python 3.10 verification image was not built.
The environment reused installed CAD transitive dependencies; it was not a
full installation of `requirements-linux.lock`.

```sh
/workspace/work/co2-native/bin/python -c \
  'import cadquery,pydantic; print(cadquery.__version__,pydantic.__version__)'
# 2.6.1 2.9.2
/workspace/work/co2-native/bin/python -m pytest -q \
  backend/tests/test_dragster_spec.py \
  backend/tests/test_dragster_geometry.py \
  backend/tests/test_dragster_cli.py
```

Result: **164 passed, zero skipped**. Initial spec, geometry, and CLI test
collections failed on their missing implementation modules before implementation.
Native tests cover bad/missing STEP files, round trips, cut probes, changed
parameters, and prior-output preservation under invalid input and injected
build/export/check/report/publication/storage failures. Dependency deprecation
warnings are unrelated to geometry acceptance.

Standalone commands were also exercised using the same interpreter:

```sh
python -m backend.dragsters.cli --spec backend/dragsters/examples/wedge.json --output-root output/dragsters
python -m backend.dragsters.cli --spec output/dragsters/dragster-d418fea8d260480f8158f19662ff2dfe/spec.json --output-root output/dragsters
python -m backend.dragsters.cli --spec work/invalid-pocket.json --output-root output/dragsters
python -m backend.dragsters.cli --spec work/wider.json --output-root output/dragsters
```

Retained runs under `/workspace/work/co2-dragster/output/dragsters/`:

| Run | Purpose |
| --- | --- |
| `dragster-d418fea8d260480f8158f19662ff2dfe` | Exact example |
| `dragster-d6a1addab38b4d5d836c35262fa22f19` | Regenerated from first run's saved JSON |
| `dragster-3a75b9cb1dd94316a365f5b367e65733` | Width edited to 46 mm |

Original/reloaded bounds: X=0–200, Y=−21–21, Z=3–35 mm (within tolerance);
volume 209869.957189815 mm³. Edited width: Y=−23–23 mm;
volume 231413.408522051 mm³. Saved digests matched the files on disk.
`work/invalid-pocket.json` changed only `pocket_center_z_mm` to 40.0;
it exited 2 with the pocket-height relationship error, printed no result path,
created no accepted output, and left prior output bytes unchanged. Scratch
inputs and execution evidence are local under `work/`, outside the commit.

## Limits

The example dimensions demonstrate the prototype; they specify no verified
cartridge fit or competition preset. Analytic wall/separation constraints apply
only to this wedge and these cylinders; they do not certify whole-body thickness,
manufacturing safety, cartridge/pressure standards, strength, compliance, or
performance. There are no wheels, assemblies, fillets, extra styles, UI, GLB,
API integration, or model calls. This is trusted local code, not a hostile-input
execution service.
