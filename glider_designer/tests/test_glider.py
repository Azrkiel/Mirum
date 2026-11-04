"""
Checks of the geometry, mass and stability maths against hand calculations.
Run:  python -m pytest -q
"""
import copy
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG
from components import WingParams, FuselageParams, TailParams
from assembly import Glider
from analysis import classify_lateral_eigs, stability_derivatives, evaluate


def wing(**kw):
    p = dict(id="Wt", style="tapered", span=1.0, root_chord=0.2, taper=1.0, sweep_qc_deg=0.0,
             dihedral_deg=0.0, washout_deg=0.0, incidence_deg=0.0, naca="2408")
    p.update(kw)
    return WingParams(**p)


def fuse(**kw):
    p = dict(id="Ft", style="pod_boom", length=0.75, max_diameter=0.07, nose_frac=0.08,
             pod_frac=0.38, boom_radius=0.007, wing_station_in_pod=0.5)
    p.update(kw)
    return FuselageParams(**p)


def tail(**kw):
    p = dict(id="Tt", kind="conventional", airfoil="0008", h_span=0.30, h_root_chord=0.08, h_taper=1.0,
             v_height=0.12, v_root_chord=0.08, v_taper=0.7, v_sweep_le_deg=20.0)
    p.update(kw)
    return TailParams(**p)


def cfg_no_payload():
    c = copy.deepcopy(CONFIG)
    c["mission"]["payload_mass_kg"] = 0.0
    return c


# ---------------------------------------------------------------- geometry
def test_rectangular_wing_area_and_mac():
    s = wing().surface()
    w = s.to_asb()
    assert w.area() == pytest.approx(0.2, rel=1e-6)
    assert w.mean_aerodynamic_chord() == pytest.approx(0.2, rel=1e-6)
    assert s.true_area() == pytest.approx(0.2, rel=1e-6)


def test_tapered_wing_mac_formula():
    cr, lam = 0.2, 0.5
    w = wing(taper=lam).surface().to_asb()
    mac_hand = 2 / 3 * cr * (1 + lam + lam ** 2) / (1 + lam)
    assert w.mean_aerodynamic_chord() == pytest.approx(mac_hand, rel=1e-4)
    assert w.area() == pytest.approx(1.0 * cr * (1 + lam) / 2, rel=1e-6)


def test_dihedral_sets_tip_height():
    s = wing(dihedral_deg=8.0).surface()
    assert s.sections[-1].z == pytest.approx(0.5 * math.tan(math.radians(8.0)), rel=1e-9)
    s = wing(style="polyhedral", dihedral_deg=8.0).surface()
    assert s.sections[1].z == 0.0                                  # flat centre panel
    assert s.sections[-1].z == pytest.approx(0.5 * math.tan(math.radians(8.0)), rel=1e-9)


def test_tail_ends_flush_with_fuselage():
    gl = Glider(wing(), fuse(), tail(), cfg_no_payload())
    for surf in gl.tail_surfaces():
        root = surf.sections[0]
        assert root.x + root.chord == pytest.approx(gl.fuse.length, abs=1e-9)


# ---------------------------------------------------------------- mass / balance
def test_rectangular_wing_mass_centroid():
    c = cfg_no_payload()
    gl = Glider(wing(), fuse(), tail(), c)
    items = [i for i in gl.mass_items() if i.name == "wing"]
    m = sum(i.mass for i in items)
    x = sum(i.mass * i.x for i in items) / m
    x_le = gl.wing_position()[0]
    assert m == pytest.approx(0.2 * c["materials"]["wing_areal_density_kg_m2"], rel=1e-6)
    assert x == pytest.approx(x_le + c["materials"]["wing_cg_chord_fraction"] * 0.2, abs=1e-9)
    assert abs(sum(i.mass * i.y for i in items)) < 1e-12        # left/right symmetric


def test_ballast_puts_cg_on_target():
    gl = Glider(wing(), fuse(), tail(), cfg_no_payload())
    x0 = gl.mass_summary()["cg"][0]
    target = x0 - 0.02                                             # ask for a more forward CG
    gl.ballast_kg = gl.solve_ballast(target)
    assert gl.ballast_kg > 0
    assert gl.mass_summary()["cg"][0] == pytest.approx(target, abs=1e-9)


