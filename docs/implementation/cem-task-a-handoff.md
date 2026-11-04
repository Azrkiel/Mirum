# Task A handoff to C

Receiving-branch integration is recorded separately in
[A into C integration](cem-a-c-integration.md). The results below remain the
original A-run results; consult that record for validation on the merged C runtime.

A is ready for C to consume its contracts, fixtures, bounded solver adapters and
specific numerical references. **A remains open; C's native family qualification
and physical admission are not complete.** Continue on
`cem-task-a-aerodynamic-verification`; this continuation starts from `aae2b7e`.

## Stable integration boundary

- `backend/cem/aero/contracts.py` remains `mirum-aero-1`: immutable SI installed
  geometry, operating points, station polars, loads, body and structural cases.
- `backend/cem/aero/fixtures.py` and `examples/cem/aero-contract-fixture.json`
  retain their original interfaces. The analytic half-wing reaction is 0.5 N,
  with root bending moment 0.125 Nm. Sentinel hashes identify fixtures only.
- Geometry axes are x aft, y starboard, z up; My is nose-up. Moments are about
  installed CG. Angles use degrees, derivatives radians; symmetric surfaces
  explicitly mirror the positive-y half. See `cem-aerodynamic-verification.md`.
- Tailless-swept, canard and tandem remain disabled in the numerical adapter.
  Their contract identities do not authorize native or physical admission.
- A owns aerodynamic adapters, numerical references and replay. C owns its
  builders, reflex/control representation, trim, interference and stall-order
  evidence. B owns applicable construction and load-path evidence. No C branch
  or deployment branch was merged or changed by this continuation.

## Native qualification results

Three separately frozen, idealized rigid NACA 0012 references pass complete
160/240-panel native polars, coupled inclined-glide force/moment equilibrium,
12/24-strip lifting refinement, 16/6–24/8 AVL refinement, force/axis checks and
unchanged mission stability limits. Inputs live in `backend/cem/data/`.

| Frozen input | Mass (kg) | CG x (m) | Static margin | Distance from 10 m (m) |
| --- | ---: | ---: | ---: | ---: |
| `aero-conventional-reference-v1.json` | 0.312 | 0.259 | 0.1048868 | 54.9568402 |
| `aero-t-tail-reference-v1.json` | 0.313 | 0.254 | 0.1185160 | 55.0687632 |
| `aero-v-tail-reference-v1.json` | 0.313 | 0.257 | 0.1385981 | 55.0295210 |

The T-tail places the horizontal tail at fin-top height, z = 0.15 m. The V-tail
has a symmetric 0.225 m panel at 35 degrees. All hardware, mass, incidence,
placement and CG values are prescribed before solving; none is adjusted inside
an equilibrium or comparison. The cases use Re 230,000/300,000/350,000,
alpha −2° to 3° by 0.25°, Ncrit 9 and transition limits 1/1.

Each separately declared aft-CG countercase is rejected for native pitch
instability: x = 0.33 m for conventional, 0.40 m for T/V. This is a stability
assessment at the positive case's operating point, with no unstable trim or
flight equilibrium claimed. Family-specific reference selection is required in
both comparison execution and parent replay; a conventional proof cannot be
substituted for a T/V reference.

These are specific numerical references, not family-wide geometry envelopes,
physical flight validation, installed-rod/material qualification or gain claims.
All reports keep `families_enabled=[]` and `verified_gain=false`. Source/runtime,
inputs, executable identities and raw artifacts bind each report. Replay checks
the raw files without executing a native solver.

## Low-Re result: qualification remains blocked

The original wide NACA 0006/Re 30,000 sweep still lacks converged rows. At Ncrit
9, ascending/repeated sweeps miss −4°, 4°, 7.5° and 8°; zero-centered continuation
misses −4°, 4° and 7°; per-point initialization misses 7°. The 8° coefficient is
sequence-sensitive. The repaired headless SIGFPE and these aerodynamic gaps are
separate issues. No sequence union, row splicing or fallback is admitted.

The new `aero-low-re-section-reference-v1.json` tests the narrower −3° to 3°
window at Re 30,000/40,000/50,000/60,000. All sampled 0.25° grids are complete at
160/240 panels; initialization audits are complete, repeatable and agree within
the declared thresholds. Maximum panel differences are approximately 0.0003 Cl,
0.00001 Cd and 0.0001 Cm.

