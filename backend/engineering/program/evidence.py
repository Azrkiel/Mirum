"""Pure F07 evidence records: check plan, delivered-geometry inputs and delivered evidence.

The check plan is a function of the reviewed IR, the v0_2 lock and the reviewed candidates only; the
builder takes no measurement argument, so no limit can come from measured geometry or from a report.
Every limit names its source: 'reviewed:<requirement id>' or 'policy:<NumericalPolicy field>'.
Records fail closed: a passed required check needs measured coverage and a measurement against a
plan limit; identity_only never passes; a mesh cannot satisfy a brep check; physics is never
'measured'; manufacturing and standards are always not_evaluated; the verdict is never 'accepted'.

Nothing here touches a store, a job, a bundle, native code, a web framework or a provider.
"""
from __future__ import annotations

import hashlib
import math
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from engineering.capabilities import CAPABILITY
from engineering.contracts import CandidateSpec
from engineering.contracts import Sha256 as Hash64
from engineering.spec import specification_digest

from .beam_bridge import HEIGHT_ID, _REQUIREMENTS
from .errors import IRModel, ProgramError
from .hashing import canonical_json, execution_lock_v0_2_sha256, semantic_sha256
from .ir import Identifier, ProgramIR, QuantityValue, SemanticId
from .kernel import ClaimScope, ExecutionLockV0_2, NativeVersion
from .versions import CHECK_PLAN_VERSION, DELIVERED_EVIDENCE_VERSION

__all__ = [
    'CATEGORIES', 'PREDICATES', 'REASONS', 'BEAM_FEATURES', 'BEAM_CHECK_IDS', 'REVIEWED_IDS',
    'POLICY_FIELDS', 'MAX_FACES', 'MAX_CANDIDATES',
    'CheckLimit', 'CheckEntry', 'CandidateChecks', 'CheckPlan', 'FaceRecord', 'FaceTable',
    'MeshSummary', 'FaceSignature', 'FeatureBinding', 'Measurement', 'ErrorBound', 'CheckEvidence',
    'DeliveredEvidence', 'build_check_plan', 'check_plan_sha256', 'evidence_sha256',
    'derive_verdict', 'check_evidence_against_plan', 'ReportInputs', 'bind_features', 'evaluate',
    'build_delivered_evidence', 'preview_can_pass', 'GLB_LINEAR_DEFLECTION_MM', 'GLB_F32_REL',
    'UNKNOWN_SHA256',
]

MAX_FACES = 64
MAX_CANDIDATES = 4
CATEGORIES = ('geometry', 'preview', 'physics', 'manufacturing', 'standards')
BEAM_FEATURES = ('beam.solid', 'beam.root_section', 'beam.free_end')
PREDICATES = (
    'solid_single_closed', 'root_section_face', 'free_end_face', 'dimension_length', 'dimension_width',
    'dimension_height', 'datum_origin', 'prism_volume', 'preview_extent', 'physics_report_inputs',
    'not_evaluated')
REASONS = (
    'binding_missing', 'binding_ambiguous', 'limit_exceeded', 'report_mismatch', 'lock_mismatch',
    'mock_backend', 'not_pinned', 'out_of_scope', 'measurement_unavailable', 'bound_exceeds_tolerance')
# Reviewed numeric requirement ids a limit may cite (text requirements can never scale a limit).
_TEXT_REQUIREMENTS = ('material.reference', 'material.source_kind', 'model.assumptions')
REVIEWED_IDS = tuple(rid for rid, _ in _REQUIREMENTS if rid not in _TEXT_REQUIREMENTS) + (HEIGHT_ID,)
_POLICY_VALUES = {name: value for name, value in CAPABILITY.numerical.model_dump().items()
                  if isinstance(value, (int, float)) and not isinstance(value, bool)}
POLICY_FIELDS = tuple(sorted(_POLICY_VALUES))

_BIG = 1e12
# Placeholder for a hash that was never computed (a non-verified record made before the bundle, the plan
# or the candidate was known). A verified record can never carry it.
UNKNOWN_SHA256 = '0' * 64

# GLB preview error model (manager decision, F07 design notes).
# Source: CadQuery v2.6.1 cadquery/assembly.py, Assembly.save(exportType='GLB') meshes with the default
# linear deflection tolerance=0.1 model units (mm), lines 513-514. Mesh vertices lie on the true surface,
# so the true extent of an axis lies in [mesh extent, mesh extent + 2 * deflection].
GLB_LINEAR_DEFLECTION_MM = 0.1
# float32 decode bound for POSITION values, relative to the largest absolute coordinate on the axis.
# A float32 rounds to nearest with relative error <= 2**-24, so an extent (hi - lo) is off by <= 2**-23 *
# max(|lo|, |hi|). One float32 node-transform multiplication may add the same again, hence 2**-22.
GLB_F32_REL = 2.0 ** -22
_FORBIDDEN_LABELS = ('beam.', 'checked_design', 'accepted for manufacture', 'accepted_for_manufacture',
                     'manufacturing accepted', 'manufacturing_accepted', 'approved for manufacture')

CheckState = Literal['passed', 'failed', 'not_evaluated', 'unavailable']
Coverage = Literal['measured', 'policy_report', 'identity_only', 'not_evaluated']
Representation = Literal['brep', 'mesh']
Verdict = Literal['verified', 'failed', 'ambiguous', 'unavailable']


def _ascii(value: str) -> str:
    if not all(0x20 <= ord(c) <= 0x7e for c in value):
        raise ValueError('text must be printable ASCII')
    return value


