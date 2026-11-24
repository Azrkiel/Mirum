"""Seeded 200-sample local polar uncertainty, explicitly not a re-trim ensemble."""
import math
import numpy as np
from .physics import trajectory
from .validity import DEGRADED_CD_FACTOR_P90, DEGRADED_CD_LOG_SIGMA


def uncertainty(req, result, calibration, process, launch, samples=200):
    rng = np.random.default_rng(req.discovery.seed)
    values = {'duration_s': [], 'distance_m': []}
    sustained = invalid = 0
    cl, cd = result['CL_trim'], result['CD_trim']
    ar = result['aspect_ratio']
    # Component drag evidence is retained by the CEM evaluator.
    profile = result['cd_profile']
    induced = cd - profile
    # Decision P1 degraded band (50,000 <= min station Re < 70,000): widen the
    # profile-drag prior. Non-degraded designs draw no extra samples, so their
    # seeded bands are unchanged.
    degraded = bool(result['validity_envelope']['low_re_degraded'])
    for _ in range(samples):
        draws = {k: math.exp(rng.normal(0., calibration[k]['log_sigma']))
                 for k in ('cd0_factor', 'cl_max_factor', 'oswald_factor')}
        mass = math.exp(rng.normal(0., calibration['mass_factor'][process]['log_sigma']))
        eta = min(1., max(0., launch['eta'] * math.exp(rng.normal(0., calibration['eta_launch'][req.launch.method]['log_sigma']))))
        speed = result['glide_speed_m_s'] * math.sqrt(mass)
        low_re = math.exp(rng.normal(0., DEGRADED_CD_LOG_SIGMA)) if degraded else 1.
        drag = profile * draws['cd0_factor'] * low_re + induced / draws['oswald_factor']
        sink = speed * math.sin(math.atan2(drag, cl))
        if cl > result['CLmax_est'] * draws['cl_max_factor'] * .85:
            # Failed samples count as zero performance, never silently dropped.
            invalid += 1
            values['duration_s'].append(0.)
            values['distance_m'].append(0.)
            continue
        height = req.launch.height_m + eta * launch['speed_m_s']**2 / (2 * 9.81)
        env = req.environment
        t = trajectory(height, speed, sink, env.wind_m_s, env.uplift_m_s)
        if t['state'] == 'sustained_lift_limited':
            sustained += 1
            continue
        for key in values:
            values[key].append(t[key])
    bands = {}
    for key, data in values.items():
        # If sustained samples exist, a finite unconditional percentile is not
        # established without a lift-region model; don't condition on descents.
        bands[key] = None if sustained else dict(zip(('p10', 'p50', 'p90'), map(float, np.percentile(data, [10, 50, 90]))))
    return dict(samples=samples, bands=bands, sustained_samples=sustained, invalid_samples=invalid,
                method='seeded local polar propagation with fixed trim CL; not a full re-trim ensemble',
                prior_state=calibration['state'],
                low_re_degraded_prior=dict(applied=degraded, log_sigma=DEGRADED_CD_LOG_SIGMA if degraded else None,
                                           p90_profile_drag_factor=DEGRADED_CD_FACTOR_P90 if degraded else None,
                                           state='engineering placeholder; replace with measured UIUC error'),
                assumptions=['Independent log-normal engineering priors, not measured confidence intervals.',
                    'Failed stall samples have zero performance; sustained lift requires a bounded region model.'])
