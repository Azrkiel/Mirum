# Task C aircraft-physics machinery and independent validation

Continues `50279906d857bfbb6a362ab78e984cd5e9f581a6` on
`cem-integration-verification` (delivered on `claude/busy-mendel-t04s6p`, based
exactly on that commit). Published inputs were re-fetched first: the
integration branch had no newer commit; A remains at `6b4df7a` (its only
unmerged commit is handoff documentation already reflected here); B remains at
`5f5fc12`, an ancestor; C remains at `40fa44a`, an ancestor. No other task's
branch was modified and no unpublished change was assumed.

This continuation adds **numerical machinery and its analytical validation**.
It does **not** qualify any aircraft, family, stall sequence, control authority
or physical construction. All production admission/search/comparison/archive/
worker/API/UI gates, `verified_gain=false` and every historical failed
regression and qualification limit are unchanged.

Evidence classes are kept distinct throughout:

| Class | Meaning | Grants qualification? |
| --- | --- | --- |
| `analytic_fixture` | Declared constant-coefficient aircraft used to check equations | Never |
| `development_fixture` | C's analytical section bank / fixture junction tables | Never |
| `numerical_model` | Native passed/replayed bank, or A's slender body inside its scope | Not by itself |
| `calculated_screening` | Mass/CG/inertia computed from C's screening inventory | Never measured |
| `measured` / `measured_delivered` | Required form exists; **no measurements were supplied** | — |

## 1. Gap matrix against Task C aircraft-physics requirements

State at the start of this continuation, then what this work changed.

| Requirement | Before | Defect / gap | Now | Remaining dependency |
| --- | --- | --- | --- | --- |
| Explicit axes, units, reference points | G axes documented in `mirum-aero-1`; ad hoc wind vectors in `performance.py` | No body/wind/earth transforms; roll/yaw pseudovector sign vs. standard body axes undocumented | `physics/frames.py`: G/B/W/E frames, exact DCMs, wind components, gravity, moment transfer, flight-path kinematics | — |
| Force/moment contributions with application points | Coupled lifting strips sum to totals; A's body increment separate | No common ledger; missing body/junction silently absent from lifting-only reports | `physics/wrench.py`: revision-bound `Contribution`, required-set `resultant`, explicit `unsupported` (never zero) | Body, junction, hardware-drag evidence |
| Gravity and all six components | L=W and pitch only (diagnostics, coupled root) | Lateral residuals and gravity attitude not handled | Gravity at frozen CG from 3-2-1 attitude; all six residuals checked | — |
| Component mass/CG/full inertia | Point inventory; diagonal-only `Reference.inertia_kg_m2`; B's full tensor limited to its straight-wing prototype | No products of inertia for C families; no strip self-inertia; no measured-property form | `physics/mass.py`: primitives, rotation, parallel-axis, products, PD/triangle checks, body-axis tensor, family binding (laminae), `MeasuredAircraftProperties` form | Hardware masses, body/equipment distributions, measurements |
| Bounded full equilibrium | 1-D pitch bracket + speed scaling (VLM); `performance.equilibrium` for A references only | Not six-axis; no saturation/untrimmable/nonfinite classes for C | `physics/trim.py`: justified variables only, bounded covered Newton, grid sign evidence, reserve/saturation, unsolved-residual closure | Complete supported aerodynamic ledger for a real aircraft |
| Stability, dynamics, control limits | AVL lateral modes with Ixz neglected; fixture `Evidence` mode dict; static Cmα only for families | No full-inertia rigid-body model; no derivative step check | `physics/dynamics.py`: nonlinear 6-DOF with full tensor, two-step central differences, mode classification, control limits, authority at limits | Rate/sideslip aerodynamics for real families; authority requirements |
| Body/junction interfaces | A slender body exists for `mirum-aero-1` geometry; C families have no body OML | No junction model; no scope wrapper | `physics/interfaces.py`: A body reused only in documented scope; `JunctionEvidence` required form | Revision-bound body OML; junction evidence |
| Control hardware/authority interfaces | Controls declared (hinge fraction, span, limit) | No hinge geometry, actuator, hardware mass or requirement contract | `ControlHardware` (validated on installed hinge line), `AuthorityRequirement`, `REQUIRED_INPUTS` | Hardware declarations, hinge moments, requirement derivation |
| Integration with actual families | Coupled lifting solve; circulation root only | Lifting-only pitch was the only aircraft-level output | `physics/assessment.py`: actual archive → mass binding → ledger → trim only when complete | All non-lifting contributions |
| Stall sequencing | Fixture scalar stall checks | Attached-flow banks reject post-peak queries | Explicitly `unsupported_attached_flow_only` | Qualified nonlinear envelope |

## 2. Force/moment accounting (`frames.py`, `wrench.py`)

