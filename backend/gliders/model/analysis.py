"""
Evaluation of one assembled glider:

  1. Geometry / build checks (cheap, no aerodynamics)
  2. Neutral point  ->  nose ballast for the target static margin
  3. Trim: pick the best flight condition, then solve the tail incidence
     (decalage) so the glider flies there hands-off
  4. Stability derivatives at the trim point -> lateral eigenmodes
  5. Glide performance and final validity checks

Aerodynamics (AeroSandbox):
  * performance, trim and the low-Re pitch check: AeroBuildup, a fast
    semi-empirical buildup that uses NeuralFoil for low-Reynolds airfoil data
  * neutral point and lateral/rate derivatives: vortex-lattice method on the
    wings and tails + AeroBuildup fuselage increments
Roughly 1-2 s per design on a laptop.
"""
from __future__ import annotations

import math
import traceback

import numpy as np
import aerosandbox as asb

from .assembly import Glider

ALPHAS = np.arange(-2.0, 14.01, 0.5)


def _op(airplane, V, alpha, beta=0.):
    context = getattr(airplane, 'cem_context', None)
    kw = {'atmosphere': asb.Atmosphere(altitude=context['altitude_m'],
          temperature_deviation=context['temperature_deviation_k'], method='isa')} if context else {}
    return asb.OperatingPoint(velocity=V, alpha=alpha, beta=beta, **kw)


def _aero(airplane, V, alpha):
    result = asb.AeroBuildup(airplane, _op(airplane, V, alpha)).run()
    context = getattr(airplane, 'cem_context', None)
    if context:
        drag = result['D_profile'] * context['cd0_factor'] + result['D_induced'] / context['oswald_factor']
        result['CD'] = result['CD'] * drag / result['D']
    return result


def _f(v) -> float:
    return float(np.asarray(v).reshape(-1)[0])


# ---------------------------------------------------------------------- #
# Lateral-directional modes
# ---------------------------------------------------------------------- #
def lateral_modes(d: dict, V, rho, S, b, m, Ixx, Izz, gamma_rad, g=9.81) -> dict:
    """Eigenvalues of the linearised lateral equations, state = [beta, p, r, phi].

    Uses the derivatives AeroSandbox returns (rates normalised by b/2V).
    Ixz is neglected - fine for screening, not for a flight-control design."""
    q = 0.5 * rho * V ** 2
    k = b / (2 * V)
    Yb, Yp, Yr = q * S * d["CYb"] / m, q * S * d["CYp"] * k / m, q * S * d["CYr"] * k / m
    Lb, Lp, Lr = q * S * b * d["Clb"] / Ixx, q * S * b * d["Clp"] * k / Ixx, q * S * b * d["Clr"] * k / Ixx
    Nb, Np, Nr = q * S * b * d["Cnb"] / Izz, q * S * b * d["Cnp"] * k / Izz, q * S * b * d["Cnr"] * k / Izz
    A = np.array([
        [Yb / V, Yp / V, Yr / V - 1.0, g * math.cos(gamma_rad) / V],
        [Lb, Lp, Lr, 0.0],
        [Nb, Np, Nr, 0.0],
        [0.0, 1.0, 0.0, 0.0],
    ])
    return classify_lateral_eigs(np.linalg.eigvals(A))


def classify_lateral_eigs(eigs) -> dict:
    eigs = np.asarray(eigs)
    cplx = [e for e in eigs if abs(e.imag) > 1e-6]
    real = sorted([e.real for e in eigs if abs(e.imag) <= 1e-6])
    out = {"eigs": eigs}
    if cplx:
        e = max(cplx, key=lambda z: z.imag)
        wn = abs(e)
        out.update(dutch_roll_real=e.real, dutch_roll_wn=wn, dutch_roll_zeta=-e.real / wn)
    else:
        # Dutch roll split into two real roots (heavily damped); the middle ones
        out.update(dutch_roll_real=max(real[1:-1]) if len(real) > 2 else -1.0,
                   dutch_roll_wn=0.0, dutch_roll_zeta=1.0)
    out["roll_real"] = real[0] if real else float("nan")
    out["spiral_real"] = max(real, key=lambda r: -abs(r)) if real else float("nan")  # closest to 0
    sp = out["spiral_real"]
    out["spiral_time_to_double_s"] = math.log(2) / sp if sp > 0 else float("inf")
    return out


