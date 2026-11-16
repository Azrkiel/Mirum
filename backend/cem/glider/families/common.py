"""Task C screening geometry. SI, x aft / y right / z up; no solver admission.

This is an isolated prototype contract pending Task A's shared-contract merge.
OML areal masses and lumped equipment are not a construction/load-path model.
"""
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path

from pydantic import Field

from cem.core import StrictModel, digest
from gliders.model.components import Section, Surface, naca


class Wing(StrictModel):
    span_m: float = Field(1.2, ge=.2, le=1.4)
    root_chord_m: float = Field(.18, ge=.04, le=.4)
    taper: float = Field(.65, ge=.3, le=1.)
    sweep_deg: float = Field(0., ge=-10, le=40)
    incidence_deg: float = Field(2., ge=-8, le=10)
    washout_deg: float = Field(2., ge=0, le=6)
    dihedral_deg: float = Field(3., ge=0, le=12)
    airfoil: str = Field('2412', pattern=r'^[0-4][0-9](08|10|12|14)$')


class Inventory(StrictModel):
    areal_density_kg_m2: float = Field(.35, ge=.05, le=1.)
    equipment_mass_kg: float = Field(.06, ge=.001, le=.3)
    equipment_x_m: float = Field(.28, ge=0, le=.9)
    body_mass_kg: float = Field(.02, ge=.001, le=.1)
    body_length_m: float = Field(.7, ge=.2, le=.95)


@dataclass(frozen=True)
class Control:
    name: str
    surface: str
    eta_start: float
    eta_end: float
    hinge_chord_fraction: float
    symmetric: bool
    limit_deg: float


@dataclass(frozen=True)
class Mass:
    name: str
    mass_kg: float
    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True)
class Aircraft:
    family: str
    builder_revision: str
    parameters: dict
    surfaces: tuple
    controls: tuple[Control, ...]
    inventory: tuple[Mass, ...]
    reflex_fraction: float = 0.

    @property
    def revision(self):
        # Include generated geometry, controls and inventory, not just input labels.
        from dataclasses import asdict
        return digest(asdict(self))

    def mass_summary(self):
        m = sum(p.mass_kg for p in self.inventory)
        cg = [sum(p.mass_kg * getattr(p, axis+'_m') for p in self.inventory)/m
              for axis in ('x', 'y', 'z')]
        return dict(mass_kg=m, cg_m=cg, state='screening_areal_inventory')

    def airplane(self):
        """Native geometry for meshing; controls are declared, not aero-modelled."""
        import aerosandbox as asb
        wings = []
        for surface in self.surfaces:
            af = naca(surface.airfoil)
            if self.reflex_fraction:
                # Explicit trailing-edge camber change, never silently export a
                # NACA surrogate for the swept family's reflexed section.
                points = af.coordinates.copy()
                points[:, 1] += self.reflex_fraction * (points[:, 0]**3)
                af = asb.Airfoil(name='screening_reflex', coordinates=points)
            wings.append(asb.Wing(name=surface.name, symmetric=True,
                xsecs=[asb.WingXSec(xyz_le=[s.x, s.y, s.z], chord=s.chord,
                                    twist=s.twist, airfoil=af) for s in surface.sections]))
        areas = [w.area() for w in wings]
        return asb.Airplane(name=self.family, wings=wings,
            s_ref=sum(areas), b_ref=max(w.span() for w in wings),
            c_ref=sum(a*w.mean_aerodynamic_chord() for a, w in zip(areas, wings))/sum(areas),
            xyz_ref=self.mass_summary()['cg_m'])


def surface(wing, name, x, z=0.):
    sections = []
    for eta in (0., .35, .7, 1.):
        y = eta*wing.span_m/2
        chord = wing.root_chord_m*(1-(1-wing.taper)*eta)
        sections.append(Section(x+.25*wing.root_chord_m+y*math.tan(math.radians(wing.sweep_deg))-.25*chord,
            y, z+y*math.tan(math.radians(wing.dihedral_deg)), chord,
            wing.incidence_deg-wing.washout_deg*eta))
    # Actual roles are preserved; neither foreplane nor rear lifting wing is htail.
    return Surface(name, sections, True, wing.airfoil, name)


def inventory(surfaces, spec):
    parts = [Mass('equipment', spec.equipment_mass_kg, spec.equipment_x_m, 0., 0.),
             Mass('body', spec.body_mass_kg, spec.body_length_m/2, 0., 0.)]
    for surf in surfaces:
        for i, (area, x, y, z) in enumerate(surf.strips(n_sub=8)):
            for sign in (-1, 1):
                parts.append(Mass(f'{surf.name}_{i}_{sign}', area*spec.areal_density_kg_m2,
                                  x, sign*y, z))
    return tuple(parts)


def make_aircraft(family, revision, params, surfaces, controls, reflex=0.):
    for surf in surfaces:
        if any(s.x < 0 or s.x+s.chord > params.inventory.body_length_m for s in surf.sections):
            raise ValueError('Lifting surface outside declared body-length envelope')
    sources = [Path(__file__), Path(__file__).with_name(family+'.py')]
    implementation = digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    return Aircraft(family, revision+'+'+implementation, params.model_dump(mode='json'), tuple(surfaces),
                    tuple(controls), inventory(surfaces, params.inventory), reflex)
