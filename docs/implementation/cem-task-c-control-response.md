# Task C continuation: native control-response evidence

Continues `aa2cad80bd95e409235fdf7da3b36d889d4b5e27`. The development
workflow now measures the installed pitch and roll channels rather than only
demonstrating a selected deflection in a test. Production admission remains
closed for tailless swept, canard and tandem aircraft.

`families/control_response.py` reconstructs the exact family archive and freezes
neutral, negative and positive states for every declared control. It validates
all states before running the native VLM solver. Each channel moves independently
about neutral; the tandem's overlapping rear elevator and aileron therefore do
not invent a composite hinge. All other controls, inventory, CG, alpha and speed
remain fixed. The report retains force and moment vectors in geometry axes,
lifting coefficients, exact control values and installed-coordinate identities.

Central differences are reported **per radian**, at two explicitly selected mesh
resolutions, with the signed changes between those derivatives. The deflection
step is bounded to 0.1–10 degrees, alpha to -10–15 degrees and speed to 1–15 m/s.
The default step is 2 degrees; coarse/fine meshes default to 3/6. The three
families need respectively 10, 10 and 14 native solves, including a neutral solve
on each mesh. Nonfinite results, saturation and unsupported installed states
raise errors; they are not converted to zero response or passing evidence.

These are **fixed-operating-point inviscid responses**. They do not solve force
or moment equilibrium, determine required control moments, demonstrate authority
through a nonlinear stall envelope, establish dynamic stability or qualify a
physical aircraft. There is no response acceptance threshold or performance-gain
claim. `qualified_control_authority`, `production_admission`,
`independent_numerical_performance`, `physical_construction` and `verified_gain`
are false. API metadata advertises the response capability separately and
explicitly leaves qualified authority false. Production selectors are unchanged.

## Reproduce and replay

Use the existing checkout; do not create a worktree unless explicitly requested.
The environment install script prepares `/workspace/mirum-env.sh` and the pinned
native tools. Given an archive from the existing development workflow:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
PYTHONPATH=backend python -m cem.glider.families.native_workflow control-response \
  /path/to/family-archive.json --output /tmp/new-control-response.json
```

Optional `--conditions` accepts a JSON object with `altitude_m`, `alpha_deg`,
`speed_m_s`, `step_deg`, `coarse_resolution` and `fine_resolution`. The output
must be a new file; existing evidence is preserved. The standalone
`python -m cem.glider.families.control_response` CLI accepts the same arguments.

```python
import json
from cem.glider.families.native_workflow import replay
report = json.load(open('/tmp/new-control-response.json'))
assert replay(report) is False
```

Replay reruns the native solver, reconstructs the installed coordinates, and
compares the complete report including implementation/runtime identities.
`False` means the evidence grants no production admission; altered evidence
raises `ValueError`. Updating a report's SHA-256 cannot grant acceptance.

## Remaining acceptance dependencies

The full Task C acceptance described in [the handoff](cem-task-c-handoff.md)
and [the previous continuation](cem-task-c-continuation.md) remains open:

| Gate | Required next evidence or implementation |
| --- | --- |
| Qualified control authority | Required moments and operating envelope, family reference cases, step/refinement validation and comparison against a qualified independent method or measurements |
| Whole-aircraft aerodynamics | Body/profile drag, full force/moment closure, local flow and Reynolds coverage, family-scoped two-way interaction/stall checks |
| Stability and directional control | Actual yaw-control geometry, full inertia, dynamic modes and stable/unstable reference cases |
| Physical construction | Family body attachments, swept torsion/control loads, hardware/hinge inventory, load paths, material/joint calibration and delivered-part closure |
| Production integration | Admit each family only after its A/B evidence is qualified; then integrate workers, search, comparisons, archives and manufacturing bundles |

No measured aircraft dataset or construction calibration was supplied in this
continuation. Native response calculations cannot close those reference-dependent
gates. The computational examples are replay inputs, not measured validation.
No deployment or environment publication is performed by the code changes.

## Current-instance evidence

Full CLI reports for all three families, their input archives and conditions are
under `/workspace/mirum-family-control-response`. Each report was independently
replayed. A compact tracked summary is in
[`../testing/cem-family-control-response.json`](../testing/cem-family-control-response.json).
This directory must be snapshotted by environment publication to persist in a
future task; the tracked summary also retains the inputs needed to recompute it.

Tests exercise every family's native symmetric pitch and opposite roll response,
six-axis output, fixed inventory, invalid conditions, nonfinite rejection,
report/hash/coordinate-identity tampering, promotion rejection, and CLI output
preservation. API checks cover metadata, typed mission feasibility and HTTP 422
for each unqualified-family-only production search. Streamlit health passes.

- Combined CEM/glider/UI/AVL/XFOIL regression: **488 passed, no skips**, two
  dependency deprecation warnings, 341.31 seconds. Native references executed.
- Final affected control-response/workflow/family/API tests: **87 passed**,
  one dependency deprecation warning, 58.62 seconds.
- Standalone CLI runs and parent replay completed for all three families using
  meshes 3/6. Existing-file CLI rejection preserved the original report bytes.
- Repeat setup, dependency compatibility, source/patch/native-binary hash checks,
  CAD/solver imports and `git diff --check` passed.
- API and UI were restarted using the saved startup commands. Activation sets
  `BACKEND_URL=http://127.0.0.1:8000` so native Streamlit uses the local API.
- Environment draft `install_script`, `start_skill` and repository base
  `aa2cad80bd95e409235fdf7da3b36d889d4b5e27` at `Mirum` were saved. The
  continuation was uncommitted when validation was recorded; see Git history for
  subsequent commits. Review/save in environment settings and
  publish to snapshot the prepared files. Publication and restoration in a new
  task remain untested.