# ---------------------------------------------------------------------- #
# Geometry / build checks
# ---------------------------------------------------------------------- #
def geometry_checks(gl: Glider, lim: dict, mis: dict) -> tuple[list, dict]:
    fails, info = [], {}
    ref = gl.wing_ref()
    tv = gl.tail_volumes()
    info.update({"wing_area_m2": ref["S"], "wing_span_m": ref["b"], "wing_mac_m": ref["mac"],
                 "aspect_ratio": ref["AR"], "Vh": tv["Vh"], "Vv": tv["Vv"],
                 "tail_arm_m": tv["lh"], "tail_arm_over_mac": tv["lh"] / ref["mac"]})

    if ref["AR"] > lim["max_aspect_ratio"]:
        fails.append("aspect ratio too high (structure)")
    if gl.wing.tip_chord() < lim["min_tip_chord_m"]:
        fails.append("tip chord too small")
    lo, hi = lim["h_tail_volume_range"]
    if not lo <= tv["Vh"] <= hi:
        fails.append("horizontal tail volume " + ("too small" if tv["Vh"] < lo else "too large"))
    lo, hi = lim["v_tail_volume_range"]
    if not lo <= tv["Vv"] <= hi:
        fails.append("vertical tail volume " + ("too small" if tv["Vv"] < lo else "too large"))
    if tv["lh"] / ref["mac"] < lim["min_tail_arm_over_mac"]:
        fails.append("tail arm too short")

    # wing and tail must not overlap along the fuselage
    wing_te = max(s.x + s.chord for s in gl.wing_surface().sections)
    tail_le = min(s.x for surf in gl.tail_surfaces(0.0) for s in surf.sections)
    if tail_le - wing_te < lim["min_wing_tail_gap_m"]:
        fails.append("wing and tail too close / overlapping")
    if gl.fuse.wing_mount()[0] + gl.wing.root_chord > gl.fuse.length:
        fails.append("wing hangs off the fuselage")

    if gl.tail.kind == "T" and gl.tail.fin_tip_chord() < lim["t_tail_min_fin_tip_chord_frac"] * gl.tail.h_root_chord:
        fails.append("T-tail fin tip too narrow to mount stabiliser")

    if mis["payload_mass_kg"] > 0 and gl.payload_bay() is None:
        fails.append("payload does not fit in fuselage")
    return fails, info


# ---------------------------------------------------------------------- #
# Balancing and trimming
# ---------------------------------------------------------------------- #
def local_aero(airplane, V: float, alpha: float, below_deg: float = 1.0) -> dict:
    """Viscous AeroBuildup (NeuralFoil airfoil data) around the trim point.

    Returns CL, CD, Cm at alpha, and "Cm_monotonic": whether the pitching moment
    keeps falling with alpha from (alpha - below_deg) to alpha + 3 deg. Thin
    cambered airfoils at low Reynolds number can lose lift slope at low alpha,
    which would make the glider pitch-unstable in a shallow dive."""
    offs = np.unique(np.concatenate([[-below_deg], np.arange(0.0, 3.01, 1.0)]))
    r = _aero(airplane, V, alpha + offs)
    CL, CD, Cm = (np.asarray(r[k], dtype=float) for k in ("CL", "CD", "Cm"))
    i0 = int(np.argmin(np.abs(offs)))
    return {"CL": CL[i0], "CD": CD[i0], "Cm": Cm[i0], "Cm_monotonic": bool(np.all(np.diff(Cm) < 0))}


