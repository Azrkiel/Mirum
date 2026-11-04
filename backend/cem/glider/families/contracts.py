"""Task C adapters to A's published mirum-aero-1 interfaces.

Only neutral-control, NACA-section geometry is representable in v1. Custom
reflex must not be replaced by its base NACA foil. Body stations are supplied
explicitly; equipment/body masses remain C's screening inventory, not measured
construction. No adapter grants numerical or physical family qualification.
"""
from dataclasses import asdict
import math

from pydantic import Field

from cem.core import StrictModel, digest
from cem.aero import CONTRACT_VERSION
from cem.aero.contracts import (Binding, BodyStation, InstalledGeometry,
    LiftingSurfaceLoads, Reference, Section, StructuralLoadCase, Surface)
from . import build


class Hardware(StrictModel):
    body: tuple[BodyStation, ...] = Field(min_length=2, max_length=64)
    control_deflections_deg: dict[str, float]


def _payload(family, parameters, hardware):
    a = build(family, parameters)
    h = hardware if isinstance(hardware, Hardware) else Hardware.model_validate(hardware)
    if a.reflex_fraction != 0.:
        raise ValueError('mirum-aero-1 cannot represent custom reflex coordinates')
    controls = {c.name for c in a.controls}
    if set(h.control_deflections_deg) != controls:
        raise ValueError('Explicit installed state required for every declared control')
    if any(v != 0. for v in h.control_deflections_deg.values()):
        raise ValueError('mirum-aero-1 cannot represent deflected family controls')
    length = a.parameters['inventory']['body_length_m']
    if h.body[0].x_m != 0. or not math.isclose(h.body[-1].x_m, length, abs_tol=1e-10, rel_tol=0):
        raise ValueError('Body stations must cover the declared installed body length')
    if any(b.x_m <= p.x_m for p, b in zip(h.body, h.body[1:])):
        raise ValueError('Body stations must strictly increase')
    ms, ap = a.mass_summary(), a.airplane()
    cg = ms['cg_m']
    # The published Reference explicitly permits diagonal point-mass
    # approximations. Retain all mirrored wing strip masses; no invented inertia.
    inertia = tuple(sum(p.mass_kg*sum((getattr(p, axis+'_m')-cg[i])**2
                    for i, axis in enumerate(('x', 'y', 'z')) if i != j)
                        for p in a.inventory) for j in range(3))
    reference = Reference(area_m2=float(ap.s_ref), chord_m=float(ap.c_ref), span_m=float(ap.b_ref),
        cg_m=tuple(cg), mass_kg=ms['mass_kg'], inertia_kg_m2=inertia)
    roles = {'main_wing': 'wing', 'foreplane': 'canard', 'front_wing': 'forewing', 'rear_wing': 'aftwing'}
    surfaces = tuple(Surface(name=s.name, role=roles[s.role], naca=s.airfoil, mirror_y=s.symmetric,
        sections=tuple(Section(leading_edge_m=(c.x,c.y,c.z), chord_m=c.chord,
                               incidence_deg=c.twist) for c in s.sections)) for s in a.surfaces)
    payload = dict(family=family, reference=reference.model_dump(mode='json'),
        surfaces=[s.model_dump(mode='json') for s in surfaces], body=[b.model_dump(mode='json') for b in h.body])
    hardware_sha = digest(dict(aircraft_revision=a.revision, hardware=h.model_dump(mode='json'),
        controls=[asdict(c) for c in a.controls], inventory=[asdict(p) for p in a.inventory]))
    return a, reference, surfaces, h.body, payload, hardware_sha


class FamilyAdapter:
    contract_version = CONTRACT_VERSION

    def __init__(self, family):
        if family not in ('tailless_swept','canard','tandem'):
            raise ValueError('Unsupported C family adapter')
        self.family = family

    def installed_geometry(self, parameters, hardware, binding: Binding) -> InstalledGeometry:
        _, reference, surfaces, body, payload, hardware_sha = _payload(self.family, parameters, hardware)
        if binding.geometry_sha256 != digest(payload) or binding.hardware_sha256 != hardware_sha:
            raise ValueError('Family geometry/hardware context mismatch')
        return InstalledGeometry(binding=binding, family=self.family, reference=reference,
                                 surfaces=surfaces, body=body)

    def prepare(self, parameters, hardware, *, mission, process, calibration):
        """Freeze actual caller context; no fabricated mission/calibration defaults."""
        from cem.provenance import provenance
        *_, payload, hardware_sha = _payload(self.family, parameters, hardware)
        binding = Binding(geometry_sha256=digest(payload), hardware_sha256=hardware_sha,
            mission_sha256=digest(mission), process_sha256=digest(process),
            calibration_sha256=digest(calibration), implementation_sha256=provenance()['implementation_sha256'])
        return self.installed_geometry(parameters, hardware, binding)

    def admit(self, geometry: InstalledGeometry, loads: LiftingSurfaceLoads) -> tuple[str, ...]:
        failures = list(load_failures(geometry, loads))
        if geometry.family != self.family:
            failures.append('installed geometry belongs to another family')
        failures.extend(('family-specific native aerodynamic qualification incomplete',
            'control authority, dynamic stability and interaction/stall evidence missing',
            'family construction/load-path qualification unavailable'))
        return tuple(failures)


