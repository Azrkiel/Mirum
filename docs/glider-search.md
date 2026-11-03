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
