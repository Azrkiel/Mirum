"""Deterministic shared-contract fixtures for B/C. NEVER native qualification.

Analytic polar rows deliberately have a sentinel executable identity. Consumers
may prototype load-path/family interfaces without a native installation. The
production parent reconstructs polars from native raw artifacts, not this bank.
"""
import math

from cem.core import digest
from .contracts import (Binding, Reference, Surface, Section, BodyStation,
                        InstalledGeometry, StationPolar, Transition, NativeIdentity, Artifact)
from .polars import PolarBank


def rectangular_fixture():
    ref=Reference(area_m2=.2,chord_m=.2,span_m=1.,cg_m=(.05,0.,0.),mass_kg=.1,
                  inertia_kg_m2=(.01,.01,.02))
    surface=Surface(name='wing',role='wing',naca='0012',mirror_y=True,sections=(
        Section(leading_edge_m=(0.,0.,0.),chord_m=.2,incidence_deg=0.),
        Section(leading_edge_m=(0.,.5,0.),chord_m=.2,incidence_deg=0.)))
    body=(BodyStation(x_m=0.,width_m=.001,height_m=.001,exponent=2.),
          BodyStation(x_m=.5,width_m=.001,height_m=.001,exponent=2.))
    payload=dict(family='conventional',reference=ref.model_dump(mode='json'),
        surfaces=[surface.model_dump(mode='json')],body=[s.model_dump(mode='json') for s in body])
    binding=Binding(geometry_sha256=digest(payload),**{k:'0'*64 for k in (
        'hardware_sha256','mission_sha256','process_sha256','calibration_sha256','implementation_sha256')})
    return InstalledGeometry(binding=binding,family='conventional',reference=ref,surfaces=(surface,),body=body)


def analytic_bank(geometry):
    angles=tuple(float(a) for a in range(-10,13))
    identity=NativeIdentity(solver='xfoil',executable_sha256='0'*64,
        artifacts=(Artifact(file='fixture-input',sha256='0'*64),Artifact(file='fixture-output',sha256='0'*64)))
    rows=[]
    for naca in sorted({s.naca for s in geometry.surfaces}):
        for reynolds in (30000.,1000000.):
            rows.append(StationPolar(binding=geometry.binding,naca=naca,reynolds=reynolds,mach=0.,
                transition=Transition(),alpha_deg=angles,cl=tuple(2*math.pi*math.radians(a) for a in angles),
                cd=tuple(.01 for a in angles),cm=tuple(0. for a in angles),native=identity))
    return PolarBank(rows,geometry.binding)


def structural_fixture():
    """One-newton uniform load: half-wing reaction .5 N, root moment .125 Nm.

    Unscaled point loads approximate a uniform beam load at strip centers;
    exact force and first moment give B a solver-free reaction reference.
    All zero artifact/binary identities are explicit fixture sentinels.
    """
    from .contracts import (StripLoad, LiftingSurfaceLoads, StructuralLoadCase,
                            InstalledStructure, InstalledMaterial, Support, OperatingPoint)
    g=rectangular_fixture()
    point=OperatingPoint(velocity_m_s=6.,alpha_deg=0.,density_kg_m3=1.225,viscosity_pa_s=1.8e-5)
    strips=[]
    for i in range(8):
        y=(i+.5)/8-.5
        strips.append(StripLoad(surface='wing',center_m=(.05,y,0.),area_m2=.025,chord_m=.2,
            velocity_m_s=(6.,0.,0.),induced_velocity_m_s=(0.,0.,0.),effective_alpha_deg=0.,
            reynolds=1.225*6*.2/1.8e-5,force_n=(0.,0.,.125),moment_cg_nm=(y*.125,0.,0.)))
    loads=LiftingSurfaceLoads(binding=g.binding,operating_point=point,strips=tuple(strips),
        force_n=(0.,0.,1.),moment_cg_nm=(0.,0.,0.),induced_drag_n=0.,profile_drag_n=0.,
        circulation_residual=0.,force_closure_n=0.,moment_closure_nm=0.)
    case=StructuralLoadCase(name='fixture-uniform-one-newton',binding=g.binding,
        aerodynamic_loads_sha256=digest(loads),load_factor=1.,loads=loads)
    materials=tuple(InstalledMaterial(part_id=name,material_id=material,artifact_sha256='0'*64,
        mass_kg=mass,cg_m=(.05,0.,0.),inertia_kg_m2=(.001,.001,.001),material_law=law,density_kg_m3=density)
        for name,material,mass,law,density in [('wing','foam',.08,'volume_density',30.),('spar','carbon',.02,'stock_rod',1600.)])
    structure=InstalledStructure(binding=g.binding,construction='fixture-only',materials=materials,
        supports=(Support(support_id='bond',parent_part_id='spar',child_part_id='wing',location_m=(.05,0.,0.),
            constrained_dofs=('tx','ty','tz','rx','ry','rz'),evidence_state='declared',artifact_sha256='0'*64),),
        closure_state='declared')
    return g,case,structure
