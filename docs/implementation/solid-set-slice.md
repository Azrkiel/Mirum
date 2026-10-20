# Structural solid-set slice

## Interface and scope

`backend.verification.solid_set.inspect_step(step_path: str) -> dict`
returns `scope: "structural_solid_set_v1"`, `passed`, boolean `checks`, static
public-safe `failures`, `volume_mm3`, `surface_area_mm2`, bounding-box dimensions
`{x_mm, y_mm, z_mm}`, `solid_count`, and `max_topology_tolerance_mm`.
Unavailable measurements remain `None`; measurements obtained before a later
rejection are diagnostic only. `checks` records predicates actually reached;
absent checks must not be interpreted as passes. All required checks must finish
before `passed` becomes true.

This is an independent structural acceptance function, **not** a replacement for
or extension of the legacy geometry certificate. It does not spawn processes.
The parent must invoke it inside its bounded ProcessRunner worker and handle
worker timeout, native crash, memory limit, and publication/artifact binding.

## Native acceptance policy

- OCP only, imported lazily. No pythonocc, CadQuery, mesh or synthetic fallback.
- Read and transfer every reported STEP root; reject incomplete transfer, no
  roots, null shape, missing backend, missing file and native exceptions.
- Reject files above 500 MiB before calling the native reader.
- Walk direct topology children with an explicit iterative stack. Outside a
  solid, only compound/compsolid wrappers and complete solids are allowed.
  Reject loose shells/faces/wires/edges/vertices and empty wrappers. Within
  solids, enforce native solid/shell/face/wire/edge/vertex containment. Native
  face/edge geometry leaves need not have children; native validity still applies.
- At most 64 solid occurrences (raised from 32 so the 50-part manifest limit fits); at most 100000 traversed topology occurrences,
  including the root and repeated/shared subshapes. This conservative occurrence
  budget also bounds the traversal stack; it is not a unique-TShape count.
- Every shell must be closed according to `BRep_Tool.IsClosed_s`; every solid
  must pass `BRepCheck_Analyzer(solid, True)` with geometry checking enabled.
- Every solid must have finite strictly positive native volume and surface area.
  Aggregate metrics sum body values, including both sides of a contacting
  interface; surface area is not the exterior area of a fused union.
- Bounds use `BRepBndLib.AddOptimal_s(shape, box, False, False)`: no triangulation
  and no tolerance inflation. Validate both each solid's box and the full box.
- Imported face, edge and vertex tolerances must be finite, strictly positive,
  and at most **0.001 mm**. This is an explicit application numerical policy,
  **not manufacturing tolerance, GD&T, accuracy or engineering sign-off**.
- Use each solid's optimal box as a broad phase, conservatively padded by 0.002 mm
  (twice the allowed topology-tolerance ceiling). For remaining pairs, execute
  actual `BRepAlgoAPI_Common`, check operation completion and non-null output,
  and reject invalid/nonfinite/negative intersection mass or any positive native
  closed-volume intersection. There is no positive overlap-volume allowance and
  no fuzzy Boolean tolerance added. Face-touching and separated bodies remain
  distinct legitimate bodies, not fused into one solid.

## Verification evidence

The focused test file builds genuine OCP shapes, exports STEP, and reimports via
this inspector. The first real RED was a missing inspector (1 failed). Subsequent
RED cycles demonstrated false acceptance of loose topology (5 failed), overlapping
solids (3 failed), missing numerical/resource policies, and partial root transfer.
Retained evidence:

- `.hermes/solid-set-red.log`: actual failing test output from successive cycles.
- `.hermes/solid-set-green.log`: successive green outputs; final focused run:
  **37 passed in 3.71s**.

Coverage includes a known 10 mm box, solid plus each loose topology type, open
shell, nested compounds/compsolids, coincident and overlapping boxes (including
0.001 mm penetration), separated and face-touching boxes, corrupt/empty STEP,
missing file/backend, null shape, transfer failure/partial transfer, native
exception redaction, sparse file above 500 MiB, 65-solid rejection, 32- and 50-solid
acceptance. A lowered traversal budget exercises topology-limit rejection using
real transferred STEP rather than building a very large hostile fixture.

The deployment STEP reader normalized a native 0.01 mm tolerance to 1e-7 mm.
Therefore excessive-tolerance and empty-wrapper/open-solid rejection cases use
an explicitly labelled reader seam returning genuine native shapes after a real
STEP read/transfer. Nonfinite/nonpositive native tolerance returns and reader
errors use fault injection; these are not claimed as transferable STEP fixtures.

Exact focused invocation (Windows Git Bash, host-side `tee`):

```bash
set -o pipefail
MSYS_NO_PATHCONV=1 docker run --rm --network none --read-only --tmpfs /tmp \
  -v 'C:/Users/jiang/Mirum-brep-phase1:/workspace:ro' -w /workspace \
  -e PYTHONPATH=/workspace:/workspace/backend \
  mirum-integration-verify:latest python -m pytest \
  backend/tests/test_solid_set_acceptance.py -q -p no:cacheprovider \
  2>&1 | tee -a .hermes/solid-set-green.log
```

The read-only source mount overrides image source; no network/model call or full
suite was run. Integration with the parent's worker and full gate is parent-owned.

## Limits and interpretation

This accepts the **imported** native representation, which STEP translation may
heal/normalize. It does not compare the STEP model with source design intent or
prove that every source-file entity survived translation unchanged. Kernel
checks and Booleans have finite precision and implementation limitations.
**Zero Boolean volume is not proof of exact disjointness/equivalence**, nor is
this a universal mathematical proof against self-interference or all pathological
B-reps. Near-kernel overlaps can be absorbed by native tolerances. Optimal bounds
and mass integrals are native numerical evidence, not symbolic exact results.
Application mm measurements rely on the deployment STEP reader's standard mm
system-unit convention. File bytes must remain stable during inspection; path
containment, immutable publication and evidence binding belong to the parent.

Not evaluated: typed feature/design conformity, joints, clearance intent,
manufacturing process capability, physical strength, regulatory compliance,
full self-intersection proof, or as-manufactured geometry.