**The narrower qualification fails at interpolation coverage.** The independent
240-panel query at Re sqrt(30,000 × 40,000) ≈ 34,641.016 and 0.125° spacing lacks
the 2.125° row. Agreement on the sampled coarse grids does not establish the
intervening envelope. The failure and completed preceding checks are retained
and replayed; forged success/admission is rejected. No production grid was
silently narrowed, and no aircraft or family was admitted from this section test.

Native XFOIL output headers now also have to match the requested NACA section,
Re, zero Mach, Ncrit and both transition controls, allowing only native header
rounding. Matching input commands alone are insufficient evidence.

## End-to-end and regression status

The combined CEM/glider/UI/native regression suite passed **317 tests**, with
two dependency deprecation warnings. Native binaries were selected, including
positive/countercase replay, failed-section replay and forged-evidence checks.
`pip check`, source compilation and `git diff --check` also passed. See
[cem-a-qualification-results.json](cem-a-qualification-results.json) for the
source/runtime, input, executable and evidence identities.

The fresh 16-evaluation, seed-42 HTTP run produced eight shaped elites and the
+0.13605958335011792 m primary screening difference. The matching T-tail
reference passed parent evidence reconstruction. Both actual independent arms
failed incomplete NACA 0006 polar coverage; both legacy AVL arms retained the
spiral-instability rejection. Result: `inconclusive`, `verified_gain=false`, and
comparison bundle HTTP 404. The temporary API was stopped; the existing service
on port 8000 was left alone.

## Next qualification work

1. Resolve low-Re missing-row and interpolation coverage using a justified
   numerical/physical method and explicit transition/stall scope. Coarse-grid
   agreement does not justify interpolation through missing evidence.
2. Extend each existing family's reference only through separately declared
   positive and rejected cases over the intended geometry/operating envelope.
   The three frozen cases do not qualify junction flow, stall or all T/V designs.
3. Preserve the real pair's spiral rejection. A new stable design needs a new
   geometry/hardware revision and fresh evidence, with no in-solve hardware edits.
4. Establish body/interference error envelopes beyond the current engineering
   allowances; add applicable empirical flight/material validation.
5. C must qualify tailless reflex/control trim and stability, canard interference
   and stall order, and tandem lift sharing/interference/stall behavior. Extend
   section/control contracts explicitly where required. Removing a family guard
   or relabelling a conventional surface is insufficient. Physical family
   admission additionally needs B's construction/load-path closure.

## Reproduce and transfer

Build native tools with `scripts/cem/build_native_aero.py`; select the generated
binaries with `MIRUM_XFOIL_BIN` and `MIRUM_AVL_BIN`. Put their directories on PATH
for legacy tests. Preserve the 20-second invocation and 1 MiB file caps. Run
native qualifications sequentially on a CPU-limited workspace; concurrent runs
can time out and are not acceptable success evidence.

```sh
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest \
  backend/tests/test_cem* backend/tests/test_glider* frontend/test_cem_ui.py \
  analysis/tests/test_avl.py analysis/tests/test_airfoil.py -q
python scripts/cem/qualify_aero.py --case conventional --directory work/reference-conventional
python scripts/cem/qualify_aero.py --case t_tail --directory work/reference-t
python scripts/cem/qualify_aero.py --case v_tail --directory work/reference-v
python scripts/cem/qualify_aero.py --directory work/reference-t --replay
python scripts/cem/qualify_aero_section.py --directory work/section-reference
python scripts/cem/qualify_aero_section.py --directory work/section-reference --replay
python scripts/cem/audit_aero_polars.py --directory work/wide-audit
python scripts/cem/audit_aero_polars.py --directory work/wide-audit --replay
```

A qualification command exits 0 only for a passed reference. The failed narrow
section exits 1 even when its replay faithfully reconstructs the failure. The
wide audit's zero status means diagnostic execution, not complete coverage or
admission. Every section result withholds aircraft admission and gain.

Raw evidence is retained locally under `work/qualification-next/`; these are not
hosted downloads. Frozen inputs, scripts and the checked-in result receipt support
reproduction on C's own runtime. New source or executable identities require
fresh evidence. The earlier `work/qualification/` evidence is historical.
Deployment, environment publication, fresh-task restoration and empirical flight
validation have not been performed.