def stability_derivatives(airplane, V: float, alpha: float) -> dict:
    """Static and dynamic stability derivatives about airplane.xyz_ref.

    * Lifting surfaces: vortex-lattice method (VLM). It is inviscid and linear,
      which is exactly what you want for the neutral point and the lateral
      derivatives (it is what XFLR5/AVL do), and it is not disturbed by the
      wiggly low-Reynolds airfoil data.
    * Fuselage: increments from AeroBuildup's slender-body model (its
      destabilising pitch and yaw moments), added on top.
    Rates are non-dimensional (p b/2V, q c/2V, r b/2V)."""
    kw = dict(xyz_ref=list(airplane.xyz_ref), s_ref=airplane.s_ref, c_ref=airplane.c_ref, b_ref=airplane.b_ref)
    wings_only = asb.Airplane(wings=airplane.wings, **kw)
    wings_only.cem_context = getattr(airplane, 'cem_context', None)
    d = asb.VortexLatticeMethod(wings_only, _op(airplane, V, alpha),
                                spanwise_resolution=8, chordwise_resolution=6).run_with_stability_derivatives()
    out = {k: _f(d[k]) for k in ["CLa", "Cma", "CYb", "Clb", "Cnb", "CYp", "Clp", "Cnp",
                                 "CYr", "Clr", "Cnr", "Cmq"]}
    body = asb.Airplane(fuselages=airplane.fuselages, **kw)
    body.cem_context = getattr(airplane, 'cem_context', None)
    r = asb.AeroBuildup(body, _op(airplane, V, np.array([alpha - 2, alpha + 2, alpha, alpha]),
                                beta=np.array([0.0, 0.0, 2.0, -2.0]))).run()
    g = {k: np.asarray(r[k], dtype=float) for k in ["CL", "Cm", "CY", "Cl", "Cn"]}
    four = np.radians(4.0)
    out["CLa"] += (g["CL"][1] - g["CL"][0]) / four
    out["Cma"] += (g["Cm"][1] - g["Cm"][0]) / four
    for k in ["CY", "Cl", "Cn"]:
        out[k + "b"] += (g[k][2] - g[k][3]) / four
    out["x_np"] = airplane.xyz_ref[0] - out["Cma"] / out["CLa"] * airplane.c_ref
    return out


def neutral_point(gl: Glider, V: float, alpha: float = 4.0, tail_incidence_deg: float = 0.0) -> float:
    ap = gl.airplane(xyz_ref=[gl.wing_ref()["x_ac"], 0, 0], tail_incidence_deg=tail_incidence_deg)
    return stability_derivatives(ap, V, alpha)["x_np"]


def balance_with_wing_slide(gl: Glider, V: float, sm_target: float, iters: int = 4) -> float:
    """Slide the wing along its saddle so the glider balances at the target
    static margin with as little ballast as possible (like a rubber-band wing
    mount on a real model). Then slide the payload, then add nose ballast.

    Moving the wing by dx moves the neutral point by ~k*dx (k < 1 because the
    tail stays put) and the CG by (m_wing/M)*dx; k is updated by secant steps."""
    lo, hi = gl.wing_saddle()
    gl.wing_x = gl.wing_position()[0]
    k = 0.8
    prev = None
    x_np = neutral_point(gl, V)
    for _ in range(iters):
        mac = gl.wing_ref()["mac"]
        target = x_np - sm_target * mac
        gl.balance(target)
        ms_nb = gl.mass_summary(include_ballast=False)
        cg_nb, M = ms_nb["cg"][0], ms_nb["mass"]
        mw = ms_nb["by_part"].get("wing", 0.0)
        gap = cg_nb - target          # >0 tail-heavy (needs ballast), <0 nose-heavy
        if abs(gap) < 0.002 * mac + 1e-4:
            break
        dx = gap / max(k - mw / M, 0.2)
        new_x = min(max(gl.wing_x + dx, lo), hi)
        if abs(new_x - gl.wing_x) < 5e-4:
            break                      # pinned at the end of the saddle
        prev = (gl.wing_x, x_np)
        gl.wing_x = new_x
        x_np = neutral_point(gl, V)
        if abs(gl.wing_x - prev[0]) > 1e-6:
            k = min(max((x_np - prev[1]) / (gl.wing_x - prev[0]), 0.3), 1.2)
    gl.balance(x_np - sm_target * gl.wing_ref()["mac"])
    return x_np


