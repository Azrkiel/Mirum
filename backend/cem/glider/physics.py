"""Analytic launch, atmosphere and trajectory functions; no geometry or LLM."""
import math

G = 9.81
DEFAULT_SPEEDS = {'drop': 0., 'hand_toss': 8., 'discus': 25., 'catapult': 30.}


def atmosphere(environment):
    # Hydrostatic ISA pressure with explicitly supplied ambient temperature.
    pressure = 101325 * (1 - 2.25577e-5 * environment.altitude_m) ** 5.25588
    temperature_k = environment.temperature_c + 273.15
    density = pressure / (287.05 * temperature_k)
    viscosity = 1.716e-5 * (temperature_k / 273.15) ** 1.5 * (273.15 + 110.4) / (temperature_k + 110.4)
    return dict(density_kg_m3=density, dynamic_viscosity_pa_s=viscosity)


def launch_state(launch, calibration):
    speed = launch.launch_speed_m_s
    assumed = speed is None
    if assumed:
        speed = DEFAULT_SPEEDS[launch.method]
    eta = calibration['eta_launch'][launch.method]['median']
    height = launch.height_m + eta * speed ** 2 / (2 * G)
    return dict(effective_height_m=height, speed_m_s=speed, eta=eta,
                assumptions=([f'{launch.method} release speed assumed {speed:g} m/s'] if assumed else []) +
                [f'Launch zoom efficiency {eta:g} is a calibration prior; release height includes tow climb.'])


def trajectory(height, speed, sink, wind=0., uplift=0.):
    if not all(math.isfinite(x) for x in (height, speed, sink, wind, uplift)) or min(height, speed, sink) < 0 or speed <= sink:
        raise ValueError('Invalid trajectory evidence')
    ground_speed = math.sqrt(speed * speed - sink * sink) + wind
    if uplift >= sink:
        return dict(state='sustained_lift_limited', duration_s=None, distance_m=None,
                    distance_range_m=[0., None])
    duration = height / (sink - uplift)
    return dict(state='descending' if ground_speed > 0 else 'no_forward_progress',
                duration_s=duration, distance_m=duration * max(0., ground_speed))