Ascii64 = Annotated[str, StringConstraints(max_length=64), AfterValidator(_ascii)]
Ascii120 = Annotated[str, StringConstraints(max_length=120), AfterValidator(_ascii)]
Id32 = Annotated[str, StringConstraints(min_length=32, max_length=32, pattern=r'^[0-9a-f]{32}$')]
Coord = Annotated[float, Field(ge=-_BIG, le=_BIG)]
Vec3 = Annotated[tuple[Coord, ...], Field(min_length=3, max_length=3)]
Box6 = Annotated[tuple[Coord, ...], Field(min_length=6, max_length=6)]
NonNeg = Annotated[float, Field(ge=0, le=_BIG)]
Count = Annotated[int, Field(ge=0, le=10_000_000)]


def _bad(message: str):
    raise ValueError(message)


def _box_ok(box, what: str) -> None:
    if any(box[i] > box[i + 3] for i in range(3)):
        _bad(f'{what} minimum exceeds maximum')


def _compare(comparison: str, value: float, limit: float) -> bool:
    if comparison == 'le':
        return value <= limit
    if comparison == 'ge':
        return value >= limit
    if comparison == 'abs_le':
        return abs(value) <= limit
    return value == limit


# --------------------------------------------------------------------------- the check plan

class CheckLimit(IRModel):
    name: Identifier
    value: Annotated[float, Field(ge=-_BIG, le=_BIG)]
    unit: Literal['mm', 'mm2', 'mm3', 'mm4', 'ratio', 'count']
    comparison: Literal['le', 'ge', 'abs_le', 'eq']
    source: Ascii120
    scale_refs: Annotated[tuple[Ascii64, ...], Field(max_length=4)] = ()

    @model_validator(mode='after')
    def _sources(self):
        kind, _, ident = self.source.partition(':')
        if kind == 'reviewed':
            if ident not in REVIEWED_IDS:
                _bad(f'limit source {self.source!r} is not a reviewed numeric requirement')
        elif kind == 'policy':
            if ident not in _POLICY_VALUES:
                _bad(f'limit source {self.source!r} is not a numerical policy field')
            policy = float(_POLICY_VALUES[ident])
            if self.scale_refs:
                if self.value < policy:
                    _bad('a scaled policy limit cannot be below its policy value')
            elif self.value != policy:
                _bad('an unscaled policy limit must equal its policy value')
        else:
            _bad(f'limit source {self.source!r} must be reviewed:<id> or policy:<field>')
        if len(set(self.scale_refs)) != len(self.scale_refs):
            _bad('scale_refs must be unique')
        for ref in self.scale_refs:
            k, _, i = ref.partition(':')
            if k != 'reviewed' or i not in REVIEWED_IDS:
                _bad(f'scale ref {ref!r} must be reviewed:<numeric requirement id>')
        if self.comparison in ('le', 'abs_le') and self.value < 0:
            _bad('an upper limit cannot be negative')
        return self


class CheckEntry(IRModel):
    check_id: SemanticId
    category: Literal['geometry', 'preview', 'physics', 'manufacturing', 'standards']
    required: bool
    requirement_refs: Annotated[tuple[Ascii64, ...], Field(max_length=8)] = ()
    feature_refs: Annotated[tuple[Ascii64, ...], Field(max_length=3)] = ()
    predicate: Literal[PREDICATES]
    required_representation: Representation
    limits: Annotated[tuple[CheckLimit, ...], Field(max_length=8)] = ()

    @model_validator(mode='after')
    def _rules(self):
        if any(r not in REVIEWED_IDS for r in self.requirement_refs):
            _bad('requirement_refs must be reviewed numeric requirement ids')
        if any(f not in BEAM_FEATURES for f in self.feature_refs):
            _bad('feature_refs must be beam features')
        if len({lim.name for lim in self.limits}) != len(self.limits):
            _bad('limit names must be unique within an entry')
        if self.category in ('manufacturing', 'standards'):
            if self.required or self.predicate != 'not_evaluated' or self.limits or self.feature_refs:
                _bad('manufacturing and standards entries are optional, not evaluated and carry no limits')
        elif self.predicate == 'not_evaluated':
            _bad('only manufacturing and standards entries use the not_evaluated predicate')
        if self.category == 'preview' and self.required_representation != 'mesh':
            _bad('a preview entry needs the mesh representation')
        if self.category in ('geometry', 'physics') and self.required_representation != 'brep':
            _bad('geometry and physics entries need the brep representation')
        if self.category == 'physics' and self.predicate != 'physics_report_inputs':
            _bad('a physics entry uses the physics_report_inputs predicate')
        return self


def preview_can_pass(tolerance: float, extents: tuple[float, ...]) -> bool:
    """Whether the conservative per-axis rule can ever pass: tolerance >= deflection + float32 bound."""
    return all(tolerance >= GLB_LINEAR_DEFLECTION_MM + GLB_F32_REL * abs(r) for r in extents)


def _entry_preview_can_pass(entry: 'CheckEntry') -> bool:
    lims = {lim.name: lim.value for lim in entry.limits}
    if not all(k in lims for k in ('extent', 'length', 'width', 'height')):
        return False
    return preview_can_pass(lims['extent'], (lims['length'], lims['width'], lims['height']))