def trim(gl: Glider, cg, V, W, rho, S, objective, stall_margin, below_deg=1.0):
    """Pick the best angle of attack for the objective, then solve the tail
    incidence (decalage) so the pitching moment about the CG is zero there.
    Returns alpha*, tail incidence, CLmax, trimmed speed, stability-derivative dict."""
    delta, alpha_star, CLmax = 0.0, 4.0, float("nan")
    for _ in range(12):
        r = _aero(gl.airplane(cg, delta), V, ALPHAS)
        CL, CD = np.asarray(r["CL"]), np.asarray(r["CD"])
        i_max = int(np.argmax(CL))
        CLmax = float(CL[i_max]) * gl.cfg.get('cem_context', {}).get('cl_max_factor', 1.)
        ok = (np.arange(len(ALPHAS)) <= i_max) & (CL <= stall_margin * CLmax) & (CL > 0.05)
        if gl.cfg.get('cem_context'):
            # Speed-to-fly is constrained by the same station Reynolds envelope
            # as admission; a tailwind must not optimize into untrusted low Re.
            min_chord = min(s.chord for surface in [gl.wing_surface(), *gl.tail_surfaces()]
                            for s in surface.sections)
            min_speed = 30000 * gl.cfg['mission']['dynamic_viscosity_pa_s'] / (rho * min_chord)
            equilibrium_speeds = np.sqrt(2 * W / (rho * S * np.maximum(CL, .001)))
            ok &= (equilibrium_speeds >= min_speed * 1.001) & (equilibrium_speeds <= gl.cfg['limits']['max_glide_speed_m_s'])
        if not ok.any():
            if gl.cfg.get('cem_context'):
                raise ValueError('No trimmed polar samples satisfy stall and operating-envelope limits')
            break
        metric = CL / CD if objective == "distance" else np.clip(CL, 0, None) ** 1.5 / CD
        mission = gl.cfg['mission']
        if mission.get('wind_m_s', 0.) or mission.get('uplift_m_s', 0.):
            speeds = np.sqrt(2 * W / (rho * S * np.maximum(CL, .001)))
            gamma = np.arctan2(CD, np.maximum(CL, .001))
            sinks = speeds * np.sin(gamma)
            ground = speeds * np.cos(gamma) + mission.get('wind_m_s', 0.)
            descent = sinks - mission.get('uplift_m_s', 0.)
            # Sustained conditions are reported separately; never optimize infinity.
            metric = (ground if objective == 'distance' else np.ones_like(ground)) / np.maximum(descent, .001)
            if objective == 'distance':
                ok &= ground > 0
        alpha_star = float(ALPHAS[int(np.argmax(np.where(ok, metric, -np.inf)))])
        for _ in range(4):           # Newton steps: Cm(alpha*, delta) = 0
            c1 = _f(_aero(gl.airplane(cg, delta), V, alpha_star)["Cm"])
            if abs(c1) < 1e-3:
                break
            c2 = _f(_aero(gl.airplane(cg, delta - 1.0), V, alpha_star)["Cm"])
            if abs(c1 - c2) < 1e-9:
                break
            delta = float(np.clip(delta - c1 / (c1 - c2), -15.0, 15.0))
        CL_star = _f(_aero(gl.airplane(cg, delta), V, alpha_star)["CL"])
        if CL_star > 0.05:
            next_V = math.sqrt(2 * W / (rho * S * CL_star))
            if abs(next_V - V) / V < 1e-4:
                break
            V = next_V
    ap = gl.airplane(cg, delta)
    d = local_aero(ap, V, alpha_star, below_deg=below_deg)
    next_V = math.sqrt(2 * W / (rho * S * max(d["CL"], 1e-3)))
    if abs(next_V - V) / V > 1e-3:
        raise ValueError('Coupled speed/trim calculation did not converge')
    # Re-evaluate the stall reference at the final incidence and Reynolds condition.
    CLmax = float(np.max(_aero(ap, V, ALPHAS)['CL'])) * gl.cfg.get('cem_context', {}).get('cl_max_factor', 1.)
    d.update(stability_derivatives(ap, V, alpha_star))
    return alpha_star, delta, CLmax, V, d


