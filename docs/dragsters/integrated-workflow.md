# Integrated deterministic wedge workflow

This slice starts from `862aac0871c3cab8e8de7f5a83505a5c8857c0b9` on
`feat/co2-dragster-local-preview`. It adds **CO2 dragster** to the main Mirum
workflow selector. The frozen version-1 wedge contract, geometry implementation,
CLI arguments/exit codes/three-file contract, and standalone editor remain usable.
See [the parameter guide](deterministic-wedge.md) for dimensions and tolerances.

## Setup and launch

Use Linux and Python 3.10+ with the native CAD dependencies. From the repository
root, a reproducible verification environment is supplied by `Dockerfile.verify`:

```sh
docker build -f Dockerfile.verify -t mirum-integrated-verify .
docker run --rm --network none --tmpfs /tmp:rw,size=1g mirum-integrated-verify
```

For native local development, install the repository locks into a virtualenv:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-linux.lock \
  -r requirements-mirum-verification.lock -r requirements-glider.lock \
  -r requirements-dragster-ui.txt
PYTHONPATH=.:backend .venv/bin/python -m uvicorn main:app \
  --host 127.0.0.1 --port 8017
# In a second terminal:
BACKEND_URL=http://127.0.0.1:8017 PYTHONPATH=.:frontend \
  .venv/bin/python -m streamlit run frontend/app.py \
  --server.address 127.0.0.1 --server.port 8518 \
  --server.headless true --browser.gatherUsageStats false
```

Visit `http://127.0.0.1:8518` and select **CO2 dragster**. No provider credentials
are needed to start the backend or use this form. Other provider-based workflows
still need their configured credentials. The existing access-token middleware and
frontend access gate still apply when configured. Do not expose this local
prototype as an unauthenticated public CAD service.

The Compose frontend now builds from the repository root so it can copy the
same spec module, example, and offline preview helper as the standalone editor.
It installs no native CAD kernel: only the backend worker builds CAD. The Fly
frontend image copies the same small shared modules. No hosting was changed.

## API and native execution

`POST /dragsters/generate` takes the complete strict version-1 spec as the JSON
body (not a source script or an envelope). Unknown fields, unsupported
version/style/units, booleans/strings in numeric fields, nonfinite values, and
infeasible relationships return HTTP 422 with validation details. Malformed JSON
also returns 422. The endpoint is limited to five requests/minute per client.

```sh
curl -N -H 'Content-Type: application/json' \
  --data-binary @backend/dragsters/examples/wedge.json \
  http://127.0.0.1:8017/dragsters/generate
```

SSE uses `data: <JSON>\n\n`, with `step=native,status=running` first, comment
heartbeats while running, then exactly one terminal event when connected:

- `step=complete,status=done`: `attempt_id`, `run_id`, `revision_id`, resolved
  `spec`, full `report`, four mandatory `downloads`, and exact byte `digests`.
- `step=complete,status=error`: `attempt_id` and a sanitized useful `message`;
  no accepted run or downloadable artifacts.

Only the dedicated fixed worker receives the canonical JSON on stdin. The worker
uses the existing local transaction: deterministic builder → exported STEP →
reimported STEP checks → fresh import of that STEP → native GLB → report.
Classification, agents, providers, generated Python, and the source-execution
entrypoint do not participate. No sandbox allowlists were changed.

One application-owned `ProcessRunner` limits execution to three simultaneous
workers. Each has a 30-second native deadline, 1536 MiB address-space cap,
1 MiB combined stdout/stderr cap, owned POSIX process group/token cleanup, and a
minimal environment without provider/deployment credentials. Queued work is also
cancelled on shutdown. Disconnect/cancellation reaps native descendants before
removing this attempt's scratch directory. These are resource/lifecycle controls,
not a hostile-code OS sandbox. The API never runs native CAD on its event loop.

## Acceptance and artifacts

The parent verifies the returned spec, STEP and GLB digests before publication.
It binds the report to one server-generated revision, adds coordinate-frame
metadata, and stages **all four** exact byte payloads in a private artifact-store
directory. A single rename exposes the complete run only after storage/readback
succeeds. Any required publication failure blocks success and removes only this
attempt. Occupied run IDs are rejected without deleting earlier runs.

`GET /download/{run_id}/{fmt}` serves the existing authenticated download route:

| Format | Saved filename | Meaning |
| --- | --- | --- |
| `step` | `model.step` | Exact reimported/checked B-rep; imports as a solid, not native feature history |
| `glb` | `model.glb` | Mesh from that STEP; metres and glTF Y-up |
| `spec` | `spec.json` | Canonical complete explicit version-1 parameters in mm |
| `report` | `report.json` | Native check evidence, generator/kernel version, spec/STEP/GLB digests, revision ID, frame and preview provenance |

