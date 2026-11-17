# Regenerative cooling nozzle integration

Goal: expose the existing deterministic PicoGK chamber/nozzle builder in Mirum's main workflow and API.

Architecture: share the existing strict specification, serial disposable job runner,
acceptance gate, and offline WebGL viewer. The main API owns and closes its runner;
the standalone browser app retains its existing routes. Streamlit sends the user's
access header server-side and only previews/downloads accepted, hash-bound artifacts.

Tech stack: FastAPI, Pydantic, Streamlit, existing PicoGK CLI, vanilla WebGL.

```text
Workflow selector -> parameter form -> POST /engineering/regen/jobs
                                         |
                  invalid / missing runtime / busy -> clear error, no files
                                         |
                         queued -> bounded builder + verifier
                                         |
            GET job <- accepted / rejected / unavailable
                         |
               accepted artifacts -> checksum -> preview + downloads
```

- [x] Add failing API tests: register through `attach_engineering_routes`, fetch
  presets/schema, submit/poll, check prefixed artifact links, reject invalid or
  unavailable requests, and close the runner on application shutdown.
- [x] Extract `attach_regen_routes` and `regen_lifespan` from the standalone web
  module; wire them into `engineering/api.py` and `main.py`. Package presets with
  the backend so configuration works in backend-only container layouts.
- [x] Add failing Streamlit AppTest cases for workflow selection, edited parameter
  submission, polling, unavailable runtime, authenticated downloads, checksum
  rejection, and rejection without artifacts.
- [x] Add `frontend/regen_ui.py`, select it from `frontend/app.py`, and extract the
  existing WebGL function into `frontend/regen/viewer.js` for both UIs. Keep
  parameter/report state separate from Text to CAD and glider workflows.
- [x] Update Compose's frontend bind mounts, browser documentation, README and CI.
- [x] Run focused verifier, API and Streamlit tests under a memory/process deadline;
  inspect the running UI and report native-runtime limitations explicitly.

The user requested a local commit after integration; no push, merge or deployment
is requested. The geometry builder,
verifier algorithms, thresholds and certification fields remain unchanged.

## Validation

- Final results: **216 passed, 46 deselected** in the focused run (35.63 s;
  0.21 GiB process-tree peak). The actual main API's **six access-token tests
  passed** separately (24.87 s; 0.25 GiB peak). Each run used a 1.5 GiB cap.
- Final self-review checked route authentication, same-origin submission checks,
  rate/body/output limits, fixed artifact paths, worker credential isolation,
  artifact checksums and stale-result state. The extracted WebGL viewer matches
  the original function exactly except for its local reset-button lookup.
- Focused host regression command: `python -m pytest backend/tests/test_regen_spec.py backend/tests/test_regen_cli.py backend/tests/test_regen_mesh.py backend/tests/test_regen_budget.py backend/tests/test_regen_integration.py backend/tests/test_regen_web.py frontend/test_regen_ui.py frontend/test_access_gate.py frontend/test_streaming_hardening_ports.py frontend/test_refinement_review_regressions.py -q -p no:cacheprovider --tb=short -k "not adv and not timeout and not busy and not output_limit"`.
- The host run uses the Windows process-tree cap: 1.5 GiB and a 120-second deadline.
  The 43 adversarial fixtures and three POSIX worker-kill/timeout/output-limit tests
  are outside this run. The existing Linux CI command retains those process tests.
- A second environment imports the real main API and runs
  `backend/tests/test_access_token_gate.py`, including every new route.
- The real Uvicorn main API and Streamlit server started on loopback for a bounded
  smoke check. Configuration and Streamlit returned HTTP 200, malformed input 422,
  and a valid request without the runtime 503. Both servers were stopped afterward.
- JavaScript syntax checks and `git diff --check` pass.
- Browser inspection was attempted, but the browser host and native automation
  pipe were unavailable. No rendered screenshot or browser WebGL claim is made.
- Docker was stopped and the Linux PicoGK runtime was unavailable on this host.
  No fresh native mesh generation, Docker execution or broad qualification sweep
  was performed. Builder/verifier algorithms and geometry acceptance thresholds
  are unchanged.