# ---------------------------------------------------------------------- #
# Main evaluation
# ---------------------------------------------------------------------- #
def fixed_geometry_trim(gl, cg, V, W, rho, S, below_deg=1.0):
    """Find equilibrium speed/alpha without changing any aircraft controls.

    This is a screening equilibrium (L = W, as in the existing trim model),
    not a speed-to-fly optimization or permission to adjust tail incidence.
    """
    from scipy.optimize import least_squares
    ap = gl.airplane(cg)
    def residual(x):
        alpha, speed = x
        aero = _aero(ap, speed, alpha)
        return [_f(aero['CL'])*.5*rho*speed**2*S/W-1., _f(aero['Cm'])]
    fit = least_squares(residual, [4., min(max(V, .51), 29.9)],
                        bounds=([ALPHAS[0], .5], [ALPHAS[-1], 30.]),
                        xtol=1e-9, ftol=1e-9, gtol=1e-9, max_nfev=80)
    if not fit.success or np.max(np.abs(residual(fit.x))) > 1e-4:
        raise ValueError('Fixed-geometry equilibrium did not converge')
    alpha, speed = map(float, fit.x)
    d = local_aero(ap, speed, alpha, below_deg)
    d.update(stability_derivatives(ap, speed, alpha))
    clmax = float(np.max(_aero(ap, speed, ALPHAS)['CL'])) * gl.cfg.get('cem_context', {}).get('cl_max_factor', 1.)
    return alpha, gl.tail_incidence_deg, clmax, speed, d


