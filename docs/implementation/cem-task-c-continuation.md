# Task C software continuation

Continues branch handoff `956ceaac6e4264034a4c65a0dd59462a820d1f34`.
The user explicitly requested leaving reference-dependent qualification gates
open because no reference dataset/design package is available. This continuation
does not admit any new family to production or physical construction.

## Delivered development workflow

`families/installed.py` adds `mirum-family-coordinates-1`, an isolated development
representation. It retains exact reflexed airfoil coordinates, incidence, washout,
reference dimensions, screened CG and every explicit control state. Left and
right halves are separate. Control span boundaries split the lifting geometry;
deflection rotates actual foil coordinates rather than attaching ignored labels.
Positive deflection is trailing-edge down; antisymmetric channels reverse on the
left half. Summed pitch/roll deflection must respect the physical surface limit.
Simultaneously deflecting overlapping controls with different hinges is rejected
instead of inventing a composite mechanism. Neutral tandem geometry remains usable.

Reconstruction checks the complete coordinate/state payload, source identity,
package version and archive. Rehashing altered coordinates or promotion flags
does not make the payload valid. This representation is deliberately separate
from A's unchanged `mirum-aero-1`; its NACA/neutral-control restrictions remain.
No existing solver is silently made to accept coordinates it cannot represent.

`families/native_workflow.py` supplies native development search, paired comparison
and exports for all three families. Search has a bounded 1–24 evaluation budget
and deterministic seed. Incidence/washout vary; inventory and controls remain
fixed. Both comparison arms reconstruct their geometry and retain identical
inventory, control topology/hinges, deflections and operating conditions.
Neither arm can claim a performance delta or verified gain.

Diagnostics now accept explicit installed controls. Reports identify equilibrium
as **lift/weight and pitching moment only**. They retain raw six-axis force/moment
output and solver derivatives, two mesh levels, all root-bracketing samples and
geometry/condition/implementation identities. Control-coordinate effects are
exercised in native tests for all three families, including swept pitch response
and opposite roll responses. They are not calibrated authority limits.

Exports contain the original screened lifting OML, controlled lifting meshes in
mm, installed coordinates, exact archive, diagnostic report and artifact hashes.
Parent replay recomputes native calculations and rebuilds mesh bytes; replacing
the mesh and its manifest hash cannot grant acceptance. Exports require an empty
directory and CLI JSON output refuses existing files. Rejected and mesh-sensitive
diagnostics remain explicitly labelled development artifacts.

The API advertises these development capabilities separately. Production family
selectors, admission and verified-gain gates remain closed.

## Reproduce

Use the existing checkout; do not create a worktree unless explicitly requested.
Activate `/workspace/mirum-env.sh` after the environment install script completes.
The provider-free API is `uvicorn cem.app:app` with `PYTHONPATH=backend`; the
separate full `main:app` backend requires a genuine Gemini key.

The compact current-instance evidence in
[`../testing/cem-family-native-continuation.json`](../testing/cem-family-native-continuation.json)
contains complete reference archives, conditions, source/package identities,
diagnostic results and raw artifact hashes. These are computational reference
inputs, not measured validation datasets. Extract an input archive and conditions:

```sh
cd /workspace/Mirum
source /workspace/mirum-env.sh
python - <<'PY'
import json
from pathlib import Path
r = json.loads(Path('docs/testing/cem-family-native-continuation.json').read_text())
out = Path('/tmp/task-c-reference')
out.mkdir(exist_ok=True)
for name, case in r['families'].items():
    (out/(name+'.json')).write_text(json.dumps(case['geometry']))
(out/'conditions.json').write_text(json.dumps(r['conditions']))
PY
PYTHONPATH=backend python -m cem.glider.families.native_workflow search \
  /tmp/task-c-reference/tailless_swept.json --budget 2 --seed 42 \
  --conditions /tmp/task-c-reference/conditions.json --output /tmp/task-c-search.json
PYTHONPATH=backend python -m cem.glider.families.native_workflow compare \
  /tmp/task-c-reference/canard.json /tmp/task-c-reference/canard.json \
  --conditions /tmp/task-c-reference/conditions.json --output /tmp/task-c-compare.json
PYTHONPATH=backend python -m cem.glider.families.native_workflow export \
  /tmp/task-c-reference/tandem.json --conditions /tmp/task-c-reference/conditions.json \
  --output /tmp/task-c-export
```

`--controls` accepts JSON with an explicit degree value for every declared
control. Omitting it installs neutral controls, which are recorded explicitly.
The standalone diagnostic CLI also supports `--controls`.

From Python, `native_workflow.replay(report)` and
`native_workflow.validate_export(directory)` recompute the evidence. Successful
reconstruction returns `False` for production admission by design.

## Qualification boundaries retained

- Inviscid results omit profile/body drag, nonlinear interaction/stall envelopes,
  dynamic modes, and full force/moment/trajectory equilibrium. A converged
  pitch/lift diagnostic does not establish glide performance or stability.
- Body OML, directional control/yaw strategy, hardware/hinge mass, measured full
  inertia and family-specific construction are absent. Screened inventory/CG
  stays fixed during a control diagnostic; it is not a remeasured deflected build.
- B's narrow straight-wing construction is not transferred to these families.
  Family attachments, swept torsion/clearance, body load paths and complete
  launch/gust/landing sizing remain unqualified. Existing A/B vector/couple
  handoff and reconstruction tests are preserved.
- No material/joint calibration, stall/stability benchmark or measured aircraft
  reference was supplied. The user chose to leave these gates open. Once these
  dependencies are qualified, production registration, worker/archive evidence,
  search/comparison/design delivery and API/UI admission must be integrated and
  tested for each qualified family independently. The development CLI does not
  replace that production acceptance work.
- No deployment, push, environment publication or fresh-task restoration is
  claimed. Raw native reports and meshes are currently under
  `/workspace/mirum-family-final`; publication is needed to snapshot this local
  environment. The compact tracked report preserves inputs, results and hashes
  without committing meshes.

See the original [C handoff](cem-task-c-handoff.md),
[A continuation](cem-task-a-handoff.md), and
[B qualification handoff](cem-task-b-qualification-handoff.md) for the remaining
engineering acceptance requirements.

## Current-instance validation

- Combined CEM/glider/UI/AVL/XFOIL regression: **470 passed**, no skips,
  two existing dependency deprecation warnings, in 275.75 seconds. Native
  XFOIL panel refinement and AVL references executed using the pinned builds.
- Final affected coordinate/workflow/diagnostic/shared-contract checks:
  **36 passed** in 37.68 seconds after the final report-scope changes. This
  includes the three refined controlled-geometry positive diagnostics.
- Native two-evaluation searches, paired comparisons, exports and parent
  reconstruction were exercised for all three families. Fine-mesh diagnostic
  evidence is recorded in the compact report. Mesh-sensitive or unsuccessful
  arms retain their actual state; no performance gain is asserted.
- The search, comparison and export CLIs executed against actual archives.
  API metadata/feasibility and Streamlit health returned successful responses;
  all three unqualified-family-only production searches returned HTTP 422.
- Dependency compatibility, repeat installation, native-binary hash verification
  and `git diff --check` passed. Environment draft `install_script` and
  `start_skill` were saved. Review/save in environment settings and publish to
  activate the prepared environment; publication/restoration remain untested.
