"""Shared A/B/C contracts. All dimensional values are SI; no implicit symmetry.

Geometry axes: x aft, y starboard, z up (right handed). Forces/moments use
these axes; pitch My is positive nose-up. AVL geometric axes are identical;
AVL stability-axis coefficients require conversion before structural use.
alpha/beta/incidence are degrees; derivatives are per radian. Rate derivatives
use p*b/(2V), q*c/(2V), r*b/(2V). CG is the moment origin. Geometry describes
the installed OML, not evidence of fabrication or delivered-material closure.
"""
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from cem.core import StrictModel, digest
from . import CONTRACT_VERSION

Positive = Annotated[float, Field(gt=0)]
SHA256 = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Vector = tuple[float, float, float]


class Binding(StrictModel):
    geometry_sha256: SHA256
    hardware_sha256: SHA256
    mission_sha256: SHA256
    process_sha256: SHA256
    calibration_sha256: SHA256
    implementation_sha256: SHA256


class Reference(StrictModel):
    area_m2: Positive
    chord_m: Positive
    span_m: Positive
    cg_m: Vector
    mass_kg: Positive
    inertia_kg_m2: Vector  # principal approximations Ixx, Iyy, Izz; Ixz omitted

    @model_validator(mode='after')
    def inertia(self):
        if min(self.inertia_kg_m2) <= 0:
            raise ValueError('Positive inertias required')
        return self


class Section(StrictModel):
    leading_edge_m: Vector
    chord_m: Positive
    incidence_deg: float = Field(ge=-15, le=15)