def test_balance_slides_payload_before_adding_ballast():
    gl = Glider(wing(), fuse(), tail(), copy.deepcopy(CONFIG))
    bay = gl.payload_bay()
    assert bay is not None
    gl.payload_x = 0.5 * (bay[0] + bay[1])
    target = gl.mass_summary()["cg"][0] - 0.003                    # small shift: payload can do it
    gl.balance(target)
    assert gl.ballast_kg == 0.0
    assert gl.mass_summary()["cg"][0] == pytest.approx(target, abs=1e-6)


# ---------------------------------------------------------------- tail volume
def test_tail_volume_hand_calculation():
    gl = Glider(wing(), fuse(), tail(), cfg_no_payload())
    tv = gl.tail_volumes()
    # rectangular, unswept surfaces: aerodynamic centre at the quarter chord
    x_ac_w = gl.wing_position()[0] + 0.25 * 0.2
    x_ac_h = gl.fuse.length - 0.08 + 0.25 * 0.08
    Sh, Sw, c = 0.30 * 0.08, 0.2, 0.2
    assert tv["lh"] == pytest.approx(x_ac_h - x_ac_w, abs=1e-6)
    assert tv["Vh"] == pytest.approx(Sh * (x_ac_h - x_ac_w) / (Sw * c), rel=1e-4)


def test_v_tail_projected_areas():
    t = tail(kind="V", vt_panel_span=0.2, vt_root_chord=0.08, vt_taper=1.0, vt_dihedral_deg=40.0)
    gl = Glider(wing(), fuse(), t, cfg_no_payload())
    tv = gl.tail_volumes()
    S = 2 * 0.2 * 0.08
    g = math.radians(40)
    assert tv["Sh"] == pytest.approx(S * math.cos(g) ** 2, rel=1e-4)
    assert tv["Sv"] == pytest.approx(S * math.sin(g) ** 2, rel=1e-4)


# ---------------------------------------------------------------- dynamics
def test_lateral_eigenvalue_classification():
    out = classify_lateral_eigs([-10.0, -0.05, complex(-0.5, 2.0), complex(-0.5, -2.0)])
    assert out["roll_real"] == -10.0
    assert out["spiral_real"] == -0.05
    assert out["dutch_roll_zeta"] == pytest.approx(0.5 / math.hypot(0.5, 2.0))
    out = classify_lateral_eigs([-8.0, 0.0693, complex(-0.3, 1.5), complex(-0.3, -1.5)])
    assert out["spiral_time_to_double_s"] == pytest.approx(10.0, rel=1e-2)


def test_neutral_point_does_not_depend_on_reference_point():
    gl = Glider(wing(dihedral_deg=6.0), fuse(), tail(), cfg_no_payload())
    a = stability_derivatives(gl.airplane(xyz_ref=[0.15, 0, 0]), 7.0, 4.0)["x_np"]
    b = stability_derivatives(gl.airplane(xyz_ref=[0.30, 0, 0]), 7.0, 4.0)["x_np"]
    assert a == pytest.approx(b, abs=1e-3)


# ---------------------------------------------------------------- end to end
def good_glider(c):
    return Glider(wing(span=1.1, root_chord=0.15, taper=0.6, dihedral_deg=7.0, washout_deg=1.5,
                       incidence_deg=2.0, naca="2408"),
                  fuse(), tail(h_span=0.32, h_root_chord=0.08, v_height=0.12, v_root_chord=0.09), c)


def test_reasonable_glider_passes():
    r = evaluate(good_glider(copy.deepcopy(CONFIG)))
    assert r["valid"], r["fail_reasons"]
    assert 5 < r["L_over_D"] < 25                                  # plausible for a small foam glider
    assert r["static_margin"] == pytest.approx(CONFIG["limits"]["static_margin_target"], abs=0.03)
    assert abs(r["Cm_trim"]) < 0.01


def test_tail_heavy_glider_is_rejected():
    c = copy.deepcopy(CONFIG)
    c["limits"]["static_margin_target"] = -0.10                   # balance it behind the neutral point
    r = evaluate(good_glider(c))
    assert not r["valid"]
    assert "pitch unstable" in r["fail_reasons"]