def load_failures(geometry, loads):
    geometry = InstalledGeometry.model_validate_json(geometry.model_dump_json())
    loads = LiftingSurfaceLoads.model_validate_json(loads.model_dump_json())
    if loads.binding != geometry.binding:
        raise ValueError('Family load/installed context mismatch')
    expected = {s.name for s in geometry.surfaces}
    if {s.surface for s in loads.strips} != expected:
        raise ValueError('Missing or unexpected installed lifting-surface loads')
    failures = []
    if abs(loads.force_n[2]/(geometry.reference.mass_kg*9.80665)-1.) > .02:
        failures.append('shared lifting loads do not close screening weight')
    for name in expected:
        if sum(s.force_n[2] for s in loads.strips if s.surface == name) <= 0:
            failures.append(name+': nonpositive lifting contribution')
    return tuple(failures)


def structural_case(geometry, loads, *, name, load_factor):
    """A/B handoff only: rigid steady loads, without claiming joint qualification."""
    if load_failures(geometry, loads):
        raise ValueError('Family lifting loads fail screening closure')
    return StructuralLoadCase(name=name, binding=geometry.binding,
        aerodynamic_loads_sha256=digest(loads), load_factor=load_factor, loads=loads)


def family_reactions(geometry, case, *, root_axes_m):
    """Sequential C/B handoff: every installed member, all six resultants.

    Roots are explicit installed load-transfer references, not qualified joints.
    B retains all point loads, section couples and omitted-surface diagnostics.
    """
    import numpy as np
    from cem.glider.structural_adapter import consume_lifting_surface
    geometry = InstalledGeometry.model_validate_json(geometry.model_dump_json())
    case = StructuralLoadCase.model_validate_json(case.model_dump_json())
    if load_failures(geometry, case.loads):
        raise ValueError('Family lifting loads fail screening closure')
    if set(root_axes_m) != {s.name for s in geometry.surfaces}:
        raise ValueError('Explicit root required for every installed lifting surface')
    members = {name: consume_lifting_surface(geometry, case, expected_binding=geometry.binding,
                   root_axis_m=root, surface=name) for name,root in root_axes_m.items()}
    force, moment = np.zeros(3), np.zeros(3)
    for name, member in members.items():
        for side in member['sides'].values():
            f = np.array(side['force_n'])
            force += f
            moment += np.array(side['moment_root_nm'])+np.cross(
                np.array(root_axes_m[name])-geometry.reference.cg_m, f)
    if (not np.allclose(force,np.array(case.loads.force_n)*case.load_factor,rtol=0,atol=1e-8)
            or not np.allclose(moment,np.array(case.loads.moment_cg_nm)*case.load_factor,rtol=0,atol=1e-8)):
        raise ValueError('Family member resultant conservation failed')
    return dict(state='incomplete', scope='rigid-steady-family-member-resultants-only',
        binding=geometry.binding.model_dump(mode='json'), load_case_sha256=digest(case),
        members=members, force_n=force.tolist(), moment_cg_nm=moment.tolist(),
        physical_construction=False, manufacturing_ready=False,
        missing=['aerodynamic/material correspondence', 'family attachments and complete body path',
                 'multi-axis strength and sizing', 'inertia, launch, gust and landing cases'])


def validate_family_reactions(report, geometry, case, *, root_axes_m):
    from cem.core import canonical_json
    expected = family_reactions(geometry, case, root_axes_m=root_axes_m)
    if canonical_json(report) != canonical_json(expected):
        raise ValueError('Family reaction evidence differs from reconstructed load handoff')
    return False  # Valid resultants are not physical construction admission.
