# Task C dynamics and controls: machinery, validation and qualification attempt

Continues `cem-integration-verification`. Work started from the aircraft-physics
delivery `f1b193f` (PR #14, [`cem-aircraft-physics.md`](cem-aircraft-physics.md)).
While this work was in progress the branch advanced to `b2b70cb`
([panel-distribution continuation](cem-panel-distribution-continuation.md));
it was fetched, inspected and fast-forwarded underneath this change before any
evidence was regenerated. Every result below was produced on `b2b70cb` plus this
change. Published A/B/C task branches are unchanged (`6b4df7a`, `5f5fc12`,
`40fa44a`) and were not modified. No worktree was created.

**Outcome in one line:** dynamics and control software is complete and validated
against analytical limits and an independently published reference case, but
**no aircraft revision or envelope is numerically qualified**, because the
inputs listed in [Remaining dependencies](#remaining-dependencies) do not exist.
Production admission, physical construction and verified-gain gates stay
closed; no physical validation is claimed.

## Evidence classes kept separate

| Class | What it establishes here | What it does not |
| --- | --- | --- |
| Tested software | 31 new tests (positive and adversarial) on equations, conventions, provenance | Any property of a real aircraft |
| Published reference (747 cruise) | The 6-DOF/linearization/unsteady chain reproduces published matrices and roots | Glider, family or physical evidence (`analytic_fixture` class) |
| Fixture envelopes | Every qualification path can pass, fail or block as designed | Qualification: they stop at `fixture_machinery_only` |
| Numerical model (actual family) | Tailless +3° lifting loads from fresh passed/replayed native banks | Aircraft equilibrium, derivatives, modes or authority |
| Physical validation | Nothing | Measured mass/inertia, hardware, bench or flight data were not supplied |

## 1. Qualification inputs (per exact revision)

`physics/qualification.py` writes one inventory per revision: full mass/CG/inertia
tensor and evidence class, physical control topology, aerodynamic domain, ledger
state and every missing input in its required form. Default screening archives
with neutral controls (all values `calculated_screening`; body/equipment
self-inertia omitted, hardware absent):

| Family | Aircraft revision | Mass (kg) | CG x, z (m) | Ixx, Iyy, Izz (kg m²) | Jxz (kg m²) | Channels → role | Physical segments |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Tailless swept | `3b24b59d…` | 0.14246 | 0.28237, 0.00641 | 6.714e-3, 9.193e-4, 7.608e-3 | 7.82e-5 | `elevon_pitch` pitch, `elevon_roll` roll | 2 (both channels mix on each elevon) |
| Canard | `9a4fb99e…` | 0.15665 | 0.37366, 0.00996 | 6.960e-3, 2.016e-3, 8.911e-3 | −1.03e-4 | `foreplane_elevator` pitch, `aileron` roll | 4, unshared |
| Tandem | `57d38f45…` | 0.16530 | 0.30760, 0.02279 | 5.967e-3, 3.497e-3, 9.136e-3 | 5.32e-4 | `front_elevator`, `rear_elevator` pitch, `aileron` roll | 6; 2 outboard rear segments with two flaps on different hinges (mutually exclusive) |

Full revisions/tensors are in the receipt. No family declares a directional
(yaw) control. Required ledger contributions: lifting surfaces, body, one
junction per lifting member and control-hardware drag. With current inputs every
ledger is `unsupported`; no trim is attempted. A circulation root or lifting-only
pitch closure is labelled `circulation_root_lifting_only_not_trim` and never used
as the qualified trim point.

Aerodynamic domain actually available (from the current section record and the
fresh audits below): swept F1 (neutral) Re 49k–74k, native α 3–6°, and F2 (+3°
pitch region) Re 49k–64k, α 2.5–5.5°, both only for the frozen +3° installed
state, β = 0, zero rates, attached flow. Canard/tandem F3/F4 have **no** passed
bank. Under the [low-Re decision](cem-low-re-method-decision.md), their 6 m/s
sections (Re 33,000) are below the Re 50,000 floor, so those families need
resized surfaces before section evidence can apply. No body, junction, rate,
sideslip, unsteady or stall evidence exists.

## 2. Dynamics formulation (`dynamics.py`, `trim.py`, `mass.py`)

- States `x = [u, v, w, p, q, r, φ, θ]` in body axes B (x forward, y starboard,
  z down); m/s, rad/s, rad. Geometry axes G (x aft, y starboard, z up) map by the
  proper rotation `diag(−1, 1, −1)`, so L_B = −Mx_G, M_B = My_G, N_B = −Mz_G.
- `m(v̇ + ω×v) = F_aero + W`, `I ω̇ + ω×Iω = M_aero` with the complete tensor
  (products of inertia), exact 3-2-1 Euler kinematics; ψ and position ignorable.
- **Gravity consistency fix:** linearization and per-channel authority previously
  always used standard gravity even when the trim problem declared another value,
  so a trim could fail its own equilibrium check. Both now use the trim's gravity
  (`trim_gravity`), and a record without gravity is rejected.
- **Unsteady (acceleration) terms:** `UnsteadyDerivatives` (dimensional rows X…N
  versus u̇, v̇, ẇ; every row explicit) or `UnsteadyCoefficients`
  (C_Lα̇, C_mα̇, C_Yβ̇, C_lβ̇, C_nβ̇, converted exactly by the chain rule at each trim)
  enter `(E − K) ẋ = A₀x + B₀u`. They are revision- and domain-bound. Without them
  the result is labelled quasi-steady (`unsupported_missing_unsteady_derivatives`)
  and cannot meet a mode requirement. Static or rate terms never stand in for them.
- `trim.verify_equilibrium` checks a *declared* steady state (all six scaled
  residuals) instead of trusting a label; used for the published reference.
- `mass.reference_rigid_body` validates a declared tensor for reference cases; it
  carries no components and cannot satisfy a family qualification.

Validation (tests `test_cem_family_dynamics_control.py`):

- **Published reference.** Boeing 747, 40,000 ft, M 0.8 (Etkin & Reid dimensional
  derivatives, including Z_ẇ, M_ẇ and I_zx). Acceptance values are the
  independently published state matrix and eigenvalues of J. How, MIT 16.61
  (2003), Lectures AC-2/AC-3. Our nonlinear 6-DOF → central differences →
  generalized mass matrix reproduces the longitudinal A matrix within 4.4e-5
  (printed to 4 decimals) and every root within its printed precision: short
  period −0.3717 ± 0.8869i (ω 0.962, ζ 0.387), phugoid −0.0033 ± 0.0672i
  (ζ 0.049), Dutch roll −0.0331 ± 0.9470i, roll −0.5633, spiral −0.0073.
  Lateral/longitudinal coupling is < 1e-9. Omitting Z_ẇ/M_ẇ measurably moves
  the short period, and the mode assessment is then blocked.
- **Convention errors detected:** a model that reports body-axis vectors as
  geometry axes cannot balance gravity (`not_an_equilibrium`); converting forces
  but not moment pseudovectors trims yet produces a divergent lateral root; a
  sign-flipped product of inertia moves the Dutch roll; inconsistent gravity is
  not an equilibrium.
- **Analytical limits:** torque-free body with Jxz conserves |H| and rotational
  energy; a pure roll moment couples into yaw as `ṙ = Jxz L/(Ix Iz − Jxz²)`;
  zero-aero free fall gives the exact body-axis gravity components; Euler rates
  agree with the independent DCM identity `Ṙ = −[ω×]R`.
- Existing textbook 8×8 assembly, AVL lateral comparison and rejections remain.

## 3. Aerodynamic and control derivatives (`derivatives.py`)

Nondimensional body- and wind-axis derivatives about an established equilibrium
of one revision, aerodynamic contributions only (gravity excluded). Units are
explicit: α, β and controls per rad; û = ΔV/V₀; p̂ = pb/2V₀, q̂ = qc/2V₀,
r̂ = rb/2V₀; coefficients use the reference q₀ (Etkin convention). Each variable is
central-differenced at a declared decreasing step sequence (default α/β
0.5/0.25/0.125°, û 0.02/0.01/0.005, rate 0.01/0.005/0.0025, control
1/0.5/0.25°); `converged` only if the two smallest steps agree (rtol 2e-3,
atol 1e-5). The observed error ratio and Richardson estimate are reported.

Explicit failure states, never a value: `step_leaves_coverage` (the failing step
is recorded; smaller steps are not substituted), `step_exceeds_control_limit`
(channel or combined physical-surface limit), `unsupported` (for example missing
rate dependence — static derivatives do not replace it), `not_converged`
(demonstrated with output quantized like native rounding). `complete` is false
unless every static, rate and control derivative converged. Fixture checks
recover every declared coefficient (CLα, Cmα, CLq, Cmq, Clp, Cnr, Clβ, Cnβ,
control per rad) and the second-order error ratio 4.

## 4. Linearized dynamics and modal analysis (`dynamics.py`, `stability.py`)

Mode identification is by weighted eigenvector participation. It now refuses to
guess: exactly two longitudinal oscillatory pairs are short period/phugoid; one
lateral pair plus two real roots are Dutch roll/roll/spiral. Previously an
overdamped short period (one pair, two real roots) was labelled short period and
a coupled roll-spiral oscillation would have produced two "Dutch roll" entries;
both are now `*_ambiguous` and the mode is `not_identified`. Neutral roots are
flagged.

`StabilityRequirement` declares a criterion per mode with its derivation
artifact; an absent criterion is `unassessed` and blocks. `mission_limits_requirement`
reproduces the existing mission limits (Dutch roll ζ ≥ 0.05, spiral time to
double ≥ 8 s, bound to the config source hash); short-period, phugoid and roll
criteria are not declared there and stay unassessed. The unchanged family
admission rule (all five modes identified with real part < −1e-4 s⁻¹) is
evaluated separately and both must pass. Tested: stable (met), weakly damped
(`insufficient_damping`), unstable static margin (rejected by both), overdamped
short period (degenerate → not identified), slow spiral divergence (meets the
lenient mission limit, still rejected by the preserved family rule), fast spiral
(`divergence_too_fast`), missing unsteady terms, missing requirements and
unsupported linearization.

## 5. Control effectiveness and authority (`authority.py`, `interfaces.py`)

- `ControlTopology` maps channels to the physical surface segments they move,
  exactly as the installed geometry mixes them (symmetric channels alike on both
  halves, antisymmetric reversed on the left). Segment deflection is the sum of
  its channels and must respect the segment limit and, when hardware is
  declared, its asymmetric travel. `channel_range` gives the travel actually
  available with the other channels held: tailless elevons with 15° pitch trim
  leave ±5° of roll. The tandem's overlapping rear elevator (hinge 0.70) and
  aileron (hinge 0.75) are **mutually exclusive flaps**; one is locked while the
  other is deflected. Channels sharing one surface must declare identical
  hardware or the topology is rejected as conflicting.
- Roles come only from the declared topology (symmetric → pitch, antisymmetric →
  roll). No family has a directional channel. Yaw from roll/pitch channels is
  reported as cross-coupling and never counted as directional authority. A yaw
  requirement is `blocked_missing_directional_hardware`.
- `AuthorityRequirementSet` (revision-bound, with derivation artifact): manoeuvre
  and disturbance cases with required L/M/N increments, both directions,
  optional onset time; per-channel trim reserve; per-axis exemptions only by
  cited artifact. Every axis must be required or exempted, otherwise it is
  blocked. The earlier per-channel `control_authority` no longer reports
  `requirement_met` when axes are unlisted (`listed_axes_met_other_axes_unassessed`).
- Cases are solved with the evaluated effectiveness matrix and **verified by
  re-evaluating the model at the solved deflections** (no extrapolation). A
  demand beyond the physical limits is `combined_surface_saturated` with the
  largest feasible evaluated fraction. Actuator surface rate (actuator rate /
  linkage ratio) is checked against deflection/onset time; hinge torque requires
  `HingeMomentEvidence` (sampled Ch(α, δ) grid, no extrapolation) and actuator
  torque limits. Missing hardware or hinge evidence blocks.

Tested: met with hardware/rate/torque evidence; rate and torque insufficient;
missing hinge evidence; insufficient authority; combined elevon saturation on the
actual tailless topology; trim already saturated; missing yaw hardware; absent
requirement, reserve or axis; stale requirement revision; conflicting hardware.

## 6. Declared envelopes (`envelope.py`)

`EnvelopeDeclaration` fixes before evaluation: configurations (revision and mass
digest), speeds (strictly increasing, boundaries included), α/pitch bounds,
β = 0, bank 0, Reynolds bounds, altitude, load factor 1, trim control and fixed
controls, stability and authority requirements, unsteady terms and derivative /
linearization settings. Scripts write it to disk first; `run(..., declared_file=)`
rejects a run whose file differs. Every declared point is evaluated and reported
(trim → linearization → modes → derivatives → authority, plus strip Reynolds
range); none is dropped. A point is `qualified_point` only if all pass. An
envelope is `numerically_qualified` only if all points qualify **and** evidence is
admissible: aerodynamic contributions `numerical_model`/`measured`, mass including
declared or measured hardware, hardware declared. Fixture envelopes stop at
`fixture_machinery_only`. Load factors other than 1 g, stall and nonlinear
manoeuvres are outside this linear scope.

Fixture results: 9/10.5/12 m/s all points pass → `fixture_machinery_only`; the
7 m/s boundary is **rejected** (slowly divergent spiral, time to double ≈ 73 s:
meets the mission limit, fails the preserved family rule); 45 m/s is blocked by
aerodynamic coverage; an unstable configuration is rejected; missing hardware
and missing unsteady terms block.

## 7. Provenance, reconstruction and integration

Reports bind the declaration digest and file hash, configuration revision and
mass digest, mass state and body-axis tensor, topology digest and hardware class,
section-bank digest/class, atmosphere, reference geometry, gravity, required
contributions, every setting, the implementation identity (SHA-256 of all
physics sources, coupled lifting, installed geometry, coordinate polars and A's
numerics/polars/body) and runtime versions. `replay` rejects incomplete reports,
any promoted production/physical/verified/validation flag, digest mismatch, stale
implementation identity and any difference from an independent full
recomputation — including a forged `numerically_qualified` that was re-hashed.
API metadata adds `aircraft_dynamics`, `control_authority_machinery`,
`directional_control: absent_no_declared_yaw_hardware`, `qualified_dynamics: false`,
`qualified_envelopes: []` and `dynamics_blocked_by`; the UI caption states the
same. Production selectors and gates are unchanged.

## Actual aircraft results

`scripts/cem/assess_family_dynamics.py` (declarations written first, then
evaluated and independently replayed):

| Envelope | Declared scope | Points | Outcome |
| --- | --- | --- | --- |
| Tailless, canard, tandem default archives, neutral | 5/6/7/8 m/s, α −2…8°, Re 3e4–1.5e5, sea level, pitch channel trimmed, mission stability limits, no authority requirement | 4 each | All **blocked**: lifting (no bank for these states), body, junction(s), control-hardware drag unsupported; replay matched |
| Tailless +3° frozen pitch state with fresh banks | 5.5/6/6.5 m/s, α 2.5–5.5°, Re 4.9e4–7.4e4, no trim control (bank covers only this state) | 3 | **Blocked**. 6 m/s: lifting supported (`numerical_model`) but body, `junction:main_wing`, `control_hardware_drag` unsupported. 5.5 and 6.5 m/s: the zero-circulation initial strips already leave the bank's Reynolds bracket (`out_of_coverage`) |

Fresh native section audits for that state (declared before native work, run
sequentially) passed and independently replayed with the new panel-binding code:
deflected 17 queries, worst Cl 0.0142783 / Cd 0.00029536 / Cm 0.00261653;
neutral 11 queries, worst Cl 0.0139 / Cd 0.0007025 / Cm 0.0015 — identical
numbers to the previous receipts. Lifting-only force ≈ [−0.01939, 0, 1.90072] N,
My −0.0433757 N m about the screening CG: **not** an equilibrium.

**Numerically qualified revisions/envelopes: none.** Failed/rejected/unsupported
cases are listed in the receipt; historical failed regressions, the real paired
spiral rejection, low-Re interpolation failures and failed canard/tandem section
banks are unchanged.

## Remaining dependencies

Each blocks the named qualification; none is defaulted.

| Missing input (required form) | Unblocks |
| --- | --- |
| Section data for every installed and deflected foil over the trim, derivative-step and authority-limit domain, per the [low-Re decision](cem-low-re-method-decision.md): every station at Re ≥ 50,000 at the lowest speed (P1), NeuralFoil with confidence ≥ 0.8 (P2), and XFOIL spot checks at the actual converged/derivative/authority points (P3). The F3/F4 6 m/s banks are withdrawn because their sections are below the floor, so the canard/tandem geometry must be resized first. Passed swept banks remain usable | Lifting contribution away from one point; trim variation of controls; derivatives and authority at limits |
| Revision-bound body OML (or separately qualified body model) | Body force/moment; whole-aircraft drag |
| `JunctionEvidence` per lifting member | Complete ledger → trim |
| `ControlHardware` per control (hinge line, travel, actuator torque/rate, linkage, backlash, masses, exposed drag) | Hardware mass/inertia, control-hardware drag, rate and travel checks |
| `HingeMomentEvidence` per control | Actuator torque adequacy |
| Rate (p, q, r) and sideslip lifting/body/junction aerodynamics | Lateral trim residuals, damping derivatives, linearization of actual families |
| `UnsteadyCoefficients`/`UnsteadyDerivatives` from a qualified unsteady method or measurement | Short-period and Dutch-roll requirements |
| `StabilityRequirement` with short-period, phugoid and roll criteria (mission limits cover only Dutch roll and spiral) | Mode qualification |
| `AuthorityRequirementSet` from mission manoeuvre and disturbance derivation | Authority and reserve assessment |
| A declared directional (yaw) control in the family builders, or an artifact-backed exemption | Yaw authority (currently blocked for all families) |
| `MeasuredAircraftProperties` of the delivered article | Measured mass/CG/inertia; physical validation path |
| Qualified nonlinear post-peak/interaction evidence | Stall sequencing (still `unsupported_attached_flow_only`) |
| Bench and flight evidence; B's family construction | Physical validation and construction (not claimed) |

## Validation performed

- Combined regression (command in the continuation prompt): **666 passed**, no
  failures or skips (635 previous + 31 new), native XFOIL/AVL cases included,
  748.82 s, run alone after the final source change.
- Focused dynamics/physics/family suites: 128 passed. Merge-gate schema tests
  passed (9/9, 10/10, 11/11). `git diff --check` clean.
- Provider-free `cem.app` on real local sockets, started/stopped/restarted twice:
  metadata 200 (new dynamics capability fields, all unqualified), computed
  feasibility 200 (`infeasible`), HTTP 422 for each of the three
  unqualified-family-only searches; clean stop both cycles. No socket restriction.
- No security-review surface (`main.py` endpoints, `compiler.py`, `schemas.py`,
  `cad_utils.py`, Gemini keys) was changed.

## Reproduce

```sh
source /home/user/mirum-env.sh   # session-local; see the continuation prompt
python -m pytest backend/tests/test_cem_family_dynamics_control.py \
  backend/tests/test_cem_family_physics.py backend/tests/test_cem_family_physics_integration.py -q
python scripts/cem/assess_family_physics.py --directory <new dir A>        # native, sequential
python scripts/cem/assess_family_dynamics.py --directory <new dir B> --section-audits <dir A>
```

Validation results and raw-evidence identity: see
[`../testing/cem-aircraft-dynamics-control-results.json`](../testing/cem-aircraft-dynamics-control-results.json)
and [the evidence archive](../testing/artifacts/cem-dynamics-control/README.md).