class CandidateChecks(IRModel):
    candidate_sha256: Hash64
    entries: Annotated[tuple[CheckEntry, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode='after')
    def _unique(self):
        ids = [e.check_id for e in self.entries]
        if len(set(ids)) != len(ids):
            _bad('check ids must be unique within a candidate')
        return self


BEAM_CHECK_IDS = (
    'delivered.solid', 'delivered.root_section', 'delivered.free_end', 'delivered.length',
    'delivered.width', 'delivered.height', 'delivered.datum', 'delivered.volume',
    'delivered.preview_extent', 'physics.report_inputs', 'manufacturing.not_evaluated',
    'standards.not_evaluated')


class CheckPlan(IRModel):
    schema_version: Literal['mirum_check_plan_v0_1'] = CHECK_PLAN_VERSION
    capability_id: Literal['rectangular_cantilever']
    capability_version: Literal['1.0.0']
    claim_scope: Literal['checked_design']
    semantic_sha256: Hash64
    lock_sha256: Hash64
    brief_sha256: Hash64
    fixed_requirements_sha256: Hash64
    mesh_convention_pinned: bool
    candidates: Annotated[tuple[CandidateChecks, ...], Field(min_length=1, max_length=MAX_CANDIDATES)]

    @model_validator(mode='after')
    def _rules(self):
        hashes = [c.candidate_sha256 for c in self.candidates]
        if len(set(hashes)) != len(hashes):
            _bad('candidate_sha256 values must be unique')
        for cand in self.candidates:
            if tuple(sorted(e.check_id for e in cand.entries)) != tuple(sorted(BEAM_CHECK_IDS)):
                _bad('every candidate needs exactly the beam check set')
            for e in cand.entries:
                if e.category in ('geometry', 'physics') and not e.required:
                    _bad(f'{e.check_id} must be required')
                if e.category == 'preview' and e.required != (
                        self.mesh_convention_pinned and _entry_preview_can_pass(e)):
                    _bad('the preview check is required exactly when the mesh convention is pinned '
                         'and the conservative rule can pass for the reviewed tolerance')
        return self


def _reviewed_values(ir: ProgramIR) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in ir.requirements.requirements:
        if isinstance(r.value, QuantityValue):
            out[r.id] = r.value.value
    return out


def _agree(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=0.0)


def _metric_tol(product: float) -> float:
    policy = CAPABILITY.numerical
    return max(policy.metric_absolute_error, policy.metric_relative_error * product)


def _lim(name: str, value: float, unit: str, comparison: str, source: str,
         scale_refs: tuple[str, ...] = ()) -> CheckLimit:
    return CheckLimit(name=name, value=float(value), unit=unit, comparison=comparison, source=source,
                      scale_refs=scale_refs)


def _candidate_entries(spec, pinned: bool) -> tuple[CheckEntry, ...]:
    pol = CAPABILITY.numerical
    length, width, height = spec.length_mm, spec.width_mm, spec.height_mm
    dim_tol = spec.dimensional_tolerance_mm
    section = ('reviewed:beam.width', 'reviewed:beam.height')
    box = ('reviewed:beam.length', 'reviewed:beam.width', 'reviewed:beam.height')
    area_tol = _metric_tol(width * height)
    vol_tol = _metric_tol(length * width * height)
    iy = width * height ** 3 / 12.0
    iy_tol = _metric_tol(iy)
    tol_src = 'reviewed:beam.dimensional_tolerance'
    normal = _lim('normal_error', pol.face_normal_error, 'ratio', 'le', 'policy:face_normal_error')
    datum = _lim('datum_offset', pol.datum_budget_mm, 'mm', 'abs_le', 'policy:datum_budget_mm')
    area = _lim('area', area_tol, 'mm2', 'abs_le', 'policy:metric_absolute_error', section)
    guard = _lim('guard_multiplier', pol.dimension_guard_topology_multiplier, 'ratio', 'eq',
                 'policy:dimension_guard_topology_multiplier')

    def entry(cid, cat, predicate, limits, *, features=(), reqs=(), required=True, rep='brep'):
        return CheckEntry(check_id=cid, category=cat, required=required, requirement_refs=reqs,
                          feature_refs=features, predicate=predicate, required_representation=rep,
                          limits=limits)

    def dim(cid, predicate, req, value):
        return entry(cid, 'geometry', predicate,
                     (_lim('dimension', dim_tol, 'mm', 'le', tol_src), guard,
                      _lim('reviewed', value, 'mm', 'eq', f'reviewed:{req}')),
                     features=('beam.solid',), reqs=(req, 'beam.dimensional_tolerance'))

    rev_w = _lim('width', width, 'mm', 'eq', 'reviewed:beam.width')
    rev_h = _lim('height', height, 'mm', 'eq', 'reviewed:beam.height')
    rev_l = _lim('length', length, 'mm', 'eq', 'reviewed:beam.length')
    pinned_required = pinned and preview_can_pass(dim_tol, (length, width, height))

    return (
        entry('delivered.solid', 'geometry', 'solid_single_closed',
              (_lim('topology_tolerance', pol.max_topology_tolerance_mm, 'mm', 'le',
                    'policy:max_topology_tolerance_mm'),), features=('beam.solid',)),
        entry('delivered.root_section', 'geometry', 'root_section_face', (normal, datum, area, rev_w, rev_h),
              features=('beam.root_section',), reqs=('beam.width', 'beam.height')),
        entry('delivered.free_end', 'geometry', 'free_end_face',
              (normal, _lim('position', dim_tol, 'mm', 'le', tol_src), guard, area, rev_l, rev_w,
               rev_h),
              features=('beam.free_end',),
              reqs=('beam.length', 'beam.width', 'beam.height', 'beam.dimensional_tolerance')),
        dim('delivered.length', 'dimension_length', 'beam.length', length),
        dim('delivered.width', 'dimension_width', 'beam.width', width),
        dim('delivered.height', 'dimension_height', HEIGHT_ID, height),
        entry('delivered.datum', 'geometry', 'datum_origin', (datum,), features=('beam.solid',)),
        entry('delivered.volume', 'geometry', 'prism_volume',
              (_lim('volume', vol_tol, 'mm3', 'abs_le', 'policy:metric_absolute_error', box),
               _lim('reviewed_volume', length * width * height, 'mm3', 'eq', 'reviewed:beam.length',
                    ('reviewed:beam.width', 'reviewed:beam.height'))),
              features=('beam.solid',), reqs=('beam.length', 'beam.width', HEIGHT_ID)),
        entry('delivered.preview_extent', 'preview', 'preview_extent',
              (_lim('extent', dim_tol, 'mm', 'le', tol_src), rev_l, rev_w, rev_h),
              features=('beam.solid',),
              reqs=('beam.length', 'beam.width', HEIGHT_ID, 'beam.dimensional_tolerance'),
              required=pinned_required, rep='mesh'),
        entry('physics.report_inputs', 'physics', 'physics_report_inputs',
              (_lim('section_iy', iy_tol, 'mm4', 'abs_le', 'policy:metric_absolute_error', section),
               _lim('length', _metric_tol(length), 'mm', 'abs_le', 'policy:metric_absolute_error',
                    ('reviewed:beam.length',)),
               _lim('height', _metric_tol(height), 'mm', 'abs_le', 'policy:metric_absolute_error',
                    ('reviewed:beam.height',))),
              reqs=('beam.length', 'beam.width', HEIGHT_ID)),
        entry('manufacturing.not_evaluated', 'manufacturing', 'not_evaluated', (), required=False),
        entry('standards.not_evaluated', 'standards', 'not_evaluated', (), required=False),
    )


def build_check_plan(ir: ProgramIR, lock: ExecutionLockV0_2, candidates: tuple[CandidateSpec, ...], *,
                     mesh_convention_pinned: bool) -> CheckPlan:
    """Beam check plan from reviewed inputs only (D3). Refuses (ProgramError) on any disagreement."""
    if (ir.capability.id, ir.capability.version) != (CAPABILITY.id, CAPABILITY.version):
        raise ProgramError('IR010', 'only the beam capability has a delivered check plan',
                           'capability.id')
    if (lock.capability_id, lock.capability_version, lock.numerical_policy_version) != (
            CAPABILITY.id, CAPABILITY.version, CAPABILITY.numerical.version):
        raise ProgramError('IR010', 'the lock does not pin the beam capability and numerical policy',
                           'lock')
    candidates = tuple(candidates)
    if not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise ProgramError('IR012', f'a check plan covers 1 to {MAX_CANDIDATES} reviewed candidates')
    reviewed = _reviewed_values(ir)
    heights = {p.id: tuple(v.value for v in p.values if isinstance(v, QuantityValue))
               for p in ir.candidates.parameters}.get(HEIGHT_ID, ())
    pairs = (('beam.length', 'length_mm'), ('beam.width', 'width_mm'),
             ('beam.dimensional_tolerance', 'dimensional_tolerance_mm'))
    brief_sha = {c.brief_sha256 for c in candidates}
    fixed_sha = {c.fixed_requirements_sha256 for c in candidates}
    if len(brief_sha) != 1 or len(fixed_sha) != 1:
        raise ProgramError('IR010', 'candidates disagree on the reviewed brief', 'candidates')
    out = []
    for cand in candidates:
        spec = cand.specification
        if cand.candidate_sha256 != specification_digest(spec):
            raise ProgramError('IR010', 'candidate digest does not match its specification',
                               'candidates')
        for rid, attr in pairs:
            if rid not in reviewed or not _agree(reviewed[rid], getattr(spec, attr)):
                raise ProgramError('IR010', f'candidate disagrees with reviewed {rid}', rid)
        if not any(_agree(h, spec.height_mm) for h in heights):
            raise ProgramError('IR010', 'candidate height is not a reviewed height', HEIGHT_ID)
        out.append(CandidateChecks(candidate_sha256=cand.candidate_sha256,
                                   entries=_candidate_entries(spec, mesh_convention_pinned)))
    out.sort(key=lambda c: c.candidate_sha256)
    return CheckPlan(
        capability_id=CAPABILITY.id, capability_version=CAPABILITY.version, claim_scope='checked_design',
        semantic_sha256=semantic_sha256(ir), lock_sha256=execution_lock_v0_2_sha256(lock),
        brief_sha256=brief_sha.pop(), fixed_requirements_sha256=fixed_sha.pop(),
        mesh_convention_pinned=mesh_convention_pinned, candidates=tuple(out))


def _sha(record) -> str:
    return hashlib.sha256(canonical_json(record).encode('utf-8')).hexdigest()


def check_plan_sha256(plan: CheckPlan) -> str:
    return _sha(plan)


# --------------------------------------------------------------------------- delivered inputs

class FaceRecord(IRModel):
    surface: Literal['plane', 'cylinder', 'cone', 'sphere', 'torus', 'bspline', 'other']
    normal: Vec3 | None = None
    center: Vec3
    area: NonNeg
    bounds: Box6
    name: Ascii64 | None = None  # STEP entity name; the binder must ignore it

    @model_validator(mode='after')
    def _rules(self):
        if (self.surface == 'plane') != (self.normal is not None):
            _bad('a plane carries a normal; other surfaces carry none')
        if self.normal is not None and abs(math.sqrt(sum(c * c for c in self.normal)) - 1.0) > 1e-6:
            _bad('a plane normal must have unit length')
        _box_ok(self.bounds, 'face bounds')
        return self


class FaceTable(IRModel):
    solid_count: Annotated[int, Field(ge=0, le=MAX_FACES)]
    closed_solid_count: Annotated[int, Field(ge=0, le=MAX_FACES)]
    bounds: Box6
    volume: NonNeg
    area: NonNeg
    max_topology_tolerance: NonNeg
    native_versions: Annotated[tuple[NativeVersion, ...], Field(max_length=16)]
    faces: Annotated[tuple[FaceRecord, ...], Field(max_length=MAX_FACES)]

    @model_validator(mode='after')
    def _rules(self):
        if self.closed_solid_count > self.solid_count:
            _bad('closed_solid_count exceeds solid_count')
        _box_ok(self.bounds, 'table bounds')
        return self


class MeshSummary(IRModel):
    triangle_count: Count
    vertex_count: Count
    bounds_mm: Box6
    mesh_bound_mm: NonNeg
    node_names: Annotated[tuple[Ascii64, ...], Field(max_length=64)] = ()  # ignored by every check

    @model_validator(mode='after')
    def _rules(self):
        _box_ok(self.bounds_mm, 'mesh bounds')
        return self


class FaceSignature(IRModel):
    normal: Vec3
    center: Vec3
    area: NonNeg


class FeatureBinding(IRModel):
    feature: Literal['beam.solid', 'beam.root_section', 'beam.free_end']
    state: Literal['bound', 'missing', 'ambiguous']
    match_count: Annotated[int, Field(ge=0, le=MAX_FACES)]
    signature: FaceSignature | None = None

    @model_validator(mode='after')
    def _rules(self):
        want = {'bound': lambda n: n == 1, 'missing': lambda n: n == 0, 'ambiguous': lambda n: n >= 2}
        if not want[self.state](self.match_count):
            _bad(f'match_count {self.match_count} does not fit state {self.state}')
        if self.state != 'bound' and self.signature is not None:
            _bad('only a bound feature carries a signature')
        if self.state == 'bound' and self.feature != 'beam.solid' and self.signature is None:
            _bad('a bound face feature carries its geometric signature')
        return self


# --------------------------------------------------------------------------- evidence

class ErrorBound(IRModel):
    value: NonNeg
    unit: Literal['mm']
    source: Ascii120


class Measurement(IRModel):
    name: Identifier
    value: Annotated[float, Field(ge=-_BIG, le=_BIG)]
    unit: Literal['mm', 'mm2', 'mm3', 'mm4', 'ratio', 'count']
    representation: Representation
    limit: CheckLimit
    satisfied: bool

    @model_validator(mode='after')
    def _rules(self):
        if self.unit != self.limit.unit:
            _bad('measurement and limit units differ')
        if self.satisfied != _compare(self.limit.comparison, self.value, self.limit.value):
            _bad('satisfied does not follow from the value and the limit')
        return self


class CheckEvidence(IRModel):
    check_id: SemanticId
    category: Literal['geometry', 'preview', 'physics', 'manufacturing', 'standards']
    required: bool
    state: CheckState
    coverage: Coverage
    method: Ascii64
    method_version: Ascii64
    representation: Literal['brep', 'mesh', 'none']
    error_bound: ErrorBound | None = None
    independence: Literal['same_kernel_reread'] = 'same_kernel_reread'
    measurements: Annotated[tuple[Measurement, ...], Field(max_length=16)] = ()
    reason: Literal[REASONS] | None = None

    @model_validator(mode='after')
    def _rules(self):
        cat, state, cov = self.category, self.state, self.coverage
        if cat in ('manufacturing', 'standards'):
            if state != 'not_evaluated' or cov != 'not_evaluated' or self.measurements or self.required:
                _bad('manufacturing and standards are always not_evaluated and never required')
        if cat == 'physics' and cov == 'measured':
            _bad('physics coverage is policy_report or not_evaluated, never measured')
        if cov == 'identity_only' and state == 'passed':
            _bad('identity_only coverage can never pass')
        if state in ('not_evaluated', 'unavailable') and cov != 'not_evaluated':
            _bad('a check that was not evaluated has coverage not_evaluated')
        if state != 'passed' and self.reason is None and cat not in ('manufacturing', 'standards'):
            _bad('a check that did not pass needs a reason')
        if state == 'passed':
            if self.reason is not None:
                _bad('a passed check carries no reason')
            if cat in ('geometry', 'preview'):
                if cov != 'measured':
                    _bad('a passed geometry or preview check needs measured coverage')
                if self.error_bound is None:
                    _bad('a passed geometry or preview check declares its error bound')
            elif cat == 'physics':
                if cov != 'policy_report':
                    _bad('a passed physics check has policy_report coverage')
            if not self.measurements:
                _bad('a passed check needs at least one measurement against a plan limit')
            if self.representation == 'none':
                _bad('a passed check names its representation')
            if any(m.representation != self.representation for m in self.measurements):
                _bad('a measurement of another representation cannot satisfy this check')
            if not all(m.satisfied for m in self.measurements):
                _bad('a passed check needs every measurement satisfied')
        return self


def derive_verdict(checks: tuple[CheckEvidence, ...]) -> str:
    """ambiguous binding on a required check; else any failed check with measured coverage (required or
    not) or any failed required check; else any required check not passed -> unavailable; else verified."""
    required = [c for c in checks if c.required]
    if any(c.reason == 'binding_ambiguous' for c in required):
        return 'ambiguous'
    if any(c.state == 'failed' and (c.required or c.coverage == 'measured') for c in checks):
        return 'failed'
    if not required or any(c.state != 'passed' for c in required):
        return 'unavailable'
    return 'verified'


def _labels(value, found: list) -> None:
    if isinstance(value, str):
        low = value.lower()
        if any(label in low for label in _FORBIDDEN_LABELS):
            found.append(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            _labels(k, found)
            _labels(v, found)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _labels(v, found)


class DeliveredEvidence(IRModel):
    schema_version: Literal['mirum_delivered_evidence_v0_1'] = DELIVERED_EVIDENCE_VERSION
    revision_id: Id32
    job_id: Id32
    candidate_sha256: Hash64
    claim_scope: ClaimScope
    manifest_sha256: Hash64
    step_sha256: Hash64 | None  # None only for a record made before the STEP was leased (never verified)
    glb_sha256: Hash64 | None = None
    check_plan_sha256: Hash64
    check_plan_version: Literal['mirum_check_plan_v0_1'] = CHECK_PLAN_VERSION
    method_version: Ascii64
    native_versions: Annotated[tuple[NativeVersion, ...], Field(max_length=16)]
    bindings: Annotated[tuple[FeatureBinding, ...], Field(max_length=8)] = ()
    checks: Annotated[tuple[CheckEvidence, ...], Field(max_length=32)]
    verdict: Verdict
    reasons: Annotated[tuple[Literal[REASONS], ...], Field(max_length=8)] = ()

    @model_validator(mode='after')
    def _rules(self):
        ids = [c.check_id for c in self.checks]
        if len(set(ids)) != len(ids):
            _bad('check ids must be unique')
        if self.verdict != derive_verdict(self.checks):
            _bad('the verdict is not consistent with the checks')
        if self.verdict == 'verified' and self.reasons:
            _bad('a verified record carries no reasons')
        if self.verdict == 'verified' and (self.step_sha256 is None or UNKNOWN_SHA256 in (
                self.step_sha256, self.manifest_sha256, self.check_plan_sha256, self.candidate_sha256)):
            _bad('a verified record names its STEP, manifest, plan and candidate')
        if 'mock_backend' in self.reasons and (
                self.verdict != 'unavailable' or any(c.state == 'passed' for c in self.checks)):
            _bad('a mock backend yields unavailable evidence that passes nothing')
        if self.claim_scope == 'checked_design':
            if self.verdict == 'verified':
                if {b.feature for b in self.bindings} != set(BEAM_FEATURES) or any(
                        b.state != 'bound' for b in self.bindings):
                    _bad('verified beam evidence needs all three features bound')
        else:
            self._label_rules()
        return self

    def _label_rules(self) -> None:
        """D10: geometry_checks_only and screening_only evidence never carries beam or acceptance labels."""
        if self.bindings:
            _bad('this claim scope carries no beam feature bindings')
        for c in self.checks:
            if c.check_id.startswith(('delivered.', 'beam.')):
                _bad('this claim scope cannot carry a delivered beam check')
            if c.category in ('physics', 'manufacturing', 'standards') and (
                    c.state != 'not_evaluated' or c.coverage != 'not_evaluated'):
                _bad('physics, manufacturing and standards must be not_evaluated for this claim scope')
        found: list = []
        _labels(self.model_dump(mode='json', exclude={'claim_scope', 'schema_version'}), found)
        if found:
            _bad('this claim scope cannot carry beam, checked_design or acceptance labels')


def evidence_sha256(record: DeliveredEvidence) -> str:
    return _sha(record)


def check_evidence_against_plan(evidence: DeliveredEvidence, plan: CheckPlan) -> None:
    """Raise ValueError unless the record's checks and limits are exactly the plan's for its candidate."""
    if evidence.check_plan_sha256 != check_plan_sha256(plan):
        _bad('evidence names another check plan')
    cand = next((c for c in plan.candidates if c.candidate_sha256 == evidence.candidate_sha256), None)
    if cand is None:
        _bad('the candidate is not in the check plan')
    entries = {e.check_id: e for e in cand.entries}
    if {c.check_id for c in evidence.checks} != set(entries):
        _bad('evidence checks differ from the plan entries')
    for c in evidence.checks:
        e = entries[c.check_id]
        if (c.category, c.required) != (e.category, e.required):
            _bad(f'{c.check_id} differs from its plan entry')
        if c.state == 'passed' and c.representation != e.required_representation:
            _bad(f'{c.check_id} passed on another representation than the plan requires')
        for m in c.measurements:
            if m.limit not in e.limits:
                _bad(f'{c.check_id} measured against a limit that is not in the plan')


# --------------------------------------------------------------------------- binder and evaluator

class ReportInputs(IRModel):
    """Physics inputs the report claims; only cross-bound to measured geometry, never a threshold."""
    physics_passed: bool
    section_iy_mm4: NonNeg
    length_mm: NonNeg
    height_mm: NonNeg


_PREDICATE_BY_FEATURE = {'beam.solid': 'solid_single_closed', 'beam.root_section': 'root_section_face',
                         'beam.free_end': 'free_end_face'}


def _limits(entry: CheckEntry) -> dict[str, CheckLimit]:
    return {lim.name: lim for lim in entry.limits}


def _entry_for(entries, feature: str) -> CheckEntry:
    found = [e for e in entries if e.predicate == _PREDICATE_BY_FEATURE[feature]]
    if len(found) != 1:
        raise ValueError(f'the plan needs exactly one entry for {feature}')
    return found[0]


def _ok(lim: CheckLimit, value: float) -> bool:
    return _compare(lim.comparison, value, lim.value)


def _normal_error(normal, expected) -> float:
    return math.hypot(normal[0] - expected[0], normal[1] - expected[1], normal[2] - expected[2])


def _root_matches(table: FaceTable, entry: CheckEntry) -> list[FaceRecord]:
    lims = _limits(entry)
    ref = lims['width'].value * lims['height'].value
    return [f for f in table.faces
            if f.surface == 'plane' and f.normal is not None
            and _ok(lims['normal_error'], _normal_error(f.normal, (-1.0, 0.0, 0.0)))
            and _ok(lims['datum_offset'], f.center[0])
            and _ok(lims['area'], f.area - ref)]


def _free_position(face_cx: float, lims, tolerance: float) -> float:
    return abs(face_cx - lims['length'].value) + lims['guard_multiplier'].value * tolerance


def _free_matches(table: FaceTable, entry: CheckEntry) -> list[FaceRecord]:
    lims = _limits(entry)
    ref = lims['width'].value * lims['height'].value
    return [f for f in table.faces
            if f.surface == 'plane' and f.normal is not None
            and _ok(lims['normal_error'], _normal_error(f.normal, (1.0, 0.0, 0.0)))
            and _ok(lims['position'], _free_position(f.center[0], lims, table.max_topology_tolerance))
            and _ok(lims['area'], f.area - ref)]


def _binding(feature: str, matches: list[FaceRecord]) -> FeatureBinding:
    n = len(matches)
    state = 'bound' if n == 1 else ('missing' if n == 0 else 'ambiguous')
    sig = None
    if n == 1:
        f = matches[0]
        sig = FaceSignature(normal=f.normal, center=f.center, area=f.area)
    return FeatureBinding(feature=feature, state=state, match_count=n, signature=sig)


def bind_features(table: FaceTable, entries) -> tuple[FeatureBinding, ...]:
    """Bind the three beam features by geometry only (D4). Face order, indices and names are never read.

    Dimensions and tolerances come from the plan entry limits, never from the table.
    """
    entries = tuple(entries)
    if table.solid_count == 1 and table.closed_solid_count == 1:
        solid = FeatureBinding(feature='beam.solid', state='bound', match_count=1)
    elif table.solid_count >= 2:
        solid = FeatureBinding(feature='beam.solid', state='ambiguous', match_count=table.solid_count)
    else:
        solid = FeatureBinding(feature='beam.solid', state='missing', match_count=0)
    root = _binding('beam.root_section', _root_matches(table, _entry_for(entries, 'beam.root_section')))
    free = _binding('beam.free_end', _free_matches(table, _entry_for(entries, 'beam.free_end')))
    return (solid, root, free)


def _meas(name: str, value: float, lim: CheckLimit, rep: str) -> Measurement:
    return Measurement(name=name, value=value, unit=lim.unit, representation=rep, limit=lim,
                       satisfied=_ok(lim, value))


def _versions_match(a, b) -> bool:
    return {(v.name, v.version) for v in a} == {(v.name, v.version) for v in b}


def _ev(entry: CheckEntry, state: str, coverage: str, method: str, method_version: str, *,
        representation: str = 'none', reason: str | None = None, measurements=(),
        bound=None) -> CheckEvidence:
    return CheckEvidence(check_id=entry.check_id, category=entry.category, required=entry.required,
                         state=state, coverage=coverage, method=method, method_version=method_version,
                         representation=representation, error_bound=bound, measurements=measurements,
                         reason=reason)


def _geometry_measurements(entry: CheckEntry, table: FaceTable, binds: dict[str, FeatureBinding]):
    lims = _limits(entry)
    pred = entry.predicate
    tol = table.max_topology_tolerance
    lo, hi = table.bounds[:3], table.bounds[3:]
    ext = {'x': hi[0] - lo[0], 'y': hi[1] - lo[1], 'z': hi[2] - lo[2]}
    if pred == 'solid_single_closed':
        return (_meas('topology_tolerance', tol, lims['topology_tolerance'], 'brep'),)
    if pred in ('root_section_face', 'free_end_face'):
        root = pred == 'root_section_face'
        sig = binds['beam.root_section' if root else 'beam.free_end'].signature
        ref = lims['width'].value * lims['height'].value
        expected = (-1.0, 0.0, 0.0) if root else (1.0, 0.0, 0.0)
        out = [_meas('normal_error', _normal_error(sig.normal, expected), lims['normal_error'], 'brep')]
        if root:
            out.append(_meas('datum_offset', sig.center[0], lims['datum_offset'], 'brep'))
        else:
            out.append(_meas('position', _free_position(sig.center[0], lims, tol), lims['position'],
                             'brep'))
        out.append(_meas('area_error', sig.area - ref, lims['area'], 'brep'))
        return tuple(out)
    if pred in ('dimension_length', 'dimension_width', 'dimension_height'):
        axis = {'dimension_length': 'x', 'dimension_width': 'y', 'dimension_height': 'z'}[pred]
        value = abs(ext[axis] - lims['reviewed'].value) + lims['guard_multiplier'].value * tol
        return (_meas(f'dimension_{axis}', value, lims['dimension'], 'brep'),)
    if pred == 'datum_origin':
        return tuple(_meas(f'datum_{a}', lo[i], lims['datum_offset'], 'brep') for i, a in enumerate('xyz'))
    if pred == 'prism_volume':
        return (_meas('volume_error', table.volume - lims['reviewed_volume'].value, lims['volume'],
                      'brep'),)
    raise ValueError(f'unsupported geometry predicate {pred}')


def _preview_evidence(entry: CheckEntry, mesh: MeshSummary | None, pinned: bool, mv: str) -> CheckEvidence:
    method = 'glb_bounds'
    if not pinned:
        return _ev(entry, 'not_evaluated', 'not_evaluated', method, mv, reason='not_pinned')
    if mesh is None:
        state = 'unavailable' if entry.required else 'not_evaluated'
        return _ev(entry, state, 'not_evaluated', method, mv, reason='measurement_unavailable')
    deflection = max(GLB_LINEAR_DEFLECTION_MM, mesh.mesh_bound_mm)
    bound = ErrorBound(value=deflection, unit='mm', source='glb_linear_deflection')
    if mesh.triangle_count == 0:
        return _ev(entry, 'failed', 'measured', method, mv, representation='mesh', reason='limit_exceeded',
                   bound=bound)
    lims = _limits(entry)
    tol = lims['extent']
    meas, verdicts = [], []
    for i, axis in enumerate('xyz'):
        ref = lims[('length', 'width', 'height')[i]].value
        lo, hi = mesh.bounds_mm[i], mesh.bounds_mm[i + 3]
        m = hi - lo
        f = GLB_F32_REL * max(abs(lo), abs(hi))
        # the true extent lies in [m - f, m + 2 * deflection + f]
        excess = max(m - f - ref, ref - (m + 2 * deflection + f))
        conservative = max(ref - (m - f), (m + 2 * deflection + f) - ref)
        meas.append(_meas(f'extent_excess_{axis}', excess, tol, 'mesh'))
        meas.append(_meas(f'extent_conservative_{axis}', conservative, tol, 'mesh'))
        verdicts.append('failed' if excess > tol.value else (
            'passed' if conservative <= tol.value else 'open'))
    if 'failed' in verdicts:
        return _ev(entry, 'failed', 'measured', method, mv, representation='mesh', reason='limit_exceeded',
                   measurements=tuple(meas), bound=bound)
    if all(v == 'passed' for v in verdicts):
        return _ev(entry, 'passed', 'measured', method, mv, representation='mesh',
                   measurements=tuple(meas), bound=bound)
    return _ev(entry, 'not_evaluated', 'not_evaluated', method, mv, reason='bound_exceeds_tolerance')


def _physics_evidence(entry: CheckEntry, table: FaceTable, report: ReportInputs | None, mv: str,
                      solid: FeatureBinding) -> CheckEvidence:
    method = 'report_cross_bind'
    if report is None:
        return _ev(entry, 'unavailable', 'not_evaluated', method, mv, reason='measurement_unavailable')
    if solid.state != 'bound':
        return _ev(entry, 'failed', 'not_evaluated', method, mv,
                   reason='binding_missing' if solid.state == 'missing' else 'binding_ambiguous')
    lims = _limits(entry)
    lo, hi = table.bounds[:3], table.bounds[3:]
    length, width, height = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    iy = width * height ** 3 / 12.0
    meas = (_meas('section_iy_error', report.section_iy_mm4 - iy, lims['section_iy'], 'brep'),
            _meas('length_error', report.length_mm - length, lims['length'], 'brep'),
            _meas('height_error', report.height_mm - height, lims['height'], 'brep'))
    ok = report.physics_passed and all(m.satisfied for m in meas)
    return _ev(entry, 'passed' if ok else 'failed', 'policy_report', method, mv, representation='brep',
               reason=None if ok else 'report_mismatch', measurements=meas)


def evaluate(plan: CheckPlan, candidate_sha256: str, table: FaceTable, mesh: MeshSummary | None,
             report_inputs: ReportInputs | None, lock_native_versions, *,
             method_version: str) -> tuple[CheckEvidence, ...]:
    """Pure evaluation of one reviewed candidate's plan entries against delivered measurements (D6)."""
    cand = next((c for c in plan.candidates if c.candidate_sha256 == candidate_sha256), None)
    if cand is None:
        raise ValueError('the candidate is not in the check plan')
    mv = method_version
    binds = {b.feature: b for b in bind_features(table, cand.entries)}
    mismatch = not _versions_match(table.native_versions, lock_native_versions)
    bound = ErrorBound(value=table.max_topology_tolerance, unit='mm', source='occt_topology_tolerance')
    out = []
    for e in cand.entries:
        if e.category in ('manufacturing', 'standards'):
            out.append(_ev(e, 'not_evaluated', 'not_evaluated', 'none', mv))
        elif mismatch:
            out.append(_ev(e, 'unavailable', 'not_evaluated', 'native_versions', mv,
                           reason='lock_mismatch'))
        elif e.category == 'preview':
            out.append(_preview_evidence(e, mesh, plan.mesh_convention_pinned, mv))
        elif e.category == 'physics':
            out.append(_physics_evidence(e, table, report_inputs, mv, binds['beam.solid']))
        else:
            bad = [binds[f] for f in e.feature_refs if binds[f].state != 'bound']
            if bad:
                amb = any(b.state == 'ambiguous' for b in bad)
                out.append(_ev(e, 'failed', 'not_evaluated', 'brep_face_table', mv,
                               reason='binding_ambiguous' if amb else 'binding_missing'))
                continue
            ms = _geometry_measurements(e, table, binds)
            ok = bool(ms) and all(m.satisfied for m in ms)
            out.append(_ev(e, 'passed' if ok else 'failed', 'measured', 'brep_face_table', mv,
                           representation='brep', reason=None if ok else 'limit_exceeded',
                           measurements=ms, bound=bound))
    return tuple(out)


def build_delivered_evidence(plan: CheckPlan, candidate_sha256: str, table: FaceTable,
                             mesh: MeshSummary | None, report_inputs: ReportInputs | None,
                             lock_native_versions, *, revision_id: str, job_id: str, manifest_sha256: str,
                             step_sha256: str, glb_sha256: str | None,
                             method_version: str) -> DeliveredEvidence:
    """Bind, evaluate and assemble a validated checked_design DeliveredEvidence record."""
    cand = next((c for c in plan.candidates if c.candidate_sha256 == candidate_sha256), None)
    if cand is None:
        raise ValueError('the candidate is not in the check plan')
    bindings = bind_features(table, cand.entries)
    checks = evaluate(plan, candidate_sha256, table, mesh, report_inputs, lock_native_versions,
                      method_version=method_version)
    verdict = derive_verdict(checks)
    reasons: tuple = ()
    if verdict != 'verified':
        found = {c.reason for c in checks if c.reason and c.state != 'passed' and (
            c.required or c.state == 'failed')}
        reasons = tuple(r for r in REASONS if r in found)[:8]
    return DeliveredEvidence(
        revision_id=revision_id, job_id=job_id, candidate_sha256=candidate_sha256,
        claim_scope='checked_design', manifest_sha256=manifest_sha256, step_sha256=step_sha256,
        glb_sha256=glb_sha256, check_plan_sha256=check_plan_sha256(plan), method_version=method_version,
        native_versions=table.native_versions, bindings=bindings, checks=checks, verdict=verdict,
        reasons=reasons)
