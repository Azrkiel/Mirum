"""Explicit axes, units and exact frame transformations. SI units throughout.

Frames (all right handed):

- ``G`` geometry axes (shared ``mirum-aero-1``): x aft, y starboard, z up.
  Moment components: My positive nose-up, Mx positive starboard-wing-up
  (left roll), Mz positive nose-left (left yaw).
- ``B`` standard flight-mechanics body axes: x forward, y starboard, z down.
  ``G`` and ``B`` differ by a proper 180-degree rotation about y, so both
  vectors and moment pseudovectors map with the same matrix ``T_GB``:
  L_B = -Mx_G, M_B = My_G, N_B = -Mz_G.
- ``W`` standard wind axes: x along the aircraft velocity relative to the air,
  z in the body symmetry plane pointing down for positive lift. Aerodynamic
  drag D = -F.x_W, side force Y = F.y_W, lift L = -F.z_W.
- ``E`` local earth axes, north-east-down. Attitude uses the standard 3-2-1
  Euler sequence (psi, theta, phi) from E to B.

Angles passed to public functions are degrees unless the name ends in
``_rad``. Rates are rad/s in body axes ``B`` (p roll, q pitch, r yaw).
"""
import math

import numpy as np

G_FROM_B = np.diag([-1., 1., -1.])  # proper rotation, det +1; its own inverse
STANDARD_GRAVITY_M_S2 = 9.80665


def _finite(*values):
    array = np.asarray(values, dtype=float)
    if not np.isfinite(array).all():
        raise ValueError('Nonfinite frame input')


def vector(value):
    out = np.asarray(value, dtype=float)
    if out.shape != (3,) or not np.isfinite(out).all():
        raise ValueError('Finite 3-vector required')
    return out


def body_to_geometry(v_b):
    return G_FROM_B @ vector(v_b)


def geometry_to_body(v_g):
    return G_FROM_B @ vector(v_g)


def tensor_body_to_geometry(tensor_b):
    t = np.asarray(tensor_b, dtype=float)
    return G_FROM_B @ t @ G_FROM_B.T


def tensor_geometry_to_body(tensor_g):
    t = np.asarray(tensor_g, dtype=float)
    return G_FROM_B @ t @ G_FROM_B.T


def body_from_wind(alpha_deg, beta_deg):
    """Columns are W unit vectors expressed in B (R_BW)."""
    _finite(alpha_deg, beta_deg)
    a, b = math.radians(alpha_deg), math.radians(beta_deg)
    ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
    return np.array([[ca*cb, -ca*sb, -sa],
                     [sb, cb, 0.],
                     [sa*cb, -sa*sb, ca]])


def geometry_from_wind(alpha_deg, beta_deg):
    """Columns are W unit vectors expressed in G (R_GW)."""
    return G_FROM_B @ body_from_wind(alpha_deg, beta_deg)


def freestream_direction(alpha_deg, beta_deg=0.):
    """Unit direction of the relative air flow (air past aircraft) in G."""
    return -geometry_from_wind(alpha_deg, beta_deg)[:, 0]


def aerodynamic_components(force_g, alpha_deg, beta_deg=0.):
    """Drag, side force and lift (N) of a G-axes force."""
    w = geometry_from_wind(alpha_deg, beta_deg)
    f = vector(force_g)
    return dict(drag_n=float(-f @ w[:, 0]), side_n=float(f @ w[:, 1]), lift_n=float(-f @ w[:, 2]))


def force_from_wind(drag_n, side_n, lift_n, alpha_deg, beta_deg=0.):
    _finite(drag_n, side_n, lift_n)
    w = geometry_from_wind(alpha_deg, beta_deg)
    return -drag_n*w[:, 0] + side_n*w[:, 1] - lift_n*w[:, 2]


def body_from_earth(phi_deg, theta_deg, psi_deg=0.):
    """Standard 3-2-1 direction cosine matrix R_BE (earth NED -> body B)."""
    _finite(phi_deg, theta_deg, psi_deg)
    f, t, s = (math.radians(v) for v in (phi_deg, theta_deg, psi_deg))
    cf, sf, ct, st, cs, ss = math.cos(f), math.sin(f), math.cos(t), math.sin(t), math.cos(s), math.sin(s)
    return np.array([[ct*cs, ct*ss, -st],
                     [sf*st*cs-cf*ss, sf*st*ss+cf*cs, sf*ct],
                     [cf*st*cs+sf*ss, cf*st*ss-sf*cs, cf*ct]])


def gravity_force_geometry(mass_kg, phi_deg, theta_deg, g_m_s2=STANDARD_GRAVITY_M_S2):
    """Weight vector (N) in G. Gravity acts at the CG: zero moment about it."""
    _finite(mass_kg, g_m_s2)
    if mass_kg <= 0 or g_m_s2 <= 0:
        raise ValueError('Positive mass and gravity required')
    return body_to_geometry(body_from_earth(phi_deg, theta_deg) @ np.array([0., 0., mass_kg*g_m_s2]))


def transfer_moment(moment_nm, force_n, from_point_m, to_point_m):
    """Exact rigid transfer: M_to = M_from + (from - to) x F."""
    return vector(moment_nm) + np.cross(vector(from_point_m)-vector(to_point_m), vector(force_n))


def flight_path_angle_deg(alpha_deg, beta_deg, phi_deg, theta_deg):
    """Climb angle (positive up) of the velocity vector, from exact kinematics."""
    velocity_b = body_from_wind(alpha_deg, beta_deg)[:, 0]
    velocity_e = body_from_earth(phi_deg, theta_deg).T @ velocity_b
    return math.degrees(math.asin(max(-1., min(1., -float(velocity_e[2])))))


AXES = dict(geometry='G: x aft, y starboard, z up; My nose-up, Mx left-roll, Mz left-yaw',
            body='B: x forward, y starboard, z down; L right-roll, M nose-up, N right-yaw',
            wind='W: x along aircraft velocity, z down in symmetry plane',
            earth='E: north-east-down; 3-2-1 Euler psi, theta, phi',
            units='SI: N, N m, m, kg, kg m^2, s; angles degrees unless *_rad; rates rad/s')