- G: x aft, y starboard, z up. B: x forward, y starboard, z down. They differ
  by a proper 180° rotation about y, so vectors *and* moments map with
  `diag(-1, 1, -1)`: L_B = −Mx_G, M_B = My_G, N_B = −Mz_G.
- Wind axes W follow the standard definition; drag D = −F·x_W, side force
  Y = F·y_W, lift L = −F·z_W. At β = 0 this reproduces the shared
  `(cos α, 0, sin α)` / `(−sin α, 0, cos α)` convention of `cem.aero.performance`.
- Moment transfer `M_P = M_O + (O − P) × F` is exact and path independent;
  a pure couple is invariant.
- A `Contribution` is supported (force, moment, reference point, optional
  verified application point, evidence class) or unsupported (reason only; any
  substituted value is rejected). `resultant` requires the declared set,
  rejects undeclared entries and foreign revisions, and returns `None` totals
  when anything is missing or unsupported. `partial_supported` is labelled
  `equilibrium_evidence=false`.

## 3. Mass properties (`mass.py`)

Tensor convention `I = Σ m (r·r E − r rᵀ)`; products `Jxz = ∫xz dm = −I[0,2]`
are reported explicitly. Parallel-axis transfer, rotation `R I Rᵀ`,
symmetry, positive definiteness and the principal triangle inequality are
enforced. In body axes Jxz keeps its sign; Jxy and Jyz reverse.

`family_screening_components` binds C's archived inventory exactly: every
lifting strip is regenerated from the installed surface, must match the
archive bit-for-bit, and becomes a thin lamina in its panel plane. Equipment
and body remain point masses with `point_mass_unknown_omitted` self-inertia;
no distribution is invented. The diagonal of the point inventory reproduces
the existing contract adapter exactly; strip self-inertia adds a PSD term.
Mirror symmetry gives Jxy = Jyz = 0; dihedral produces nonzero Jxz.

The resulting state is `calculated_screening`, `measured_aircraft=false`.
`MeasuredAircraftProperties` defines the required measured form (article and
artifact digests, calibrated scale, CG method, pendulum inertia method,
uncertainties). `compare_measured` keeps the measurement authoritative.

## 4. Bounded equilibrium (`trim.py`)

Steady, non-rotating flight; all six residuals about the frozen CG. Variables
are restricted to airspeed, α, β, pitch, bank and declared controls with
bounds inside physical limits; mass, CG, geometry and hardware cannot be
variables. Every control and flight state must be explicitly fixed or solved.
The existing covered damped Newton (`cem.aero.numerics.bounded_newton`) is
reused; out-of-bound or uncovered trials supply no residual.

Outcomes: `trimmed`, `control_saturated` (reserve violated, or the moment
residual is sign-definite over the full grid within control limits),
`untrimmable_on_declared_grid`, `unbalanced_unsolved_residuals`,
`no_equilibrium_established`, `unsupported`, `out_of_coverage`,
`nonfinite_or_invalid`. Grid sign evidence is labelled as such, not a proof.

## 5. Dynamics and control (`dynamics.py`)

Nonlinear 6-DOF equations in B with the complete tensor (`I ω̇ + ω × Iω = M`)
and exact Euler kinematics, linearized about an established trim by central
differences at two step sizes; disagreement is reported as
`derivative_not_converged`. Control perturbations never exceed physical
limits (`control_at_limit`). Modes are classified by weighted eigenvector
participation. `control_authority` evaluates moment increments **at** the
limits (no extrapolation) and stays `unassessed_missing_requirement` without
an `AuthorityRequirement`; both directions are required per axis.

## 6. Body/junction and control-hardware interfaces (`interfaces.py`)

A's slender body is reused only for slender circular sections, fineness
6–30, body Re 1e5–3e6, |α| ≤ 8°, β = 0 and zero rates; otherwise the body
contribution is unsupported. It is not extended to junction flow, shielding
or separation. `JunctionEvidence` must be bound to the aircraft revision and
is interpolated only between sampled α rows inside its speed/density domain.
`ControlHardware` must lie on the installed hinge line within 1 mm at the
control's span limits and may not exceed the declared deflection limit.
`REQUIRED_INPUTS` records each missing input's exact form and what it unblocks.

## 7. Integration (`assessment.py`)

Required contributions for an actual family: `lifting_surfaces`, `body`, one
`junction:<surface>` per lifting member, and `control_hardware_drag` unless
all hardware is declared flush/internal. A bank must be declared
`development_fixture` or `passed_and_replayed` (with matching report digests,
audit digests and replay). Coupled lifting is supported only at β = 0 and zero
rates and only for the frozen installed control state; an inner circulation
failure is a coverage limit, not a load. Trim is attempted only when the
complete ledger is supported.