class Surface(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    role: Literal['wing', 'htail', 'vtail', 'vee', 'forewing', 'aftwing', 'canard', 'fin']
    naca: str = Field(pattern=r'^\d{4}$')
    mirror_y: bool
    sections: tuple[Section, ...] = Field(min_length=2, max_length=16)


class BodyStation(StrictModel):
    x_m: float
    width_m: Positive
    height_m: Positive
    exponent: float = Field(ge=2, le=5)


class InstalledGeometry(StrictModel):
    contract_version: Literal['mirum-aero-1'] = CONTRACT_VERSION
    binding: Binding
    # The contract permits C's planned identities. Numerical availability is
    # separately gated; accepting a geometry contract does not qualify a family.
    family: Literal['conventional', 't_tail', 'v_tail', 'tailless_swept', 'canard', 'tandem']
    reference: Reference
    surfaces: tuple[Surface, ...] = Field(min_length=1, max_length=8)
    body: tuple[BodyStation, ...] = Field(min_length=2, max_length=64)

    @model_validator(mode='after')
    def geometry(self):
        if len({s.name for s in self.surfaces}) != len(self.surfaces):
            raise ValueError('Surface names must be unique')
        if any(b.x_m <= a.x_m for a, b in zip(self.body, self.body[1:])):
            raise ValueError('Body stations must strictly increase')
        for s in self.surfaces:
            if s.mirror_y and any(sec.leading_edge_m[1] < 0 for sec in s.sections):
                raise ValueError('Mirrored input contains only the positive-y half')
            if any(a.leading_edge_m == b.leading_edge_m for a,b in zip(s.sections, s.sections[1:])):
                raise ValueError('Degenerate surface panel')
        if self.binding.geometry_sha256 != digest(self.geometry_payload()):
            raise ValueError('Installed geometry revision mismatch')
        return self

    def geometry_payload(self):
        return dict(family=self.family, reference=self.reference.model_dump(mode='json'),
                    surfaces=[s.model_dump(mode='json') for s in self.surfaces],
                    body=[s.model_dump(mode='json') for s in self.body])


class OperatingPoint(StrictModel):
    velocity_m_s: float = Field(gt=0, le=30)
    alpha_deg: float = Field(ge=-10, le=12)
    beta_deg: Literal[0.] = 0.
    density_kg_m3: Positive
    viscosity_pa_s: Positive
    mach: float = Field(0., ge=0, le=.1)
    p_hat: Literal[0.] = 0.
    q_hat: Literal[0.] = 0.
    r_hat: Literal[0.] = 0.


class Transition(StrictModel):
    n_crit: float = Field(9., ge=1, le=12)
    top_xtr: float = Field(1., gt=0, le=1)
    bottom_xtr: float = Field(1., gt=0, le=1)


class Artifact(StrictModel):
    file: str = Field(pattern=r'^[a-zA-Z0-9_./-]{1,180}$')
    sha256: SHA256


class NativeIdentity(StrictModel):
    solver: Literal['xfoil', 'avl']
    executable_sha256: SHA256
    artifacts: tuple[Artifact, ...] = Field(min_length=2)


class StationPolar(StrictModel):
    contract_version: Literal['mirum-aero-1'] = CONTRACT_VERSION
    binding: Binding
    naca: str = Field(pattern=r'^\d{4}$')
    reynolds: Positive
    mach: float = Field(ge=0, le=.1)
    transition: Transition
    alpha_deg: tuple[float, ...] = Field(min_length=3, max_length=81)
    cl: tuple[float, ...]
    cd: tuple[Positive, ...]
    cm: tuple[float, ...]
    native: NativeIdentity

    @model_validator(mode='after')
    def coverage(self):
        n = len(self.alpha_deg)
        if any(len(v) != n for v in (self.cl, self.cd, self.cm)):
            raise ValueError('Incomplete polar rows')
        if any(b <= a for a,b in zip(self.alpha_deg, self.alpha_deg[1:])):
            raise ValueError('Polar angles must strictly increase')
        if self.native.solver != 'xfoil':
            raise ValueError('Station polars require native XFOIL')
        return self


class StripLoad(StrictModel):
    surface: str
    center_m: Vector
    area_m2: Positive
    chord_m: Positive
    velocity_m_s: Vector  # local velocity projected into the section plane
    induced_velocity_m_s: Vector
    effective_alpha_deg: float
    reynolds: Positive
    force_n: Vector
    moment_cg_nm: Vector


class LiftingSurfaceLoads(StrictModel):
    contract_version: Literal['mirum-aero-1'] = CONTRACT_VERSION
    binding: Binding
    operating_point: OperatingPoint
    strips: tuple[StripLoad, ...] = Field(min_length=1, max_length=256)
    force_n: Vector
    moment_cg_nm: Vector
    induced_drag_n: float = Field(ge=0)
    profile_drag_n: float = Field(ge=0)
    circulation_residual: float = Field(ge=0)
    force_closure_n: float = Field(ge=0)
    moment_closure_nm: float = Field(ge=0)

    @model_validator(mode='after')
    def conservation(self):
        for key,total in (('force_n',self.force_n),('moment_cg_nm',self.moment_cg_nm)):
            summed=tuple(sum(getattr(s,key)[i] for s in self.strips) for i in range(3))
            if max(abs(a-b) for a,b in zip(summed,total))>1e-8:
                raise ValueError('Strip force/moment sum differs from load total')
        if self.force_closure_n>1e-8 or self.moment_closure_nm>1e-8 or self.circulation_residual>1e-6:
            raise ValueError('Unconverged/nonconserving lifting loads')
        return self


class BodyIncrement(StrictModel):
    binding: Binding
    operating_point: OperatingPoint
    method: Literal['slender-body-friction-crossflow-v1'] = 'slender-body-friction-crossflow-v1'
    force_n: Vector
    moment_cg_nm: Vector
    drag_n: float = Field(ge=0)
    drag_error_bound_n: float = Field(ge=0)
    limitations: tuple[str, ...]


class StructuralLoadCase(StrictModel):
    contract_version: Literal['mirum-aero-1'] = CONTRACT_VERSION
    name: str
    binding: Binding
    aerodynamic_loads_sha256: SHA256
    load_factor: Positive  # multiplier to apply to the recorded steady loads
    loads: LiftingSurfaceLoads
    # B must add body, inertial/payload/rod, gust/landing, torsion and joints.
    scope: Literal['rigid-steady-lifting-surfaces-only'] = 'rigid-steady-lifting-surfaces-only'

    @model_validator(mode='after')
    def revision(self):
        if self.loads.binding != self.binding or digest(self.loads) != self.aerodynamic_loads_sha256:
            raise ValueError('Structural load case revision mismatch')
        return self


class FamilyBuilder(Protocol):
    """C implements one builder and admission policy per actual family."""
    contract_version: str
    family: str

    def installed_geometry(self, parameters: dict, hardware: dict, binding: Binding) -> InstalledGeometry: ...
    def admit(self, geometry: InstalledGeometry, loads: LiftingSurfaceLoads) -> tuple[str, ...]: ...


class InstalledMaterial(StrictModel):
    part_id: str = Field(min_length=1, max_length=80)
    material_id: str = Field(min_length=1, max_length=80)
    artifact_sha256: SHA256
    mass_kg: Positive
    cg_m: Vector
    inertia_kg_m2: Vector
    material_law: Literal['volume_density', 'area_density', 'stock_rod']
    density_kg_m3: Positive | None = None
    areal_density_kg_m2: Positive | None = None

    @model_validator(mode='after')
    def law(self):
        if min(self.inertia_kg_m2)<0:
            raise ValueError('Negative material inertia')
        if self.material_law=='area_density':
            if self.areal_density_kg_m2 is None or self.density_kg_m3 is not None:
                raise ValueError('Area material requires only areal density')
        elif self.density_kg_m3 is None or self.areal_density_kg_m2 is not None:
            raise ValueError('Volume/stock material requires only volume density')
        return self


class Support(StrictModel):
    support_id: str
    parent_part_id: str
    child_part_id: str
    location_m: Vector
    constrained_dofs: tuple[Literal['tx','ty','tz','rx','ry','rz'], ...] = Field(min_length=1,max_length=6)
    evidence_state: Literal['declared', 'measured']
    artifact_sha256: SHA256

    @model_validator(mode='after')
    def attachment(self):
        if self.parent_part_id==self.child_part_id or len(set(self.constrained_dofs))!=len(self.constrained_dofs):
            raise ValueError('Invalid self-support/duplicate constraint')
        return self


class InstalledStructure(StrictModel):
    contract_version: Literal['mirum-aero-1'] = CONTRACT_VERSION
    binding: Binding
    construction: str
    materials: tuple[InstalledMaterial, ...] = Field(min_length=1,max_length=100)
    supports: tuple[Support, ...] = Field(min_length=1,max_length=100)
    closure_state: Literal['declared', 'measured', 'rejected']

    @model_validator(mode='after')
    def inventory(self):
        ids={p.part_id for p in self.materials}
        if len(ids)!=len(self.materials) or len({s.support_id for s in self.supports})!=len(self.supports):
            raise ValueError('Duplicate structural inventory/interface')
        if any(s.parent_part_id not in ids or s.child_part_id not in ids for s in self.supports):
            raise ValueError('Support references missing installed material')
        return self


def installed_geometry(gl, req, calibration, process):
    """Freeze installed controls, mass/CG, rod and full request context once."""
    from cem.provenance import provenance
    from cem.glider.comparison import arm_inputs
    ms, ref = gl.mass_summary(), gl.wing_ref()
    reference = Reference(area_m2=float(ref['S']), chord_m=float(ref['mac']), span_m=float(ref['b']),
        cg_m=tuple(float(x) for x in ms['cg']), mass_kg=float(ms['mass']),
        inertia_kg_m2=tuple(float(ms[k]) for k in ('Ixx','Iyy','Izz')))
    surfaces = tuple(Surface(name=s.name, role=s.role, naca=s.airfoil, mirror_y=s.symmetric,
        sections=tuple(Section(leading_edge_m=(float(c.x),float(c.y),float(c.z)),
            chord_m=float(c.chord), incidence_deg=float(c.twist)) for c in s.sections))
        for s in [gl.wing_surface(), *gl.tail_surfaces()])
    body = tuple(BodyStation(x_m=float(x),width_m=float(w),height_m=float(h),exponent=float(n))
                 for x,w,h,n in gl.fuse.stations())
    family = {'conventional':'conventional','T':'t_tail','V':'v_tail'}[gl.tail.kind]
    payload = dict(family=family,reference=reference.model_dump(mode='json'),
        surfaces=[s.model_dump(mode='json') for s in surfaces],body=[s.model_dump(mode='json') for s in body])
    binding = Binding(geometry_sha256=digest(payload),hardware_sha256=digest(arm_inputs(gl,process)),
        mission_sha256=digest(req),process_sha256=digest(dict(process=process,construction=req.construction,
            materials=gl.cfg['materials'])),calibration_sha256=digest(calibration),
        implementation_sha256=provenance()['implementation_sha256'])
    return InstalledGeometry(binding=binding,family=family,reference=reference,surfaces=surfaces,body=body)
