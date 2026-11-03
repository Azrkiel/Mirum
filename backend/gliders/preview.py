"""Fast coarse meshes. No evaluator, optimizer or export imports on this path."""
from dataclasses import asdict
from .design_spec import VERSION, FlightConditions, controls, digest, placement, preset


def airfoils(spec):
    from .model.components import naca
    space = spec.request.configuration()['ranges']
    codes = {f'{m}{p if m else 0}{t:02d}' for m in space['wing']['naca_camber_pct']
             for p in space['wing']['naca_camber_pos'] for t in space['wing']['naca_thickness_pct']}
    codes.update(space['tail']['airfoil'])
    return {code: naca(code).repanel(n_points_per_side=13).coordinates.tolist() for code in sorted(codes)}


def metadata(spec=None):
    spec = spec or preset()
    return dict(version=VERSION, controls=controls(spec.request), preset=spec.model_dump(), flight_controls=FlightConditions.model_json_schema()["properties"], airfoils=airfoils(spec))


def geometry(gl):
    import numpy as np
    airplane = gl.airplane()
    parts = []
    for part in [*airplane.wings, *airplane.fuselages]:
        kwargs = dict(chordwise_resolution=12) if part in airplane.wings else dict(tangential_resolution=24)
        points, faces = part.mesh_body(method='tri', **kwargs)
        if len(points) > 10000 or len(faces) > 20000 or not np.isfinite(points).all():
            raise ValueError('Invalid or oversized geometry')
        parts.append(dict(name=part.name, vertices=points.tolist(), triangles=faces.tolist()))
    value = dict(parts=parts, placement=placement(gl))
    return dict(**value, geometry_digest=digest(value), units='m', axes='x aft, y right, z up',
                surfaces=[asdict(s) for s in [gl.wing_surface(), *gl.tail_surfaces()]],
                stations=gl.fuse.stations())


def preview(spec):
    gl = spec.build()  # Never share the evaluator's mutable assembly.
    warnings = []
    if gl.wing.style == 'polyhedral':
        warnings.append('Dihedral sets tip height; the centre is flat and the outer panel angle is larger.')
    return dict(input_revision=spec.digest(), implementation=VERSION, geometry=geometry(gl),
                warnings=warnings, status='preview', performance='needs_recalculation')
