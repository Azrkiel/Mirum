"""Revision-bound shape-only experiments, separate from the discovery budget."""
import copy
from dataclasses import asdict

from cem.core import canonical_json, digest
from gliders.design_spec import placement
from gliders.model.components import FuselageParams, TailParams
from gliders.model.assembly import Glider
from gliders.search import finite_json
from .model import configuration, SizedGlider, evaluate_candidate
from .shapes import ShapedWing, wing_from_parameters

LIMITS = [
    'Paired screening equilibrium; only chord factors change. No hardware rebalancing or tail adjustment.',
    'Mass, CG and inertia may change as consequences of planform changes; the source spar is held fixed.',
    'Unity factors use the same five stations, not the differently discretized template.',
    'Executable diagnostics alone cannot establish gain. Independent performance has separate coverage, stability, convergence and error gates.',
    'Any verified gain concerns the declared rigid numerical model; fabrication acceptance and flight validation remain separate.',
]


class FixedHardwareGlider(SizedGlider):
    def spar(self):
        if not hasattr(self, 'frozen_spar'):
            return super().spar()
        # Recompute loads at each arm's mass without selecting a different rod.
        spar = copy.deepcopy(self.frozen_spar)
        total = sum(p.mass for p in Glider.mass_items(self))+spar['mass_kg']
        ratio = total/spar['sizing_mass_kg']
        spar.update(stress_pa=spar['stress_pa']*ratio, root_moment_nm=spar['root_moment_nm']*ratio,
                    sizing_mass_kg=total, sizing_iterations=0, sizing_policy='fixed_source_rod')
        return spar


def comparison_pair(req, calibration, elite):
    revision = digest({k: elite[k] for k in ('process', 'parameters', 'placement')})[:16]
    if elite['elite_id'] != revision:
        raise ValueError('Shape elite revision mismatch')
    if req.discovery.wing_shape != 'multi_station' or req.construction != 'screening_oml':
        raise ValueError('Comparison requires screening wing shape discovery')
    shaped = wing_from_parameters(elite['parameters']['wing'], req)
    if not isinstance(shaped, ShapedWing):
        raise ValueError('Select an elite with chord shape factors')
    _, cfg = configuration(req, calibration, elite['process'])
    parameters = elite['parameters']
    gl = FixedHardwareGlider(shaped, FuselageParams(**parameters['fuselage']),
                    TailParams(**parameters['tail']), copy.deepcopy(cfg), **elite['placement'])
    # Resolve implicit defaults once, then use the exact same hardware positions.
    gl.wing_x = gl.wing_position()[0]
    if cfg['mission']['payload_mass_kg']:
        gl.payload_x = gl.payload_position()
    gl.frozen_spar = gl.spar()
    baseline = copy.deepcopy(gl)
    baseline.wing = ShapedWing(**dict(asdict(shaped), chord_factors=(1.,)*5))
    return baseline, gl


def arm_inputs(gl, process):
    return finite_json(dict(process=process, parameters=dict(wing=asdict(gl.wing),
        fuselage=asdict(gl.fuse), tail=asdict(gl.tail)), placement=placement(gl),
        installed_spar={k: gl.frozen_spar[k] for k in ('diameter_m', 'span_m', 'mass_kg')}))


def compare_shape(req, calibration, elite, directory, *, independent_recheck=False):
    from .service import identity
    from cem.provenance import provenance
    from .rechecks import recheck_pair
    baseline, shaped = comparison_pair(req, calibration, elite)
    request, _ = configuration(req, calibration, elite['process'])
    arms = []
    for gl in (baseline, shaped):
        inputs = arm_inputs(gl, elite['process'])
        result = evaluate_candidate(gl, request, req, calibration, elite['process'], fixed_geometry=True)
        if inputs != arm_inputs(gl, elite['process']):
            raise ValueError('Comparison evaluator changed frozen hardware')
        arms.append(dict(**inputs, revision_sha256=digest(inputs), result=finite_json(result)))
    admitted = all(a['result'].get('valid') is True for a in arms)
    delta = arms[1]['result']['objective_value']-arms[0]['result']['objective_value'] if admitted else None
    recheck = (recheck_pair((baseline, shaped), arms, req, directory) if independent_recheck else
               dict(state='not_requested', missing=['XFOIL station polars', 'AVL trim and modes']))
    from cem.aero.verification import verify_pair
    independent = (verify_pair((baseline,shaped),arms,req,calibration,elite['process'],directory)
                   if independent_recheck else dict(state='not_requested',verified_gain=False))
    report = finite_json(dict(**identity(req, calibration), requirements=req.model_dump(mode='json'),
        calibration=calibration, provenance=provenance(), kind='shape_comparison',
        elite_id=elite['elite_id'], status='screened' if admitted else 'inconclusive',
        baseline=arms[0], shaped=arms[1], objective=req.objective, objective_delta=delta,
        verified_gain=independent['verified_gain'], engineering_certified=False, accepted_for_manufacture=False,
        independent_performance=independent,
        independent_recheck=recheck, model_limits=LIMITS,
        evaluations=2, discovery_budget_used=0))
    report['comparison_sha256'] = digest(dict(requirements=report['requirements'],
        calibration=calibration, revisions=[a['revision_sha256'] for a in arms]))
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'result.json').write_text(canonical_json(report))
    return report


