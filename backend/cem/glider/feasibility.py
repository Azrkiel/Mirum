"""Sub-second optimistic mission screen. A reference failure never stops search."""
import json
import math
from pathlib import Path

from .physics import atmosphere, launch_state, trajectory, G
from .processes import PROCESSES


def feasibility(req, calibration):
    table = json.loads((Path(__file__).parents[1] / 'data/airfoil_bounds.json').read_text())
    launch, air = launch_state(req.launch, calibration), atmosphere(req.environment)
    processes = list(PROCESSES) if req.process == 'auto' else [req.process]
    # Use the largest table bounds, not a guessed operating Re; no false rejection
    # caused by selecting a pessimistic Reynolds bin before sizing.
    ld = max(r['ld_upper'] for r in table['rows'])
    efficiency = max(r['cl_1_5_over_cd_upper'] for r in table['rows'])
    payload = req.constraints.payload.mass_kg if req.constraints.payload else 0.
    # Maximum possible area within the existing chord/span envelope. Omitting
    # spar, tail and pod is intentional for an optimistic loading lower bound.
    max_area = req.constraints.max_span_m * .22
    areal_min = min(PROCESSES[p]['wing_areal_density_kg_m2'] for p in processes)
    loading = G * (areal_min + (payload + .010) / max_area)
    sink = math.sqrt(2 * loading / air['density_kg_m3']) / efficiency
    height = launch['effective_height_m']
    env = req.environment
    target = req.reference_target
    required_ld = target / height if target and height > 0 and req.objective == 'distance_m' else None
    required_sink = height / target if target and req.objective == 'duration_s' else None
    bound = height * ld if req.objective == 'distance_m' else height / sink
    qualified = env.kind == 'still_air'
    calibration_matches = (all(calibration[k]['median'] == 1. for k in ('cd0_factor', 'cl_max_factor', 'oswald_factor'))
                           and all(calibration['mass_factor'][p]['median'] == 1. for p in processes))
    construction_matches = req.construction == 'screening_oml'
    calibration_matches = calibration_matches and construction_matches
    qualified = qualified and calibration_matches
    if not qualified:
        # Environment-dependent speed/lift coverage belongs to sizing. A finite
        # still-air bound must never reject sustained lift or wind missions.
        bound = None
    verdict = 'marginal'
    if target and qualified:
        verdict = 'infeasible' if target > bound else 'marginal' if target > .8 * bound else 'feasible'
    needed_height = (target / ld if req.objective == 'distance_m' else target * sink) if target else None
    if not calibration_matches:
        needed_height = None
    levers = [dict(change='relax_reference_target', value=bound, unit=req.objective),
              dict(change='effective_launch_height', value=needed_height, unit='m')]
    if target and calibration_matches:
        # Distance uplift bound assumes V_horizontal = optimistic L/D * sink.
        desired_time = target / (ld * sink) if req.objective == 'distance_m' else target
        levers.append(dict(change='mean_uplift', value=max(0., sink - height / desired_time), unit='m/s'))
        levers.append(dict(change='wing_loading_for_duration', value=(height / desired_time * efficiency)**2 *
                          air['density_kg_m3'] / 2, unit='N/m2'))
    return dict(verdict=verdict, verdict_scope='sampled_model_envelope', continue_search=True,
                objective=req.objective, reference_target=target, optimistic_bound=bound,
                reference_gap=max(0., target - bound) if target and bound is not None else None,
                required_lift_to_drag=required_ld, required_sink_m_s=required_sink,
                optimistic_lift_to_drag=ld if calibration_matches else None,
                optimistic_cl_1_5_over_cd=efficiency if calibration_matches else None,
                optimistic_sink_m_s=sink if calibration_matches else None,
                wing_loading_lower_bound_n_m2=loading if calibration_matches else None,
                bound_state=('rebuild_required_for_construction' if not construction_matches else
                             'sampled_initial_model' if calibration_matches else 'rebuild_required_for_calibration'),
                binding_constraint='aerodynamic efficiency / launch energy' if verdict == 'infeasible' else
                                   'requires physics sizing; feasibility is not design acceptance',
                launch=launch, atmosphere=air, levers=levers,
                bounds_version=table['version'], bounds_source=table['source'],
                assumptions=launch['assumptions'] + ['Optimistic maximum area; tail, spar and pod mass omitted.',
                    'Finite-wing envelope assumes AR=14, e=1, profile drag divided by 1.25; body/tail drag omitted.',
                    'Infeasible means outside this sampled-model envelope, not a universal physical proof.',
                    'NeuralFoil envelope lacks independent XFOIL cross-validation and physical calibration.'])