def evaluate(gl: Glider, *, fixed_geometry=False) -> dict:
    cfg = gl.cfg
    lim, mis = cfg["limits"], cfg["mission"]
    rho, g = mis["air_density_kg_m3"], mis["gravity_m_s2"]
    res = {"id": gl.id, "wing": gl.wing.id, "fuselage": gl.fuse.id, "tail": gl.tail.id,
           "wing_style": gl.wing.style, "fuselage_style": gl.fuse.style, "tail_kind": gl.tail.kind,
           "valid": False, "fail_reasons": ""}
    try:
        fails, info = geometry_checks(gl, lim, mis)
        res.update(info)
        S, b, mac = info["wing_area_m2"], info["wing_span_m"], info["wing_mac_m"]

        m0 = gl.mass_summary(include_ballast=False)["mass"]
        res["mass_no_ballast_kg"] = m0
        if m0 > lim["max_total_mass_kg"]:
            fails.append("too heavy")
        if fails:                       # don't spend aero time on broken geometry
            res["fail_reasons"] = "; ".join(fails)
            return res

        # ---- 1+2. balance: slide wing, slide payload, then nose ballast ----
        V = math.sqrt(2 * m0 * g / (rho * S * 0.6))
        if not fixed_geometry:
            balance_with_wing_slide(gl, V, lim["static_margin_target"])
        fails, info = geometry_checks(gl, lim, mis)       # re-check: the wing may have moved
        res.update(info)
        S, b, mac = info["wing_area_m2"], info["wing_span_m"], info["wing_mac_m"]

        # ---- 3+4. trim, then re-balance once with the neutral point at the trim condition ----
        for attempt in range(1 if fixed_geometry else 2):
            ms = gl.mass_summary()
            m, cg = ms["mass"], ms["cg"]
            if fixed_geometry:
                alpha_star, delta, CLmax, V, d = fixed_geometry_trim(
                    gl, cg, V, m * g, rho, S, lim['pitch_stable_below_trim_deg'])
                break
            alpha_star, delta, CLmax, V, d = trim(gl, cg, V, m * g, rho, S, mis["objective"],
                                                  lim["stall_margin"], lim["pitch_stable_below_trim_deg"])
            SM = (_f(d["x_np"]) - cg[0]) / mac
            if attempt == 1 or abs(SM - lim["static_margin_target"]) < 0.015:
                break
            gl.balance(_f(d["x_np"]) - lim["static_margin_target"] * mac)
        gl.tail_incidence_deg = delta
        W = m * g
        res["CLmax_est"] = float(CLmax)
        res["ballast_kg"] = gl.ballast_kg
        res["payload_x_m"] = gl.payload_position() if mis["payload_mass_kg"] > 0 else float("nan")
        res["wing_le_x_m"] = gl.wing_position()[0]
        if gl.ballast_kg / m > lim["max_ballast_fraction"]:
            fails.append("needs too much nose ballast (tail-heavy layout)")

        CL, CD, Cm = _f(d["CL"]), _f(d["CD"]), _f(d["Cm"])
        der = {k: _f(d[k]) for k in ["CLa", "Cma", "x_np", "CYb", "Clb", "Cnb", "CYp", "Clp", "Cnp",
                                     "CYr", "Clr", "Cnr", "Cmq"]}
        V = math.sqrt(2 * W / (rho * S * max(CL, 1e-3)))
        SM = (der["x_np"] - cg[0]) / mac
        LD = CL / CD
        gamma = math.atan(1 / LD) if LD > 0 else math.pi / 2
        sink = V * math.sin(gamma)
        Re = rho * V * mac / mis.get('dynamic_viscosity_pa_s', 1.81e-5)
        lat = lateral_modes(der, V, rho, S, b, m, ms["Ixx"], ms["Izz"], gamma, g)
        spiral_E = der["Clb"] * der["Cnr"] - der["Cnb"] * der["Clr"]

        H = mis["launch_height_m"]
        res.update({
            "mass_kg": m, "cg_x_m": cg[0], "np_x_m": der["x_np"], "static_margin": SM,
            "tail_incidence_deg": delta, "decalage_deg": gl.wing.incidence_deg - delta,
            "alpha_trim_deg": alpha_star, "CL_trim": CL, "CD_trim": CD, "Cm_trim": Cm,
            "L_over_D": LD, "glide_speed_m_s": V, "sink_rate_m_s": sink,
            "glide_angle_deg": math.degrees(gamma), "reynolds_mac": Re,
            "wing_loading_g_dm2": m * 1000 / (S * 100),
            "glide_distance_m": H * LD, "time_aloft_s": H / sink if sink > 0 else float("inf"),
            "landing_energy_J": 0.5 * m * V ** 2,
            "Cma": der["Cma"], "Clb": der["Clb"], "Cnb": der["Cnb"], "spiral_E": spiral_E,
            "dutch_roll_zeta": lat["dutch_roll_zeta"], "dutch_roll_real": lat["dutch_roll_real"],
            "roll_real": lat["roll_real"], "spiral_real": lat["spiral_real"],
            "spiral_time_to_double_s": lat["spiral_time_to_double_s"],
            "Ixx": ms["Ixx"], "Iyy": ms["Iyy"], "Izz": ms["Izz"],
            "rawdon_B": (info["tail_arm_m"] * gl.wing.dihedral_deg) / (b * CL) if CL > 0 else 0.0,
        })

        # ---- 5. validity checks ----
        lo, hi = lim["tail_incidence_range_deg"]
        if not lo <= delta <= hi:
            fails.append("cannot trim within tail incidence range")
        if abs(Cm) > 0.01:
            fails.append("trim solver did not converge")
        if CL < lim["min_trim_CL"]:
            fails.append("trimmed lift coefficient too low")
        if CL > lim["stall_margin"] * CLmax:
            fails.append("trims too close to stall")
        if der["Cma"] >= 0 or SM < lim["static_margin_min"]:
            fails.append("pitch unstable / static margin too small")
        if not d["Cm_monotonic"]:
            fails.append("loses pitch stability just below trim angle (low-Re airfoil kink)")
        if SM > lim["static_margin_max"]:
            fails.append("static margin too large (nose-heavy)")
        if der["Clb"] >= 0:
            fails.append("no dihedral effect (Cl_beta >= 0)")
        if der["Cnb"] <= 0:
            fails.append("no weathercock stability (Cn_beta <= 0)")
        if lat["roll_real"] >= 0:
            fails.append("roll mode unstable")
        if lat["dutch_roll_real"] >= 0 or lat["dutch_roll_zeta"] < lim["dutch_roll_min_damping"]:
            fails.append("Dutch roll unstable / poorly damped (too much dihedral for the fin)")
        if lat["spiral_time_to_double_s"] < lim["spiral_min_time_to_double_s"]:
            fails.append("spiral divergence too fast (too little dihedral for the fin)")
        if res["wing_loading_g_dm2"] > lim["max_wing_loading_g_dm2"]:
            fails.append("wing loading too high")
        if m > lim["max_total_mass_kg"]:
            fails.append("too heavy")
        if Re < lim["min_reynolds"]:
            fails.append("Reynolds number too low")
        if V > lim["max_glide_speed_m_s"]:
            fails.append("glide/landing speed too high")

        res["valid"] = not fails
        if fixed_geometry:
            res['fixed_geometry'] = True
        res["fail_reasons"] = "; ".join(fails)
        res["score"] = (LD if mis["objective"] == "distance" else res["time_aloft_s"]) if not fails else float("nan")
    except Exception as e:  # never let one bad design kill the batch
        res["fail_reasons"] = f"analysis error: {e.__class__.__name__}: {e}"
        res["traceback"] = traceback.format_exc(limit=3)
    return res