def validate_comparison(report, payload, directory):
    """Parent recomputes the experimental controls and gain arithmetic."""
    from cem.evidence import number
    from .requirements import GliderMissionRequirements
    from .rechecks import validate_rechecks
    req = GliderMissionRequirements.model_validate(payload['requirements'])
    pair = comparison_pair(req, payload['calibration'], payload['elite'])
    if (report.get('kind') != 'shape_comparison' or type(report.get('verified_gain')) is not bool
            or report.get('accepted_for_manufacture') is not False
            or report.get('elite_id') != payload['elite']['elite_id'] or report.get('objective') != req.objective
            or report.get('evaluations') != 2 or report.get('discovery_budget_used') != 0):
        raise ValueError('Invalid shape comparison claims')
    arms = [report['baseline'], report['shaped']]
    for gl, arm in zip(pair, arms):
        inputs = arm_inputs(gl, payload['elite']['process'])
        if (any(canonical_json(arm.get(k)) != canonical_json(v) for k,v in inputs.items())
                or arm.get('revision_sha256') != digest(inputs)):
            raise ValueError('Comparison differs from frozen controls')
        result = arm['result']
        if type(result.get('valid')) is not bool or not isinstance(result.get('fail_reasons'), str):
            raise ValueError('Missing comparison admission')
        if result['valid']:
            if result['fail_reasons'] or result.get('fixed_geometry') is not True:
                raise ValueError('Missing fixed-geometry admission')
            for key in ('objective_value', 'mass_kg', 'CL_trim', 'CD_trim', 'glide_speed_m_s'):
                number(result.get(key), 0.)
            number(result.get('Cm_trim'), -.01, .01)
            number(result['validity_envelope']['min_confidence'], .8, 1.)
            number(result['validity_envelope']['min_station_reynolds'], 30000.)
            if result['manufacturability']['state'] != 'passed':
                raise ValueError('Missing comparison process screen')
            frozen = placement(gl)
            if (result.get('tail_incidence_deg') != frozen['tail_incidence_deg']
                    or result.get('ballast_kg') != frozen['ballast_kg']
                    or result.get('wing_le_x_m') != frozen['wing_x']):
                raise ValueError('Comparison result moved frozen hardware')
        if canonical_json(result.get('spar')) != canonical_json(gl.spar()):
            raise ValueError('Comparison result changed the fixed source spar')
    admitted = all(a['result']['valid'] for a in arms)
    delta = arms[1]['result']['objective_value']-arms[0]['result']['objective_value'] if admitted else None
    if delta is not None:
        number(report.get('objective_delta'))
    if report.get('objective_delta') != delta or report.get('status') != ('screened' if admitted else 'inconclusive'):
        raise ValueError('Invalid paired objective delta')
    binding = digest(dict(requirements=payload['requirements'], calibration=payload['calibration'],
                          revisions=[a['revision_sha256'] for a in arms]))
    if report.get('comparison_sha256') != binding:
        raise ValueError('Comparison revision binding mismatch')
    validate_rechecks(report['independent_recheck'], arms, payload.get('independent_recheck', False), directory, pair, req)
    from cem.aero.verification import validate
    verified=validate(report.get('independent_performance'),pair,arms,req,payload['calibration'],
                      payload['elite']['process'],directory,payload.get('independent_recheck',False))
    if report.get('verified_gain') is not verified:
        raise ValueError('Comparison gain differs from independent numerical evidence')
