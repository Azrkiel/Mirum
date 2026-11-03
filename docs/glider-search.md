# Passive glider distance search

Choose **Passive glider distance search** in the Streamlit workflow selector. This opt-in path makes no LLM calls. Existing text-to-CAD routes are unchanged.

## Scope and inputs

Small passive fixed-surface gliders only. Maximize **trimmed L/D**, subject to payload fit, balance, trim, static/lateral stability, stall margin, speed, mass and assembled size limits. Predicted still-air distance is `launch_height_m * L_over_D`, assuming steady trimmed glide at standard sea-level conditions. Higher launch height scales distance; it does not change the L/D ranking.

The form sets payload mass/dimensions, maximum span/overall length/mass/speed, minimum and target static margin, maximum trim CL/CLmax, allowed part families, areal-density mass model, search budget, seed and number of retained candidates. Empty choices, unknown fields, nonfinite inputs and invalid ranges are rejected. The UI provides sliders and airfoil choices; the API accepts range overrides. All use SI units; equal interval endpoints lock a parameter. Ranges may narrow the supported model space below, not widen it. Maximum dimensions further restrict the search. Payload mass zero disables payload fit checks.

The initial half samples independent candidate parts. The remaining half resamples around the leading feasible candidates (or the least-rejected candidates when none pass), with continuing broad exploration. Ranking uses trimmed L/D; a seeded search is repeatable in the pinned runtime. This is a finite search, with no guarantee of a global optimum or a monotonic improvement at every evaluation.

## Run and install

From the repository root, in the existing Python 3.10 environment:

```bash
uv pip install --python .venv/bin/python -r requirements-linux.lock -r requirements-mirum-verification.lock -r requirements-glider.lock
```

The verification and Fly Dockerfiles install the additive glider lock; the Compose backend installs the solver through backend/requirements.txt. Start the existing FastAPI and Streamlit services, with `BACKEND_URL` pointing to the backend. The parent app retains its existing Gemini import requirement; this workflow never calls Gemini. The solver process receives no provider credentials.

## API

- `POST /gliders/search` with a GliderRequest JSON body returns HTTP 202 and `job_id`.
- `GET /gliders/search/{job_id}` returns running progress, a failure, or the completed result.
- `GET /gliders/search/{job_id}/geometry/{candidate_id}` downloads a ZIP for a retained feasible candidate.

Example request:

```json
{"launch_height_m": 10, "payload_mass_kg": 0.06, "max_wingspan_m": 1.2,
 "evaluations": 40, "top_k": 5, "seed": 42,
 "ranges": {"wing": {"style": ["tapered", "polyhedral"], "washout_deg": [1.5, 1.5]},
            "tail": {"kind": ["conventional"]}}}
```

One search runs per API process; another submission receives 429. The existing access-token middleware covers these endpoints. Deployment must use a single worker for the in-memory job registry. Searches are bounded to 8–120 evaluations, 15 minutes and 3 GiB address space. Job metadata survives navigation/reruns, not backend restart. Up to eight jobs are retained; jobs older than one hour are removed on subsequent submissions. Downloads expire with jobs. No deployment or environment configuration is published by this implementation.

Results include leading candidates, complete request, seed, solver version, explored/refined phases, rejection counts and individual evaluations. No feasible candidates is a legitimate completed outcome, not proof that the requirements are impossible. A worker/export failure produces a failed job rather than partial accepted results.

Each ZIP contains assembly and per-part STL meshes in millimetres, 1:1 SVG cutting templates, a three-view PNG, evaluated parameters/balance/trim, and assumptions. Export uses the exact evaluated assembly with its final wing/payload positions, ballast and stabilizer incidence. These are aerodynamic surface meshes, not editable STEP solids or certified manufacturing geometry.

## Evidence and limitations

The model was ported from Azrkiel/Mirum branch glider-designer, commit 9384036784d2daed70ef66455b6827701a1338a7, under the repository MIT license. Relative imports isolate it from the existing `analysis` package. The integrated version respects locked T-tail taper and wing mounting ranges during balancing, checks final speed/trim convergence, refreshes CLmax at the final trim condition, and rejects missing/nonfinite constraint evidence.

AeroSandbox AeroBuildup/NeuralFoil supplies screening performance; VLM plus fuselage increments supply stability derivatives. These are model predictions, not flight validation. CLmax is estimated over the model's -2 to 14 degree angle grid. Static and lateral checks do not establish all longitudinal dynamic modes. Mass and inertia are estimates from designer-supplied areal densities. Structural strength, stiffness, flutter, launch/gust loads, ground effect, wind, wing flex and landing transients are not evaluated. Small score differences can be inside model error. Validate shortlisted candidates independently and with physical tests.

## Supported range overrides

Numeric pairs are inclusive intervals; lists are permitted discrete choices. A maximum span/length in the form may reduce these upper limits.

