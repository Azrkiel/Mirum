"""Track A / A2 — the six deterministic OML builders.

Every builder is keyword-only, pure, true-OML, and ends in _validate_solid
(is-solid + BRepCheck + vol>0 + bbox within envelope). Cadquery-gated.

Run: python backend/tests/test_cad_aero_builders.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import cadquery as cq  # noqa: F401

    _CQ_AVAILABLE = True
except ModuleNotFoundError:
    _CQ_AVAILABLE = False

_SKIP_MSG = "cadquery not importable in this environment."

# Canonical NACA-2412-class wing parameters (shared across builders).
SPAN = 5.3642
ROOT = 1.7881
TIP = 0.8940
FRONT = 0.25
REAR = 0.65
TC = 0.12


def _airfoil():
    import cad_aero

    return cad_aero.naca4_points("2412", chord_m=1.0)  # normalised (x/c, z/c)


def _vol(wp):
    return wp.val().Volume()


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestMakeSpar(unittest.TestCase):
    def test_front_spar_valid(self):
        import cad_aero

        wp = cad_aero.make_spar(
            airfoil_points=_airfoil(), span_m=SPAN, root_chord_m=ROOT,
            tip_chord_m=TIP, x_frac=FRONT, web_thickness_m=0.0008,
            twist_tip_deg=-3.0, dihedral_deg=2.0, n_stations=7,
        )
        self.assertGreater(_vol(wp), 0.0)
        bb = wp.val().BoundingBox()
        # spans roughly the full semi-span in Y
        self.assertAlmostEqual(bb.ylen, SPAN, delta=0.05)


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestMakeRib(unittest.TestCase):
    def test_oml_rib_valid(self):
        import cad_aero

        wp = cad_aero.make_rib(
            airfoil_points=_airfoil(), chord_m=ROOT, y_station_m=0.0,
            thickness_m=0.003, front_spar_x_frac=FRONT, rear_spar_x_frac=REAR,
            n_lightening_holes=3,
        )
        self.assertGreater(_vol(wp), 0.0)

    def test_lightening_holes_remove_mass(self):
        import cad_aero

        kw = dict(
            airfoil_points=_airfoil(), chord_m=ROOT, y_station_m=0.0,
            thickness_m=0.003, front_spar_x_frac=FRONT, rear_spar_x_frac=REAR,
        )
        solid = cad_aero.make_rib(n_lightening_holes=0, **kw)
        holed = cad_aero.make_rib(n_lightening_holes=3, **kw)
        self.assertLess(_vol(holed), _vol(solid))

    def test_high_hole_count_auto_clamps_and_stays_valid(self):
        # Hole #5: too many holes must auto-space + clamp, never produce an
        # invalid / zero-volume rib.
        import cad_aero

        wp = cad_aero.make_rib(
            airfoil_points=_airfoil(), chord_m=TIP, y_station_m=SPAN,
            thickness_m=0.003, front_spar_x_frac=FRONT, rear_spar_x_frac=REAR,
            n_lightening_holes=25,
        )
        self.assertGreater(_vol(wp), 0.0)


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestMakeStringer(unittest.TestCase):
    def test_l_section_valid(self):
        import cad_aero

        wp = cad_aero.make_stringer(
            span_m=SPAN, x_frac=FRONT, z_frac=1.0, root_chord_m=ROOT,
            tip_chord_m=TIP, section="L", leg_mm=12.0, thickness_mm=1.5,
        )
        self.assertGreater(_vol(wp), 0.0)


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestMakeWingSkinPanel(unittest.TestCase):
    def test_upper_and_lower_valid(self):
        import cad_aero

        for upper in (True, False):
            wp = cad_aero.make_wing_skin_panel(
                airfoil_points=_airfoil(), span_m=SPAN, root_chord_m=ROOT,
                tip_chord_m=TIP, upper=upper, thickness_root_m=0.0012,
                thickness_tip_m=0.0012, front_spar_x_frac=FRONT,
                rear_spar_x_frac=REAR, n_stations=11,
            )
            self.assertGreater(_vol(wp), 0.0, f"upper={upper}")


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestMakeRootFitting(unittest.TestCase):
    def test_valid_with_bolts(self):
        import cad_aero

        wp = cad_aero.make_root_fitting(
            root_chord_m=ROOT, thickness_to_chord=TC, front_spar_x_frac=FRONT,
            rear_spar_x_frac=REAR, fitting_thickness_m=0.0024,
            bolt_pattern=[(0.30, 0.0), (0.60, 0.0)],
        )
        self.assertGreater(_vol(wp), 0.0)


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestMakeControlSurface(unittest.TestCase):
    def test_flap_solid_only_and_valid(self):
        # Hole #3: returns ONLY the flap solid (aft of hinge), not the parent.
        import cad_aero

        wp = cad_aero.make_control_surface(
            parent_airfoil_points=_airfoil(), chord_frac=0.25, span_m=2.0,
            root_chord_m=ROOT, tip_chord_m=TIP, hinge_x_frac=0.75,
            deflection_deg=0.0,
        )
        self.assertGreater(_vol(wp), 0.0)
        bb = wp.val().BoundingBox()
        # Flap is the aft partial-chord region: its planform X-extent is narrower
        # than the full root chord (with taper, the hinge line sweeps in X, so the
        # extent exceeds the *local* flap chord — that is expected, not a bug).
        self.assertLess(bb.xlen, ROOT)
        # And the trailing edge is present at ~root chord (it really is the aft part).
        self.assertAlmostEqual(bb.xmax, ROOT, delta=0.05 * ROOT)

    def test_deflection_changes_geometry_but_stays_valid(self):
        import cad_aero

        kw = dict(
            parent_airfoil_points=_airfoil(), chord_frac=0.25, span_m=2.0,
            root_chord_m=ROOT, tip_chord_m=TIP, hinge_x_frac=0.75,
        )
        flat = cad_aero.make_control_surface(deflection_deg=0.0, **kw)
        defl = cad_aero.make_control_surface(deflection_deg=20.0, **kw)
        self.assertGreater(_vol(defl), 0.0)
        # deflection drops the TE -> larger Z extent than the undeflected flap
        self.assertGreater(defl.val().BoundingBox().zlen, flat.val().BoundingBox().zlen)


@unittest.skipUnless(_CQ_AVAILABLE, _SKIP_MSG)
class TestParametricFuzz(unittest.TestCase):
    """Stage-4 extreme-value fuzz: every cell must be a valid solid OR raise a
    clean CadAeroError — never an invalid B-rep, never an uncaught OCC crash."""

    def _ok_or_clean(self, fn):
        import cad_aero

        try:
            wp = fn()
        except cad_aero.CadAeroError:
            return  # clean refusal is acceptable
        shape = wp.val()
        self.assertTrue(shape is not None and shape.isValid() and shape.Volume() > 0)

    def test_spar_scale_and_attitude_extremes(self):
        import cad_aero

        af = _airfoil()
        for span in (0.3, 12.0):
            for root, tip in ((0.05, 0.02), (4.0, 4.0)):
                for tw in (-30.0, 0.0, 30.0):
                    for dih in (-15.0, 0.0, 15.0):
                        self._ok_or_clean(
                            lambda s=span, r=root, t=tip, w=tw, d=dih: cad_aero.make_spar(
                                airfoil_points=af, span_m=s, root_chord_m=r,
                                tip_chord_m=t, x_frac=0.30, web_thickness_m=0.0008,
                                twist_tip_deg=w, dihedral_deg=d, n_stations=7,
                            )
                        )

    def test_rib_hole_count_sweep(self):
        import cad_aero

        af = _airfoil()
        for n in (0, 1, 3, 8, 25, 50):
            for chord in (0.10, 0.5, 2.0):
                self._ok_or_clean(
                    lambda nn=n, c=chord: cad_aero.make_rib(
                        airfoil_points=af, chord_m=c, y_station_m=0.0,
                        thickness_m=0.003, front_spar_x_frac=0.25,
                        rear_spar_x_frac=0.65, n_lightening_holes=nn,
                    )
                )

    def test_control_surface_deflection_sweep(self):
        import cad_aero

        af = _airfoil()
        for defl in (-40.0, -20.0, 0.0, 20.0, 40.0):
            self._ok_or_clean(
                lambda d=defl: cad_aero.make_control_surface(
                    parent_airfoil_points=af, chord_frac=0.25, span_m=2.0,
                    root_chord_m=ROOT, tip_chord_m=TIP, hinge_x_frac=0.75,
                    deflection_deg=d,
                )
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
