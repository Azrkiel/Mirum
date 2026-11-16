"""Measure the exported STEP, independently checking absolute bounds and cuts."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import cadquery as cq

from .spec import DragsterSpec, specification_digest

BOUNDS_TOLERANCE_MM = 0.01
VOID_TOLERANCE_MM3 = 0.001


def verify_step(path: Path, spec: DragsterSpec) -> dict:
    # Hash the file being inspected; CLI keeps this private until publication.
    step_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        imported = cq.importers.importStep(str(path))
        shape = imported.val()
        solids = shape.Solids()
    except Exception as exc:
        raise ValueError(f'STEP reimport failed: {exc}') from exc
    checks = {}

    def check(name, passed, **evidence):
        checks[name] = {'passed': bool(passed), **evidence}

    check('single_valid_solid', len(solids) == 1 and shape.isValid(), solid_count=len(solids))
    if not checks['single_valid_solid']['passed']:
        raise ValueError('STEP checks failed: single_valid_solid')
    body = solids[0]
    b = body.BoundingBox()
    bounds = {key: getattr(b, key) for key in ('xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax')}
    L, W, H, N, g = (spec.length_mm, spec.width_mm, spec.height_mm,
                      spec.nose_height_mm, spec.ground_clearance_mm)
    expected_bounds = dict(zip(bounds, (0, L, -W/2, W/2, g, g+H)))
    check('absolute_bounds', all(math.isfinite(bounds[k]) and abs(bounds[k]-v) <= BOUNDS_TOLERANCE_MM
                               for k, v in expected_bounds.items()),
          expected_mm=expected_bounds, tolerance_mm=BOUNDS_TOLERANCE_MM)
    ra, rp = spec.axle_bore_diameter_mm/2, spec.pocket_diameter_mm/2
    D, zp, za = spec.pocket_depth_mm, spec.pocket_center_z_mm, spec.axle_z_mm
    expected_volume = W * L * (N + 3*H)/4 - 2*math.pi*ra**2*W - math.pi*rp**2*D
    volume = body.Volume()
    tolerance = max(0.1, expected_volume * 1e-6)
    check('volume', math.isfinite(volume) and volume > 0 and abs(volume-expected_volume) <= tolerance,
          expected_mm3=expected_volume, tolerance_mm3=tolerance)
    probes = [
        ('pocket_void', rp, D, (L-D, 0, zp), (1, 0, 0)),
        ('front_axle_void', ra, W, (spec.front_axle_x_mm, -W/2, za), (0, 1, 0)),
        ('rear_axle_void', ra, W, (spec.rear_axle_x_mm, -W/2, za), (0, 1, 0)),
    ]
    for name, radius, depth, origin, axis in probes:
        probe = cq.Solid.makeCylinder(radius, depth, cq.Vector(*origin), cq.Vector(*axis))
        residual = body.intersect(probe).Volume()
        check(name, math.isfinite(residual) and abs(residual) <= VOID_TOLERANCE_MM3,
              residual_body_mm3=residual, tolerance_mm3=VOID_TOLERANCE_MM3,
              origin_mm=list(origin), axis=list(axis), radius_mm=radius, length_mm=depth)
    # Revalidate independently of how the spec instance was obtained.
    DragsterSpec.model_validate_json(spec.model_dump_json())
    check('analytic_wall_and_separation', True,
          scope='Analytic fixed-wedge parameter constraints; not whole-body thickness certification')
    failures = [name for name, result in checks.items() if not result['passed']]
    if failures:
        raise ValueError('STEP checks failed: ' + ', '.join(failures))
    return {
        'schema_version': 1, 'spec_sha256': specification_digest(spec),
        'step_sha256': step_sha256, 'generator_version': 'dragster_wedge_v1',
        'cadquery_version': cq.__version__, 'solid_count': len(solids),
        'bounds_mm': bounds, 'volume_mm3': volume, 'checks': checks,
        'passed': True, 'engineering_certified': False,
    }
