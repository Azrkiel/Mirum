"""Public builder examples packaged with the backend for backend-only images."""
from .regen_spec import ASSUMPTIONS

EXAMPLES = {
    'small': dict(
        family='regen_chamber', units='mm', assumptions=ASSUMPTIONS,
        chamber_radius_mm=30.0, chamber_length_mm=40.0, convergent_length_mm=40.0,
        throat_radius_mm=16.0, divergent_length_mm=60.0, exit_radius_mm=32.0,
        wall_thickness_mm=6.0, channel_count=24, channel_radius_mm=1.0,
        channel_twist_rad_per_mm=0.01, manifold_radius_mm=1.2,
        end_margin_mm=8.0, voxel_size_mm=0.4),
    'reference': dict(
        family='regen_chamber', units='mm', assumptions=ASSUMPTIONS,
        chamber_radius_mm=35.0, chamber_length_mm=40.0, convergent_length_mm=50.0,
        throat_radius_mm=20.0, divergent_length_mm=80.0, exit_radius_mm=45.0,
        wall_thickness_mm=7.0, channel_count=32, channel_radius_mm=1.0,
        channel_twist_rad_per_mm=0.012, manifold_radius_mm=1.5,
        end_margin_mm=8.0, voxel_size_mm=0.4),
}