```json
{
  "wing": {
    "style": [
      "tapered",
      "elliptical",
      "polyhedral"
    ],
    "span_m": [
      0.6,
      1.4
    ],
    "root_chord_m": [
      0.1,
      0.22
    ],
    "taper": [
      0.35,
      1.0
    ],
    "sweep_qc_deg": [
      0.0,
      8.0
    ],
    "dihedral_deg": [
      3.0,
      12.0
    ],
    "washout_deg": [
      0.0,
      3.0
    ],
    "incidence_deg": [
      1.0,
      3.0
    ],
    "naca_camber_pct": [
      0,
      1,
      2,
      3,
      4
    ],
    "naca_camber_pos": [
      3,
      4
    ],
    "naca_thickness_pct": [
      6,
      7,
      8,
      9,
      10,
      12
    ]
  },
  "fuselage": {
    "style": [
      "pod_boom",
      "torpedo",
      "slab"
    ],
    "length_m": [
      0.45,
      0.95
    ],
    "max_diameter_m": [
      0.055,
      0.085
    ],
    "nose_frac": [
      0.06,
      0.12
    ],
    "pod_frac": [
      0.28,
      0.45
    ],
    "boom_radius_m": [
      0.005,
      0.009
    ],
    "wing_station_in_pod": [
      0.3,
      0.7
    ]
  },
  "tail": {
    "kind": [
      "conventional",
      "conventional",
      "T",
      "V"
    ],
    "h_span_m": [
      0.15,
      0.4
    ],
    "h_root_chord_m": [
      0.04,
      0.1
    ],
    "h_taper": [
      0.6,
      1.0
    ],
    "v_height_m": [
      0.05,
      0.15
    ],
    "v_root_chord_m": [
      0.05,
      0.11
    ],
    "v_taper": [
      0.5,
      0.9
    ],
    "v_sweep_le_deg": [
      10.0,
      40.0
    ],
    "vt_panel_span_m": [
      0.1,
      0.25
    ],
    "vt_root_chord_m": [
      0.05,
      0.1
    ],
    "vt_taper": [
      0.6,
      1.0
    ],
    "vt_dihedral_deg": [
      30.0,
      50.0
    ],
    "airfoil": [
      "0006",
      "0008"
    ]
  }
}
```

## Interactive single-design explorer

Choose **Explore one design** in the glider workflow, or **Explore this candidate**
under a completed search result. The search form and its job/results remain in the
session when switching modes. The preset is deterministic; candidate loading
preserves its request, evaluated wing/payload placement, ballast and tail incidence.

The browser renders while a slider is held down, without a Streamlit round trip.
It requests the canonical Python preview after 200 ms without an edit and on
release. The camera persists across edits; top/front/side and reset-camera controls
are available. The front-view angle guide distinguishes effective tip-height
angle from the steeper outer panels of a polyhedral wing. Washout is tip twist;
incidence tilts the whole wing. All controls use metres/degrees and stay within the
loaded request's permitted ranges. Search ranges are not modified by explorer edits.

An edit clears performance and nominalizes placement. Recalculate explicitly, or
enable recalculation after a 900 ms pause. The existing solver may slide the wing,
move payload, add ballast and retrim within allowed bounds. Its effective mesh
replaces the nominal preview **together with** its metrics, adjustment evidence and
geometry digest. Infeasible shapes remain visible with rejection reasons.
Downloads contain STL meshes in millimetres, SVG templates and `design.json` for
the exact evaluated assembly. Compare overlays the original in translucent color;
reset restores its inputs and requires recalculation.

New endpoints (same application access-token middleware as search):

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/gliders/metadata` | Supported controls, bounded NACA profiles, preset |
| POST | `/gliders/preview` | Typed nominal input → named coarse meshes, revision and geometry digests |
| POST | `/gliders/evaluations` | HTTP 202: one bounded evaluation, no optimization sweep |
| GET | `/gliders/evaluations/{id}` | State, revision-bound metrics, effective placement and geometry |
| GET | `/gliders/evaluations/{id}/export` | Completed evaluation's ZIP |
| GET | `/gliders/search/{job}/design/{candidate}` | Candidate-specific explorer bootstrap |

`DesignSpec` accepts typed `parameters`, `request`, `placement` and the fixed
`rebalance_and_retrim` policy. It rejects unknown fields, unsupported airfoils,
nonfinite numbers, out-of-range values, contradictory tail dimensions and
out-of-bay placement. Canonical physical-input digests exclude search seed/budget
settings. Geometry has a separate digest; evidence identifies solver and explorer
implementation versions. Preview, evaluation and export never share a mutable
assembly across requests. Preview imports no evaluator or Matplotlib exporter.

Search and evaluation share one execution slot per API process. While busy they
return 429; editing and preview remain available. There is no pending solver queue:
auto mode does not enqueue every drag. Jobs retain the existing 900-second/3-GB
child-process limits, credential exclusion, bounded retention and shutdown cleanup.
Continue deploying **one API process**: metadata is in memory, expires, and is lost
on restart. A shared job store and multi-worker scheduling remain future work.
The iframe has no API token; Streamlit's Python bridge forwards authenticated
requests, coalesces browser edits, and ignores replies for older edit sequences.

### Build and validate

From the repository root:

```bash
python -m pip install -r requirements-linux.lock -r requirements-glider.lock
python -m pip install -r frontend/requirements.txt -r requirements-dev.txt
npm --prefix frontend/components/glider_explorer ci
npm --prefix frontend/components/glider_explorer run build
OPENBLAS_NUM_THREADS=1 python -m pytest backend/tests/test_glider_explorer.py \
  backend/tests/test_glider_model.py backend/tests/test_glider_search.py \
  frontend/test_glider_ui.py -q
