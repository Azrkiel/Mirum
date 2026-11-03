"""Force estimates at a prescribed operating point; no equilibrium/trim claim."""
import math
import aerosandbox as asb
from .model.analysis import geometry_checks, _f


def evaluate_conditions(gl, spec):
    fc = spec.flight_conditions
    atmosphere = asb.Atmosphere(altitude=fc.altitude_m, method='isa')
    rho = _f(atmosphere.density())
    mu = _f(atmosphere.dynamic_viscosity())
    mass = gl.mass_summary()
    airplane = gl.airplane(xyz_ref=mass['cg'])
    op = asb.OperatingPoint(atmosphere=atmosphere, velocity=fc.airspeed_m_s,
                            alpha=fc.alpha_deg, beta=fc.beta_deg)
    aero = asb.AeroBuildup(airplane, op).run()
    keys = {'lift_N': 'L', 'drag_N': 'D', 'side_force_N': 'Y',
            'roll_moment_Nm': 'l_b', 'pitch_moment_Nm': 'm_b', 'yaw_moment_Nm': 'n_b',
            'CL': 'CL', 'CD': 'CD', 'CY': 'CY', 'Cl': 'Cl', 'Cm': 'Cm', 'Cn': 'Cn'}
    result = {name: _f(aero[key]) for name, key in keys.items()}
    if not all(math.isfinite(value) for value in result.values()):
        raise ValueError('Nonfinite aerodynamic result at specified flight conditions')
    warnings, _ = geometry_checks(gl, gl.cfg['limits'], gl.cfg['mission'])
    if fc.airspeed_m_s > spec.request.max_flight_speed_m_s:
        warnings.append('specified airspeed exceeds designer maximum')
    if mass['mass'] > spec.request.max_total_mass_kg:
        warnings.append('mass exceeds designer maximum')
    result.update(analysis_mode='specified', equilibrium_verified=False,
                  operating_conditions=fc.model_dump(),
                  mass_kg=float(mass['mass']), air_density_kg_m3=rho,
                  dynamic_viscosity_Pa_s=mu, dynamic_pressure_Pa=.5*rho*fc.airspeed_m_s**2,
                  reynolds_mac=rho*fc.airspeed_m_s*_f(airplane.c_ref)/mu,
                  lift_to_weight=result['lift_N']/(float(mass['mass'])*gl.cfg['mission']['gravity_m_s2']),
                  warnings=warnings)
    return result
