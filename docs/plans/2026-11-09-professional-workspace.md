# Professional workspace implementation

Goal: replace the scattered Mirum UI with the approved dark electric-blue workspace.

Architecture: retain Streamlit and existing workflow functions and API seams.
A small shared workspace module owns styling, sidebar navigation, headings and
empty states. Page modules own their inputs and results. Embedded browser tools
share the palette without introducing a frontend framework.

Tech stack: Python, Streamlit 1.39, existing Babylon/Three.js viewers, HTML/CSS/SVG.

```text
sidebar -> workflow -> grouped inputs -> existing API
                              |              |
                           invalid       accepted -> model / analysis / files
                              |              |
                           feedback      failed -> preserve accepted revision
```

## Files

- `frontend/workspace.py`, `frontend/workspace.css`: shared tokens, shell,
  navigation and SVG empty state. Navigation uses stable workflow IDs in session state.
- `frontend/app.py`: two-column Text to CAD workbench, result tabs, adjacent
  parameter/refinement controls, concise copy and matching viewer.
- `frontend/dragster_ui.py`: grouped body/axle/pocket inputs beside the accepted
  preview, compact downloads and readable parameter labels.
- `frontend/glider_ui.py`, `frontend/cem_ui.py`, `frontend/glider_explorer.py`:
  grouped search/engineering forms, concise headings and clear result hierarchy.
- `frontend/components/glider_explorer/src/style.css`, `src/main.js`:
  matching embedded viewer colors and responsive controls.
- `frontend/regen/index.html`, `styles.css`, `app.js`: restrained matching studio.
- frontend tests and `frontend/.streamlit/config.toml`: preserve behavior and load
  the theme from the existing Dockerfiles' complete frontend copy.

## Tasks

- [x] Run the existing frontend regressions as baseline. Add a behavior test for
  persistent workflow selection and independent Text to CAD result navigation;
  run it before implementation and observe missing workspace behavior.
- [x] Implement `render_navigation()` and `apply_workspace()` in the shared module,
  using a sidebar radio with stable IDs and loading CSS from a resolved local path.
  Verify selection persists across reruns with Streamlit AppTest.
- [x] Restructure Text to CAD and Dragster around inputs and results. Preserve
  the existing streaming, evidence binding, and download functions. Run current
  frontend regressions and fix any introduced failures.
- [x] Group Glider Search and Glider Engineering inputs and results without
  changing request dictionaries, widget keys or polling fragment ownership.
- [x] Restyle both embedded tools. Keep existing DOM IDs, events, and API paths.
  Build the glider component with `npm ci` and `npm run build`.
- [x] Run frontend tests, Python compilation and the component build. Start
  Streamlit locally and exercise controls through AppTest. Review the diff for
  regressions, unsafe HTML interpolation, and duplicated styling.
- [ ] Desktop/mobile screenshot inspection: Computer Use's browser host was
  unavailable; its native fallback stopped because the current browser URL could
  not be verified. No visual browser verification is claimed.

## Verification record

- Workspace navigation tests failed before the shared shell existed; passed after
  implementation. Main workspace test failed on missing result tabs before the
  layout change, then passed with blank-generation protection and input updates.
- Frontend suite covers glider/CEM request payloads, native dragster generation,
  preview recovery, cancellation, atomic JSON import, accepted artifact digests,
  manual-run/refinement evidence, and retained models after interrupted generation.
  Final result: 67 passed; 18 existing third-party deprecation warnings.
- Existing optional browser smoke selectors now use the new Glider Search label.
- Embedded explorer production build, JS syntax, CSS parsing, Python compilation,
  and diff whitespace checks passed. Existing third-party deprecation and bundle
  size warnings remain.
- Runtime: local Streamlit preview on `http://127.0.0.1:8510`; health endpoint
  returns HTTP 200. Preview expects the API on `http://127.0.0.1:8000`.
- Security pass: backend request/authentication seams and artifact validation
  remain intact; shared HTML escapes parameterized labels; SVG/CSS are local
  static assets; no credentials or new remote endpoints introduced.
- Cleanup: one shared shell owns Streamlit styling/navigation; page modules retain
  workflow behavior. Removed idle parameter filler and duplicate page titles.

The user subsequently requested a commit and push. Publish the reviewable
`feat/professional-workspace` branch; merging and deployment remain separate.
