# CEM continuation: segmented polyhedral wing, sleeves and bond layers

This continues the converged rod and foam-core work on `cem-next-phase` from
`c483e17b7a01f593c88ce36c89e5e142d5c2b163`. It resolves the straight-rod/dihedral
geometry restriction for a bounded polyhedral construction and produces a
positive **screening** result. It does not complete G2/G3 qualification.

For the complete continuation, use the
[`cem-next-phase` branch](https://github.com/Azrkiel/Mirum/tree/cem-next-phase).
It includes the converged rod, straight foam-core and segmented-wing slices
together. The earlier `engineering/cem-glider` branch remains their starting point.

## Construction and mass

Use `construction="segmented_foam_wing_v1"` with `process="sheet_foam"`.
The three-station polyhedral layout is required. Other processes, `auto` and
other wing layouts cannot silently substitute this construction. The default
OML mode and previous straight foam-core mode retain their own checks.

The builder follows the airfoil camber line with four straight carbon segments.
It cuts the original OML at shared joint planes to produce four foam cores,
without changing the exterior aerodynamic shape. Oblique channel ends meet
those planes. Three continuous mitered sleeves and three explicit adhesive
layers join the rods: one at the centre and two at the panel kinks. These are
14 closed material parts, plus the existing three tail OMLs and pod OML: 18
accounted STLs for the conventional reference layout.

One polygon frame is transported along the entire path. Adjacent rods, adhesive
bores and sleeve bores therefore share their interfaces without small polygon
phase overlaps. Channel clearance is at least 0.25 mm; the foam web is at least
1 mm. Every panel must fit 20 mm stock in its cutting frame. Complete clipped
surfaces are checked against the original loft cells. A single convex-kernel
test across a kink would falsely reject its small concave region; splitting
at the original section planes preserves the conservative halfspace guarantee.
The acceptance test does not use sparse point samples.

Rod diameter is selected from the existing catalogue using the mass of all
cores, rods, sleeves and adhesive, plus tail/pod, payload, ballast and hardware.
The construction supplies material volume, centroid and intrinsic inertia to
sizing and stability. Closure reimports the exact STLs and uses measured total
mass. Tail/pod remain skin-equivalent; payload/ballast/hardware remain separately
placed point masses. Their physical construction is still incomplete.

## Structural evidence and its scope

Every delivered rod is sectioned in its own axis frame. Area, centroid,
principal second moments, polar second moment and extreme radius come from the
complete intersection polygons. Each sleeve lap is measured as an annulus;
overlap comes from actual sleeve/adhesive extents. The plane-intersection graph
joins only coincident endpoints within 1e-10 m and rejects open/branched loops.
It avoids Trimesh Path3D simplification, which a regression found could omit a
polygon corner on an oblique cut. Input material meshes are never repaired.

The launch model remains ideal elliptical total lift. Its closed-form root
moment is `n W b / (3 pi)` and half-wing shear is `n W / 2`. A bound on the
path's `|dx/dy|` bounds the swept-path moment. Decreasing moment/shear bounds
at the inboard endpoint cover the whole segment; 17 station records document
the distribution. Bending, transverse shear and an approximate polygon-torsion
screen use the measured section. This is beam screening, not solid FEA.

Sleeve/bond checks explicitly use engineering priors:

- Carbon: existing 1600 kg/m3, 400 MPa allowable, safety factor 2.
- Sleeve: assumed homogeneous 1850 kg/m3 material, 250 MPa allowable, safety
  factor 2, 0.6 mm wall, 30 mm laps and concentration factor 2.
- Adhesive: 1100 kg/m3, 0.15 mm layer, assumed 8 MPa shear allowable and safety
  factor 2. Bending-transfer and direct shear demands are added conservatively.
- Near-circular polygon torsion includes an explicit 10% modeling allowance.

These are version-bound assumptions, **not supplier-qualified allowables or
coupon-test evidence**. Sleeve fabrication/laminate orientation, adhesive peel,
defects, environmental degradation and load cycling are unqualified. The
numerical checks can pass while `load_path_state="unverified"` remains true:
foam-to-structure load transfer and the wing-to-body saddle are not established.
`accepted_for_manufacture`, `manufacturing_ready` and `engineering_certified`
remain false. No buckling, deflection, fatigue or flight qualification is added.

## Parent acceptance

Canonical inventory now includes each core, rod, sleeve and bond. Missing,
duplicate, renamed, extra or unlisted parts fail accounting. The segmented
manifest limit is bounded at 32; the older modes retain their limit of 16.
The existing worker, facet, ZIP, shared-slot and timeout caps remain in effect.
The implementation uses the pinned packages already installed.

Parent validation reimports and remeasures every part, reconstructs total
aircraft mass, compares the complete material surfaces against the construction
revision, recomputes installed clearance and section/joint checks, and validates
the report and ZIP bindings. A shifted joint with refreshed file hashes and
mass measurements still fails. Altered allowables, overlaps, stresses, section
inertias, load mass or qualification claims cannot replace that evidence.

As with the straight physical mode, the old areal-mass feasibility bound is
withheld and maximization continues. The 1200 m reference remains advisory.
An optional hard minimum still applies separately at P10.

## Reproduce

From the existing `/workspace/Mirum` checkout, use the retained pinned Python
environment. Do not create a worktree or discard local changes.

```sh
PYTHONPATH=backend OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
XDG_CACHE_HOME=/tmp/mirum/cache MPLCONFIGDIR=/tmp/mirum/matplotlib \
/workspace/.venv-glider/bin/python -m uvicorn cem.app:app \
  --host 127.0.0.1 --port 8013
```

Submit `examples/cem/glider-segmented-foam.json` to the ordinary feasibility and
discovery endpoints. Poll discovery, select an exact returned elite, submit
design and poll until complete. A screened result exposes the hash-bound bundle;
a rejected result must not. Restart the host after source changes because
provenance is cached. The typed workflow requires no provider key.

Focused verification:

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
XDG_CACHE_HOME=/tmp/mirum/cache MPLCONFIGDIR=/tmp/mirum/matplotlib \
/workspace/.venv-glider/bin/python -m pytest -q \
  backend/tests/test_cem_segmented.py backend/tests/test_cem_foam_core.py \
  backend/tests/test_cem_structure.py backend/tests/test_cem_geometry_integrity.py \
  backend/tests/test_cem_requirements.py backend/tests/test_cem_api.py \
  backend/tests/test_cem_pipeline.py frontend/test_glider_ui.py \
  backend/tests/test_glider_model.py backend/tests/test_glider_search.py \
  backend/tests/test_glider_explorer.py backend/tests/test_glider_flight_conditions.py
```

Use a socket-enabled execution context for API/TestClient checks. Tests cover
oblique section golden values, volume partition, shared polygon interfaces,
positive real CAD/trim/closure/ZIP validation, determinism, amplified loads,
undersized actual sections, shortened laps, omitted material, 20% mass error,
forged joint/section evidence and rehashed/remeasured misplaced geometry.
No independent XFOIL/AVL, clean Docker image, paid provider or physical flight
validation is implied.

Final current-instance evidence: the combined command completed **209 passed**,
with one existing Starlette/AnyIO deprecation warning. `git diff --check` passed.
After restarting the host on the final source, the eight-evaluation HTTP request
returned two candidates. The selected build completed as `screened`, then
downloaded an HTTP-200 ZIP with 18 STLs, matching report identity and SHA-256.
The measured aircraft mass was 0.1948478945992522 kg; minimum delivered channel
clearance was 0.0002499852589563671 m (within the 0.2 micrometre STL tolerance
of 0.25 mm). The nominal screening range was about 123.095 m from a 10 m drop,
against the advisory 1200 m stretch goal. These are version/runtime-bound model
predictions, not physical flight measurements. No commit, clean verification
image build, deployment or environment publication is implied.

## Next continuation

1. Construct the foam-to-spar/sleeve potting or bond interface and wing saddle;
   include their actual material volume and verify the load route into the pod.
2. Add independent, provenance-bound material/coupon and adhesive test records.
   Never promote these priors to test-qualified data without that evidence.
3. Finish physical tail/pod and payload/ballast cavities, and their process and
   fit checks. Preserve the bounded geometry/trim rebuild loop and artifact binds.
4. Qualify stiffness, buckling and independent aerodynamic rechecks before
   claiming a fabrication-ready aircraft or verified novel-family gains.

The new positive result completes this bounded installed-segment/joint screen;
it does not authorize skipping those remaining physical-closure steps.