The completion event supplies SHA-256 digests for all four delivered files,
including the report. The report binds its revision to the spec/STEP/GLB digests;
it cannot contain its own byte digest. The client verifies every downloaded byte
set and the report/spec/revision relationship before replacing accepted state.
The GLB's mm→metres scale and OCCT→glTF axis transformation remain explicit;
the offline orbit viewer decodes the actual GLB back into mm/Z-up labels.

Artifacts use the existing TTL (default six hours), run count and size caps.
Downloads return 404 **Artifact not found or expired.** after expiry/eviction.
The main UI retains its already verified four-file snapshot for the session,
so server expiry does not silently erase current downloads. Expiry during the
initial fetch is an attempt failure with a regeneration message. Save the
accepted parameter JSON to regenerate after losing a session. This is a
short-lived delivery buffer, not a permanent document store.

## Editor, cancellation and session state

Controls are reactive drafts; generating is explicit. Strict JSON load validates
the whole input before changing controls. **Save edited parameters JSON** exports
a valid draft; **Download accepted parameters** belongs to the displayed revision.
The accepted parameters, report, revision, preview and downloads remain together
when the draft changes or an attempt fails. The UI labels draft/result mismatch.
A first failure explains that there is no accepted result yet.

Requests run in a background client thread while the UI polls progress. **Cancel
wedge attempt** invalidates the attempt token and closes its SSE connection at
the next heartbeat, preserving previous accepted state. Late responses from old
attempts cannot commit state. The native backend handles disconnect cleanup.
Closing the browser tab alone does not cancel the background HTTP thread; use
Cancel for explicit cancellation. There is no durable job/resume service; closing/reloading the browser loses the
session selection. An abrupt browser/server shutdown may leave a fully published
but undelivered run until normal expiry, and an abandoned client thread is
bounded by its HTTP timeout. Partial native results are never published.

The main form reuses the baseline standalone offline Plotly orbit viewer. This
avoids the generic viewer's external Babylon.js CDN dependency; other Mirum
viewers/workflows retain their existing behavior.

## Limits

Wedge only; no wheels, assemblies, added hardware dimensions, stock constraints,
density/mass, text extraction/refinement, routing changes, or manufacturing
claims. Example dimensions are demonstration values. Analytic wall/separation
checks apply to this exact wedge/cylinder topology and are not whole-body
manufacturing, cartridge-fit, pressure, strength, competition, or performance
certification. Regenerated STEP bytes may differ because of export metadata;
compare measured geometry and per-revision evidence.

## Session verification

See `work/final-focused-tests.log`, `work/final-regression-tests.log`,
`work/docker-build.log`, and `work/docker-gate.log` for exact local results.
Machine-readable browser evidence and the downloaded files are retained under
`work/integrated-evidence/`; the exercise is `work/exercise_integrated_ui.py`.
The final session report records counts, skips, environment warnings and gate
outcome. Generated evidence is intentionally outside the commit.

Verified on 2026-11-05 in this worktree with Python 3.12.14, CadQuery 2.6.1,
CadQuery-OCP 7.8.1.1.post1, Pydantic 2.9.2, pytest 8.3.3 and Streamlit 1.39.0
(the exact interpreter version is also recorded in the session report):

```sh
work/native/bin/python -m pytest -q \
  backend/tests/test_dragster_spec.py backend/tests/test_dragster_geometry.py \
  backend/tests/test_dragster_cli.py backend/tests/test_dragster_local.py \
  backend/tests/test_dragster_ui.py backend/tests/test_dragster_integrated.py \
  backend/tests/test_dragster_api.py frontend/test_dragster_state.py \
  frontend/test_dragster_ui.py
# 207 passed, zero skips, 33 warnings
work/native/bin/python -m pytest -q \
  backend/tests/test_process_lifecycle_ports.py backend/tests/test_process_descendant_ports.py \
  backend/tests/test_native_api_hardening_ports.py backend/tests/test_acceptance_delivery.py \
  backend/tests/test_structural_acceptance_routes.py frontend/test_acceptance_evidence_ui.py \
  frontend/test_streaming_hardening_ports.py frontend/test_manual_run_state_ports.py \
  frontend/test_access_gate.py frontend/test_refinement_review_regressions.py
# 69 passed, zero skips, 17 warnings
```

Warnings concern dependency deprecations and an existing Pydantic protected
namespace. The native environment reused installed CAD transitive dependencies;
it is not a complete lockfile installation. Browser evidence at
`work/integrated-evidence-final/` records real Chromium generation, a visible
2712-vertex/2700-triangle mesh, four verified downloads, explicit cancellation,
rejection of `pocket_center_z_mm=40.0` with identical prior file hashes, and a
new accepted revision after correcting the edit. No browser JavaScript errors.

The prescribed Docker offline gate was **blocked before execution**: the 7.9 GiB
VFS-backed environment ran out of disk while preparing the layer following the
pinned native dependency install (`ResourceExhausted: no space left on device`).
No verification image was produced and no gate tests ran. Owned build-cache
cleanup recovered space without removing existing worktrees or containers.
`work/docker-build.log` and `work/docker-gate.log` retain the concrete failure.