```

Both frontend Dockerfiles build and copy the bundled assets. Compose mounts only
frontend Python files, so a source bind mount does not hide the built component.
For local development, build before running Streamlit. No viewer CDN is needed.
Generated `dist/` and `node_modules/` remain ignored. Existing isolated checkouts
are sufficient; do not create another worktree for setup.

Optional real-browser smoke check (running API/UI, Python Playwright installed and
Chromium at `/usr/bin/chromium`):

```bash
python frontend/components/glider_explorer/tests/browser_smoke.py
```

This exercises a real held-mouse drag, canonical settlement, front view,
recalculation, evaluated ZIP, stale-result rejection, comparison and reset. Set
`MIRUM_UI_URL` to select a different running UI. The backend must be idle when this
check begins. Geometry fixtures execute Node against the browser formulas for all
27 wing/body/tail family combinations and compare vertices and triangles against
Python meshes, including nonzero sweep, washout and tail incidence.

Recorded local validation: 20 sequential warmed HTTP previews of the default
small-glider preset, Python 3.12/AeroSandbox 4.2.10 on the onboarding host: median
25.3 ms, maximum 70.4 ms (not an end-to-end browser or universal latency guarantee).
The same host evaluated and exported the default design in approximately 4.6 s;
it was feasible and predicted 115.4 m from a 10 m launch height. These are screening
predictions, not flight measurements. HTTP integration used the actual glider route
attachment in a minimal FastAPI host; it did not exercise the unrelated CAD/LLM
pipeline or provider credentials. Both frontend and backend container images were subsequently built and the
glider workflow was verified in a local container deployment. The local glider-only
API host avoids the unrelated CAD orchestrator's mandatory Gemini key. No Fly.io
rollout was performed. Dockerfiles normalize application read permissions so their
non-root users can run checkouts restored with restrictive file permissions.

### Specified flight conditions

The explorer's **Flight conditions → Analysis mode** offers:

- **Automatic trim (sea level)**: the existing balance/trim search, with selected
  speed/angle and steady-glide performance. Manual operating-point fields are inactive.
- **Specified conditions**: true airspeed (1–15 m/s), body-reference angle of attack
  (−10–15°), sideslip (−10–10°), and ISA altitude above sea level (0–5000 m).
  Altitude controls atmospheric density and viscosity; it is independent of the
  launch height used for distance estimates.

Specified mode runs AeroSandbox AeroBuildup/NeuralFoil at those exact conditions,
referenced to the current mass-model CG. It preserves wing/payload placement,
ballast and tail incidence; it does **not** rebalance or retrim. It returns lift,
drag, side force, body-axis roll/pitch/yaw moments, coefficients, mass, dynamic
pressure, Reynolds number, density, viscosity and lift/weight ratio. Positive
lift is upward; moments use AeroSandbox body axes (x forward, y right, z down),
not the viewer's geometry axes. Angle of attack is relative to the body reference;
wing incidence and washout still contribute to local surface angles.

These are operating-point estimates, not CFD or proof of equilibrium, stability
or stall clearance. No trimmed distance, static margin or stall margin is claimed
in this mode. Geometry screening reasons remain visible as warnings but do not
prevent this force estimate when the geometry can be analyzed; numerical failures
still fail the evaluation. A speed exceeding the designer's maximum is reported
as a warning. No wind, gust, transition or atmospheric-temperature controls are
implemented; speed is air-relative, not ground speed.

The optional `flight_conditions` object in `DesignSpec` defaults to automatic
trim for existing clients and loaded candidates:

```json
{"flight_conditions": {"mode": "specified", "airspeed_m_s": 7,
                       "alpha_deg": 6, "beta_deg": 3, "altitude_m": 1500}}
```

Conditions participate in the input-revision digest and are saved in evaluated
ZIPs alongside the effective operating point, applied balancing policy and solver
evidence. Editing conditions invalidates displayed results and stale replies;
it does not change geometry, placement, search bounds or the original candidate.
Reset restores the original conditions as well as geometry.

Validation: `backend/tests/test_glider_flight_conditions.py` checks bounds,
condition-dependent forces, atmosphere effects, revision binding, preservation
of placement and exported evidence. The optional browser check is:

```bash
python frontend/components/glider_explorer/tests/flight_conditions_smoke.py
```
