"""Run the propulsion primitives inside the production cadenv (cadquery 2.7)."""
import os
import sys
# Honor an explicit override; otherwise default to /app (production layout).
_BACKEND = os.environ.get("MIRUM_BACKEND_DIR", "/app")
sys.path.insert(0, _BACKEND)
import math
from cad_utils import (
    make_combustion_chamber, make_conical_nozzle, make_helical_cooling_channels,
    make_injector_plate, make_rao_bell_nozzle,
)
import cadquery as cq
print("cadquery", cq.__version__)


def m(label, wp):
    bb = wp.val().BoundingBox()
    print(f"[OK] {label}: vol={wp.val().Volume():.1f} valid={wp.val().isValid()} "
          f"z=[{bb.zmin:.2f},{bb.zmax:.2f}] xmax={bb.xmax:.2f}")


m("conical r_t=25 r_e=75",
  make_conical_nozzle(throat_radius=25, exit_radius=75, half_angle_deg=15,
                      converging_angle_deg=30, chamber_radius=50))
m("rao_bell r_t=20 eps=16",
  make_rao_bell_nozzle(throat_radius=20, expansion_ratio=16,
                       length_fraction=0.8, chamber_radius=60))
m("chamber+frustum r=40 L*=1.0",
  make_combustion_chamber(radius=40, l_star=1.0, throat_radius=20))
nz = make_rao_bell_nozzle(throat_radius=25, expansion_ratio=12,
                          length_fraction=0.8, chamber_radius=60)
m("regen cooled bell 60 ch helical",
  make_helical_cooling_channels(nozzle_solid=nz, nozzle_outer_radius=29.0,
                                channel_count=60, channel_width=2.0,
                                channel_depth=3.0, helix_angle_deg=15.0,
                                axial_length=80.0))
m("injector plate 24 doublet pairs",
  make_injector_plate(plate_radius=45, plate_thickness=6, orifice_count=24,
                      orifice_diameter=1.5, pattern="impinging_doublet",
                      distribution_radius=30, impingement_angle_deg=30))