## Validation summary

See [`../testing/cem-aircraft-physics-results.json`](../testing/cem-aircraft-physics-results.json).
`backend/tests/test_cem_family_physics.py` (23 tests) and
`backend/tests/test_cem_family_physics_integration.py` (11 tests) check:

- Proper rotations; wind decomposition round trips; gravity signs; exact,
  path-independent moment transfer; couple invariance.
- Ledger conservation about two points; unsupported/missing/undeclared/foreign
  contributions; nonfinite rejection.
- Analytic box/cylinder/lamina tensors; product of inertia of a rotated
  principal body; parallel-axis tensor with products; agreement with B's
  `mounted_wing.mass_properties`; tensor rejections.
- Family mass binding against C's inventory and the existing diagonal; tamper
  rejection; measured-form comparison.
- Closed-form glide trim (α = −Cm0/Cmα, γ = atan(CD/CL), V from L = W cos γ)
  to ~1e-8; reference-point invariance; fixed-speed control trim; reserve and
  beyond-limit saturation; untrimmable; unbalanced roll; nonfinite; missing
  contribution; out-of-coverage; unjustified variables.
- Full 8×8 state matrix against an independent textbook assembly (including
  drag rotation in Yv, tanθ coupling and Jxz) to 1e-5; primed roll-damping
  identity with Jxz; elevator control column; legacy `lateral_modes`
  reproduced exactly once its two omitted kinematic terms are removed.
- Unstable reference, non-trim, missing rate dependence, control-at-limit,
  missing limits, mass mismatch; authority met/insufficient/unassessed.
- Body scope wrapper equals A's increments in scope and is unsupported
  outside it; junction domain rules; hinge-line validation for all families;
  all three actual families unsupported without body/junction/hardware.

A complete *fixture* canard ledger (development section bank, A's body in
scope, zero fixture junction tables, flush declared hardware) reaches trim and
returns `no_equilibrium_established` (15 of 27 grid states uncovered); it is
not labelled untrimmable because the grid is not fully covered.

These fixtures validate implementation only. **They do not establish
actual-family stability, trim or authority.**

Combined regression: **606 passed**, no failures or skips (572 previous + 34
new), native XFOIL/AVL cases included, run twice (725.55 s, then 704.94 s on
the final source). API tests used real local sockets; no restriction occurred.
Provider-free `cem.app` was started, stopped and restarted: metadata 200,
computed feasibility 200 (`infeasible`), and HTTP 422 for each of the three
unqualified-family-only searches in both cycles. Repository schema gate tests
passed. No source under the security-review surfaces was changed.

## Actual aircraft results

`scripts/cem/assess_family_physics.py` declared, then ran fresh, the two
previously passed tailless-swept grids for the archived +3° pitch-channel
state with the rebuilt executables: deflected foil Re 49k/56k/64k, α 2.5–5.5°
by 0.375° (17 queries) and neutral foil Re 49k/74k, α 3/4.5/6° (11 queries).
Both **passed and independently replayed**; worst midpoint errors (deflected
Cl 0.01428, Cd 0.000295, Cm 0.00262) and the coupled lifting-only result at
6 m/s, 4° — force ≈ [−0.01939, 0, 1.90072] N, My −0.0433757 N m about the
screening CG — are identical to the previous receipt despite the different
compiler. The lifting contribution is `numerical_model`; the ledger is
`unsupported` (body, `junction:main_wing`, `control_hardware_drag`), so no trim
was attempted. Screening mass 0.14246 kg, CG (0.28237, 0, 0.00641) m,
Jxz 7.82e-5 kg m², body/equipment self-inertia omitted.

No actual aircraft case is qualified. With every currently available input, each actual family ledger is
`unsupported` because body OML, junction evidence and control hardware are
missing; no trim is attempted and no circulation root or lifting-only pitch
closure is reported as equilibrium. Stall sequencing remains unsupported.

## Remaining dependencies

- **Section aerodynamics (A/C):** complete passed/replayed banks for every
  installed foil and deflection over the converged local Re/α domain; rate and
  sideslip coverage; post-peak/stall envelope. Historical low-Re interpolation
  and missing-row failures remain failed; the real-pair spiral rejection stands.
- **Body/junction:** revision-bound body OML for each family; junction
  evidence; any shielding/separation model must be separately qualified.
- **Inertia/hardware:** hinge, servo, linkage, receiver, battery masses and
  placements; body/equipment distributions; measured delivered mass/CG/full
  inertia (`MeasuredAircraftProperties`).
- **Control:** `ControlHardware` declarations, hinge-moment evidence,
  `AuthorityRequirement` derived from mission/disturbance cases.
- **Physical:** B's construction is not transferred; family load paths,
  material/process coupons, bench and flight evidence remain required.
