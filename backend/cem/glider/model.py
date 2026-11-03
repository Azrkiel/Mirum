"""Reuse Mirum's existing sampling and aerodynamic admission, with physical loads."""
import copy
import math

from gliders.spec import GliderRequest, Materials
from gliders.model.assembly import Glider, MassItem
from gliders.search import admissible
from gliders.model.analysis import evaluate, _aero

from .physics import atmosphere, launch_state, trajectory
from .processes import materials, spar_size, manufacturing_checks

SUPPORTED_FAMILIES = {'conventional': 'conventional', 't_tail': 'T', 'v_tail': 'V'}


def configuration(req, calibration, process):
    payload = req.constraints.payload
    families = list(SUPPORTED_FAMILIES) if req.discovery.families == 'all' else [
        f for f in req.discovery.families if f in SUPPORTED_FAMILIES]
    if not families:
        raise ValueError('Selected families lack qualified builders and admission checks')
    ranges = {'tail': {'kind': [SUPPORTED_FAMILIES[f] for f in families]}}
    if process in ('sheet_foam', 'hybrid'):
        ranges['wing'] = {'style': ['polyhedral'], 'washout_deg': [0., 2.]}
    request = GliderRequest(objective='distance' if req.objective == 'distance_m' else 'duration',
        launch_height_m=max(.001, req.launch.height_m), payload_mass_kg=payload.mass_kg if payload else 0.,
        payload_diameter_m=payload.diameter_m if payload else .045,
        payload_length_m=payload.length_m if payload else .06,
        max_wingspan_m=req.constraints.max_span_m, max_length_m=req.constraints.max_length_m,
        max_total_mass_kg=req.constraints.max_mass_kg, max_flight_speed_m_s=req.constraints.max_speed_m_s,
        evaluations=req.discovery.budget_evals, top_k=req.discovery.top_k, seed=req.discovery.seed,
        ranges=ranges, materials=Materials(**materials(process, calibration)))
    cfg = request.configuration()
    env, air = req.environment, atmosphere(req.environment)
    cfg['mission'].update(launch_height_m=launch_state(req.launch, calibration)['effective_height_m'],
                          air_density_kg_m3=air['density_kg_m3'], dynamic_viscosity_pa_s=air['dynamic_viscosity_pa_s'],
                          wind_m_s=env.wind_m_s, uplift_m_s=env.uplift_m_s)
    cfg['cem_context'] = dict(altitude_m=env.altitude_m,
        temperature_deviation_k=env.temperature_c - (15. - .0065 * env.altitude_m),
        **{k: calibration[k]['median'] for k in ('cd0_factor', 'cl_max_factor', 'oswald_factor')})
    cfg['cem_launch_method'] = req.launch.method
    return request, cfg


class SizedGlider(Glider):
    """Full-span catalogue spar sized against the mass cap: conservative and stable."""
    def spar(self):
        return spar_size(self.cfg['limits']['max_total_mass_kg'], self.wing.span, self.cfg['cem_launch_method'])

    def mass_items(self, include_ballast=True, include_payload=True):
        items = super().mass_items(include_ballast, include_payload)
        spar = self.spar()
        x, z = self.wing_position()
        # Distributed rod points preserve its spanwise inertia in the sizing model.
        for i in range(32):
            y = self.wing.span * ((i + .5) / 32 - .5)
            items.append(MassItem('spar', spar['mass_kg'] / 32, x + .3 * self.wing.root_chord, y, z))
        return items


def as_sized(gl):
    return SizedGlider(gl.wing, gl.fuse, gl.tail, gl.cfg, ballast_kg=gl.ballast_kg,
                      tail_incidence_deg=gl.tail_incidence_deg, payload_x=gl.payload_x, wing_x=gl.wing_x)


def evaluate_candidate(gl, request, mission, calibration, process):
    result = admissible(gl, request, evaluate(gl))
    spar = gl.spar()
    manufacture = manufacturing_checks(gl, process, spar)
    result.update(spar=spar, manufacturability=manufacture)
    if manufacture['state'] != 'passed':
        result.update(valid=False, fail_reasons='; '.join(filter(None, [result['fail_reasons'], *manufacture['failures']])))
    if result['valid']:
        # Query the actual operating polar at all stations. A scalar root Re or
        # the optimizer's objective alone is not sufficient validity evidence.
        import numpy as np
        from gliders.model.components import naca
        air = atmosphere(mission.environment)
        confidence, reynolds = [], []
        for surface in [gl.wing_surface(), *gl.tail_surfaces()]:
            for section in surface.sections:
                re = air['density_kg_m3'] * result['glide_speed_m_s'] * section.chord / air['dynamic_viscosity_pa_s']
                polar = naca(surface.airfoil).get_aero_from_neuralfoil(
                    alpha=result['alpha_trim_deg'] + section.twist, Re=re, model_size='large')
                confidence.extend(np.asarray(polar['analysis_confidence']).reshape(-1).tolist())
                reynolds.append(re)
        result['validity_envelope'] = dict(min_confidence=min(confidence), min_station_reynolds=min(reynolds),
                                         confidence_floor=.8, reynolds_floor=30000.)
        if min(confidence) < .8 or min(reynolds) < 30000 or not all(math.isfinite(c) for c in confidence):
            result.update(valid=False, fail_reasons='station polar outside confidence/Reynolds envelope')
        aero = _aero(gl.airplane(gl.mass_summary()['cg']), result['glide_speed_m_s'], result['alpha_trim_deg'])
        # Profile fraction of calibrated total drag is used in Monte Carlo and
        # identifiable flight-test updates; no guessed profile/induced split.
        dp = float(np.asarray(aero['D_profile']).reshape(-1)[0]) * calibration['cd0_factor']['median']
        di = float(np.asarray(aero['D_induced']).reshape(-1)[0]) / calibration['oswald_factor']['median']
        result['cd_profile'] = result['CD_trim'] * dp / (dp + di)
        launch = launch_state(mission.launch, calibration)
        env = mission.environment
        result['trajectory'] = trajectory(launch['effective_height_m'], result['glide_speed_m_s'],
                                          result['sink_rate_m_s'], env.wind_m_s, env.uplift_m_s)
        result['objective_value'] = result['trajectory'][mission.objective]
        if result['objective_value'] is None:
            result.update(valid=False, fail_reasons='sustained lift has no bounded duration/distance objective')
    return result
