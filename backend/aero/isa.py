"""ISA Standard Atmosphere (ICAO Doc 7488, 1993)."""
from dataclasses import dataclass
import math

@dataclass
class AtmState:
    h: float       # geometric altitude m
    T: float       # temperature K
    P: float       # pressure Pa
    rho: float     # density kg/m3
    a: float       # speed of sound m/s
    mu: float      # dynamic viscosity Pa·s
    nu: float      # kinematic viscosity m2/s

# Constants
R = 287.05       # J/(kg·K)
g = 9.80665      # m/s2
GAMMA = 1.4
T0 = 288.15      # K sea-level
P0 = 101325.0    # Pa sea-level
L_TROP = 0.0065  # K/m troposphere lapse
H_TROP = 11000.0 # m tropopause
T_STRAT = 216.65 # K stratosphere
MU0 = 1.716e-5   # Pa·s Sutherland reference
T0_S = 273.15    # K Sutherland reference
S_SUTH = 110.4   # K Sutherland constant


def _sutherland(T: float) -> float:
    return MU0 * (T / T0_S)**1.5 * (T0_S + S_SUTH) / (T + S_SUTH)


def isa(h: float) -> AtmState:
    """Return ISA atmospheric state at geometric altitude h (metres)."""
    if h < 0 or h > 20000:
        raise ValueError(f"Altitude {h} m outside implemented range 0-20 km")
    if h <= H_TROP:
        T = T0 - L_TROP * h
        P = P0 * (T / T0) ** (g / (L_TROP * R))
    else:
        T_11 = T0 - L_TROP * H_TROP
        P_11 = P0 * (T_11 / T0) ** (g / (L_TROP * R))
        T = T_STRAT
        P = P_11 * math.exp(-g * (h - H_TROP) / (R * T))
    rho = P / (R * T)
    a = math.sqrt(GAMMA * R * T)
    mu = _sutherland(T)
    return AtmState(h=h, T=T, P=P, rho=rho, a=a, mu=mu, nu=mu / rho)


def density_altitude(rho: float) -> float:
    """Inverse: find altitude from density (bisect troposphere)."""
    import scipy.optimize
    f = lambda h: isa(h).rho - rho
    return scipy.optimize.brentq(f, 0, H_TROP)
