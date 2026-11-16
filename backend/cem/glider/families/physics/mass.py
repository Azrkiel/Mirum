"""Component mass, CG and full inertia tensors with exact parallel-axis transfer.

Axes G (x aft, y starboard, z up), SI units. The inertia *tensor* is

    I = sum/integral of m (r.r E - r r^T)

so off-diagonal tensor entries are the negative products of inertia, e.g.
I[0, 2] = -Jxz with Jxz = integral x z dm. Both are reported explicitly.
Transfer from the component CG to point P: I_P = I_cg + m (d.d E - d d^T),
with d = cg - P. Conversion to body axes B uses ``frames.tensor_geometry_to_body``;
Jxz keeps its sign there while Jxy and Jyz change sign.

Evidence classes are never merged into a stronger one. A calculated screening
inventory is not a measured delivered aircraft. No mass, hardware or
measurement is invented: unknown self-inertia is reported as omitted.
"""
from dataclasses import asdict
import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from cem.core import StrictModel, canonical_json, digest
from . import frames

Vector = tuple[float, float, float]
Tensor = tuple[Vector, Vector, Vector]
EVIDENCE = ('calculated_screening', 'declared_hardware', 'measured_delivered')


class MassComponent(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    mass_kg: float = Field(gt=0, le=100)
    cg_m: Vector
    inertia_cg_kg_m2: Tensor
    evidence: Literal[EVIDENCE]
    self_inertia: Literal['calculated', 'measured', 'point_mass_unknown_omitted']
    source: str = Field(min_length=1, max_length=300)

    @model_validator(mode='after')
    def physical(self):
        tensor = np.asarray(self.inertia_cg_kg_m2)
        check_tensor(tensor, allow_zero=self.self_inertia == 'point_mass_unknown_omitted')
        if self.self_inertia == 'point_mass_unknown_omitted' and np.any(tensor):
            raise ValueError('Omitted self-inertia must not carry substituted values')
        if self.self_inertia == 'measured' and self.evidence != 'measured_delivered':
            raise ValueError('Measured self-inertia requires measured delivered evidence')
        return self


def check_tensor(tensor, *, allow_zero=False, tolerance=1e-12):
    t = np.asarray(tensor, dtype=float)
    if t.shape != (3, 3) or not np.isfinite(t).all():
        raise ValueError('Finite 3x3 inertia tensor required')
    scale = max(float(np.max(np.abs(t))), 1e-30)
    if not np.allclose(t, t.T, rtol=0, atol=tolerance*scale):
        raise ValueError('Inertia tensor must be symmetric')
    if allow_zero and not np.any(t):
        return t
    values = np.linalg.eigvalsh(.5*(t+t.T))
    if values[0] <= 0:
        raise ValueError('Inertia tensor must be positive definite')
    a, b, c = values
    if a+b < c*(1-1e-9):
        raise ValueError('Principal inertias violate the triangle inequality')
    return t


def rotate(tensor, rotation):
    """Express a tensor given in local axes in G: I_G = R I_local R^T."""
    r = np.asarray(rotation, dtype=float)
    if r.shape != (3, 3) or not np.allclose(r@r.T, np.eye(3), atol=1e-12) or np.linalg.det(r) < 0:
        raise ValueError('Proper rotation required')
    return r@np.asarray(tensor, dtype=float)@r.T


def shift(tensor_cg, mass_kg, cg_m, point_m):
    d = frames.vector(cg_m)-frames.vector(point_m)
    return np.asarray(tensor_cg, dtype=float)+mass_kg*(float(d@d)*np.eye(3)-np.outer(d, d))


def _component(name, mass, cg, tensor, evidence, self_inertia, source):
    return MassComponent(name=name, mass_kg=float(mass), cg_m=tuple(map(float, frames.vector(cg))),
        inertia_cg_kg_m2=tuple(tuple(float(v) for v in row) for row in np.asarray(tensor)),
        evidence=evidence, self_inertia=self_inertia, source=source)


def point_mass(name, mass_kg, cg_m, *, evidence, source):
    return _component(name, mass_kg, cg_m, np.zeros((3, 3)), evidence, 'point_mass_unknown_omitted', source)


def solid_box(name, mass_kg, center_m, dimensions_m, *, rotation=np.eye(3), evidence, source):
    a, b, c = (float(v) for v in dimensions_m)
    if min(a, b, c) <= 0:
        raise ValueError('Positive box dimensions required')
    local = mass_kg/12*np.diag([b*b+c*c, a*a+c*c, a*a+b*b])
    return _component(name, mass_kg, center_m, rotate(local, rotation), evidence, 'calculated', source)


def solid_cylinder(name, mass_kg, center_m, axis, radius_m, length_m, *, evidence, source):
    """Uniform solid cylinder; ``axis`` is its symmetry direction in G."""
    if radius_m <= 0 or length_m <= 0:
        raise ValueError('Positive cylinder dimensions required')
    e = frames.vector(axis)
    e = e/np.linalg.norm(e)
    axial, transverse = mass_kg*radius_m**2/2, mass_kg*(3*radius_m**2+length_m**2)/12
    tensor = transverse*np.eye(3)+(axial-transverse)*np.outer(e, e)
    return _component(name, mass_kg, center_m, tensor, evidence, 'calculated', source)


def lamina(name, mass_kg, center_m, chord_direction, span_direction, chord_m, width_m, *, evidence, source):
    """Thin uniform rectangle with in-plane chord/width directions."""
    c = frames.vector(chord_direction); s = frames.vector(span_direction)
    c, s = c/np.linalg.norm(c), s/np.linalg.norm(s)
    if abs(float(c@s)) > 1e-12 or chord_m <= 0 or width_m <= 0:
        raise ValueError('Orthogonal lamina directions and positive dimensions required')
    rotation = np.column_stack([c, s, np.cross(c, s)])
    local = mass_kg/12*np.diag([width_m**2, chord_m**2, chord_m**2+width_m**2])
    return _component(name, mass_kg, center_m, rotate(local, rotation), evidence, 'calculated', source)


def combine(components, *, about_m=None):
    items = [c if isinstance(c, MassComponent) else MassComponent.model_validate_json(canonical_json(c))
             for c in components]
    if not items or len({c.name for c in items}) != len(items):
        raise ValueError('Unique non-empty mass components required')
    mass = sum(c.mass_kg for c in items)
    cg = sum(c.mass_kg*np.asarray(c.cg_m) for c in items)/mass
    tensor = sum((shift(c.inertia_cg_kg_m2, c.mass_kg, c.cg_m, cg) for c in items), np.zeros((3, 3)))
    tensor = .5*(tensor+tensor.T)
    check_tensor(tensor)
    values, vectors = np.linalg.eigh(tensor)
    evidence = sorted({c.evidence for c in items})
    omitted = sorted(c.name for c in items if c.self_inertia == 'point_mass_unknown_omitted')
    if evidence == ['measured_delivered'] and not omitted:
        state = 'measured_delivered_components'
    elif 'calculated_screening' in evidence:
        state = 'calculated_screening'
    else:
        state = 'declared_or_mixed_not_measured'
    record = dict(axes=frames.AXES['geometry'], units='kg, m, kg m^2', mass_kg=float(mass),
        cg_m=cg.tolist(), inertia_cg_kg_m2=tensor.tolist(),
        products_of_inertia_kg_m2=dict(Jxy=float(-tensor[0, 1]), Jxz=float(-tensor[0, 2]),
                                       Jyz=float(-tensor[1, 2])),
        principal_kg_m2=values.tolist(), principal_axes=vectors.T.tolist(),
        inertia_body_axes_kg_m2=frames.tensor_geometry_to_body(tensor).tolist(),
        component_count=len(items), component_evidence=evidence, omitted_self_inertia=omitted,
        state=state, measured_aircraft=False,
        components_sha256=digest([c.model_dump(mode='json') for c in items]))
    if about_m is not None:
        record['about_m'] = frames.vector(about_m).tolist()
        record['inertia_about_kg_m2'] = shift(tensor, mass, cg, about_m).tolist()
    record['mass_properties_sha256'] = digest(record)
    return record


class MeasuredAircraftProperties(StrictModel):
    """Required form for delivered-aircraft measurements; never synthesized."""
    article_revision_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    measurement_artifact_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    configuration: str = Field(min_length=1, max_length=200)
    mass_method: Literal['calibrated_scale']
    cg_method: Literal['multi_point_scale', 'suspension']
    inertia_method: Literal['compound_pendulum', 'bifilar_pendulum', 'trifilar_pendulum']
    mass_kg: float = Field(gt=0)
    mass_uncertainty_kg: float = Field(gt=0)
    cg_m: Vector
    cg_uncertainty_m: Vector
    inertia_cg_kg_m2: Tensor
    inertia_uncertainty_kg_m2: Tensor

    @model_validator(mode='after')
    def physical(self):
        check_tensor(self.inertia_cg_kg_m2)
        if min(self.cg_uncertainty_m) <= 0 or np.min(self.inertia_uncertainty_kg_m2) <= 0:
            raise ValueError('Positive measurement uncertainties required')
        return self


def compare_measured(calculated, measured, *, coverage=2.):
    """Calculated-vs-measured consistency; the measured record stays authoritative."""
    m = (measured if isinstance(measured, MeasuredAircraftProperties)
         else MeasuredAircraftProperties.model_validate_json(canonical_json(measured)))
    dm = abs(calculated['mass_kg']-m.mass_kg)
    dc = np.abs(np.subtract(calculated['cg_m'], m.cg_m))
    di = np.abs(np.subtract(calculated['inertia_cg_kg_m2'], m.inertia_cg_kg_m2))
    failures = []
    if dm > coverage*m.mass_uncertainty_kg:
        failures.append('mass')
    if np.any(dc > coverage*np.asarray(m.cg_uncertainty_m)):
        failures.append('cg')
    if np.any(di > coverage*np.asarray(m.inertia_uncertainty_kg_m2)):
        failures.append('inertia')
    return dict(state='consistent' if not failures else 'inconsistent', failures=failures,
        mass_difference_kg=dm, cg_difference_m=dc.tolist(), inertia_difference_kg_m2=di.tolist(),
        coverage_factor=coverage, authoritative='measured', calculated_state=calculated['state'])


def family_screening_components(aircraft):
    """Bind C's actual screening inventory, refining lifting strips to laminae.

    Every wing inventory point is regenerated from the installed surface and
    must match the archived inventory exactly. Strips become thin rectangles in
    their panel plane (sweep shear, taper within a strip, twist and the model's
    0.42-chord mass-centroid assumption remain screening approximations).
    Equipment and body are point masses with unknown, omitted self-inertia.
    """
    inventory = {p.name: p for p in aircraft.inventory}
    density = aircraft.parameters['inventory']['areal_density_kg_m2']
    source = 'C screening areal inventory; '+aircraft.builder_revision
    components, used = [], set()
    for surf in aircraft.surfaces:
        index = 0
        for a, b in zip(surf.sections[:-1], surf.sections[1:]):
            panel = math.hypot(b.y-a.y, b.z-a.z)
            if panel <= 0:
                raise ValueError('Degenerate lifting panel in mass binding')
            for k in range(8):
                t0, t1 = k/8, (k+1)/8
                tm = .5*(t0+t1)
                c0, c1, cm = (a.chord+(b.chord-a.chord)*t for t in (t0, t1, tm))
                area = .5*(c0+c1)*panel/8
                point = (a.x+(b.x-a.x)*tm+.42*cm, a.y+(b.y-a.y)*tm, a.z+(b.z-a.z)*tm)
                for sign in (-1, 1):
                    name = f'{surf.name}_{index}_{sign}'
                    archived = inventory.get(name)
                    expected = (area*density, point[0], sign*point[1], point[2])
                    if archived is None or not np.allclose(
                            (archived.mass_kg, archived.x_m, archived.y_m, archived.z_m), expected,
                            rtol=0, atol=1e-15):
                        raise ValueError('Lifting strip does not match archived inventory')
                    span = np.array([0., sign*(b.y-a.y), b.z-a.z])/panel
                    components.append(lamina(name, archived.mass_kg,
                        (archived.x_m, archived.y_m, archived.z_m), (1., 0., 0.), span, cm, panel/8,
                        evidence='calculated_screening', source=source))
                    used.add(name)
                index += 1
    for name, part in inventory.items():
        if name not in used:
            components.append(point_mass(name, part.mass_kg, (part.x_m, part.y_m, part.z_m),
                evidence='calculated_screening', source=source))
    if len(components) != len(inventory):
        raise ValueError('Mass binding does not account for every inventory item')
    return components


def family_screening_properties(aircraft):
    """Calculated screening inventory properties bound to the aircraft revision."""
    components = family_screening_components(aircraft)
    record = combine(components)
    summary = aircraft.mass_summary()
    if (abs(record['mass_kg']-summary['mass_kg']) > 1e-12
            or not np.allclose(record['cg_m'], summary['cg_m'], rtol=0, atol=1e-12)):
        raise ValueError('Mass binding disagrees with C inventory summary')
    point = combine([point_mass(c.name, c.mass_kg, c.cg_m, evidence=c.evidence, source=c.source)
                     for c in components])
    return dict(record, aircraft_revision=aircraft.revision, family=aircraft.family,
        point_inventory_inertia_cg_kg_m2=point['inertia_cg_kg_m2'],
        scope='calculated_screening_inventory_not_measured_delivered_aircraft',
        missing=['hinges, servos, linkages, receiver, battery and other control hardware',
                 'body OML and body/equipment mass distributions (point masses)',
                 'measured delivered mass, CG and full inertia'],
        binding_sha256=digest(dict(aircraft_revision=aircraft.revision,
                                   mass=record['mass_properties_sha256'],
                                   inventory=[asdict(p) for p in aircraft.inventory])))


def reference_rigid_body(mass_kg, cg_m, inertia_body_axes_kg_m2, *, source):
    """Declared rigid body for analytical/published reference checks only.

    Not an aircraft mass model: it carries no components and can never satisfy
    a family qualification. The body-axes tensor is validated like any other.
    """
    if not (math.isfinite(mass_kg) and mass_kg > 0):
        raise ValueError('Positive finite reference mass required')
    body = check_tensor(inertia_body_axes_kg_m2)
    tensor = frames.tensor_body_to_geometry(body)
    record = dict(axes=frames.AXES['geometry'], units='kg, m, kg m^2', mass_kg=float(mass_kg),
        cg_m=frames.vector(cg_m).tolist(), inertia_cg_kg_m2=tensor.tolist(),
        products_of_inertia_kg_m2=dict(Jxy=float(-tensor[0, 1]), Jxz=float(-tensor[0, 2]),
                                       Jyz=float(-tensor[1, 2])),
        inertia_body_axes_kg_m2=np.asarray(body, dtype=float).tolist(), component_count=0,
        component_evidence=['reference'], omitted_self_inertia=[], state='reference_rigid_body_not_aircraft',
        measured_aircraft=False, source=source)
    record['mass_properties_sha256'] = digest(record)
    return record
