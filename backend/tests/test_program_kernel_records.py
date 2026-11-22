"""F03 kernel records: strictness, validators, reason allowlists, carriers, backend lock."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from engineering.contracts import CandidateSpec
from engineering.program.kernel import (
    PRECISION_REASONS, UNAVAILABLE_REASONS, UNSUPPORTED_REASONS, Artifact, ArtifactRef,
    BackendLock, BuildPlan, BuildResult, Built, ExecutionContext, ExportResult, InspectResult,
    Measurement, NativeVersion, ProbeResult, SupportDecision, ToleranceConvention, backend_lock,
    make_description, plan_for,
)
from engineering.program.registry import (
    ArtifactGraph, ArtifactNode, OpStream, OpStreamEntry,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'engineering'
H = 'a' * 64


def beam_candidate():
    return CandidateSpec.model_validate_json((FIXTURES / 'candidate-15mm.json').read_text())


def stream():
    return OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='capability.build', operation_version='0.1.0'),))


def graph(node_id='beam.solid', kind='solid', st=None):
    return ArtifactGraph(schema_version='mirum_artifact_graph_v0_1', op_stream=st or stream(),
                         nodes=(ArtifactNode(id=node_id, kind=kind, op_seq=0),))


def ref(node_id='beam.solid'):
    return ArtifactRef(name='model.step', format='step', representation='brep', sha256=H, size=3,
                       node_id=node_id)


def result(**kw):
    base = dict(backend='occt', adapter_version='x-1', mode='native', capability_id='rectangular_cantilever',
                capability_version='1.0.0', outcome='completed', domain_outcome='accepted',
                claim_scope='checked_design', op_stream=stream(), artifact_graph=graph())
    base.update(kw)
    return BuildResult(**base)


def test_records_are_strict_frozen_and_forbid_extras():
    d = SupportDecision(backend='occt', mode='native', supported=True)
    with pytest.raises(ValidationError):
        d.supported = False
    with pytest.raises(ValidationError):
        SupportDecision(backend='occt', mode='native', supported=True, surprise=1)
    with pytest.raises(ValidationError):
        SupportDecision(backend='occt', mode='native', supported='yes')
    with pytest.raises(ValidationError):
        ToleranceConvention(name='t', value=float('nan'), unit='mm', source='s')
    with pytest.raises(ValidationError):
        SupportDecision(backend='other', mode='native', supported=True)


def test_reason_lists_match_literals_and_fail_closed():
    with pytest.raises(ValidationError):
        SupportDecision(backend='occt', mode='native', supported=False, reasons=('made_up',))
    # an unavailable reason inside an unsupported result is rejected
    with pytest.raises(ValidationError):
        result(outcome='unsupported', reasons=('busy',), domain_outcome=None, claim_scope=None,
               op_stream=None, artifact_graph=None)
    with pytest.raises(ValidationError):
        SupportDecision(backend='occt', mode='native', supported=False, reasons=('busy',))
    with pytest.raises(ValidationError):
        result(outcome='unavailable', reasons=('plan_invalid',), domain_outcome=None,
               claim_scope=None, op_stream=None, artifact_graph=None)
    with pytest.raises(ValidationError):
        result(outcome='unavailable', reasons=('nonsense',), domain_outcome=None, claim_scope=None,
               op_stream=None, artifact_graph=None)
    assert len(UNSUPPORTED_REASONS) == 7 and len(UNAVAILABLE_REASONS) == 8
    assert PRECISION_REASONS == ('measurement_out_of_tolerance', 'reread_mismatch')


def test_probe_validator():
    ProbeResult(backend='occt', mode='native', available=True,
                native_versions=(NativeVersion(name='cadquery', version='2.4.0'),))
    with pytest.raises(ValidationError):
        ProbeResult(backend='occt', mode='native', available=True, reasons=('busy',))
    with pytest.raises(ValidationError):
        ProbeResult(backend='occt', mode='native', available=False)
    with pytest.raises(ValidationError):
        ProbeResult(backend='occt', mode='mock', available=True)
    with pytest.raises(ValidationError):
        ProbeResult(backend='occt', mode='mock', available=False, reasons=('busy',))
    ProbeResult(backend='occt', mode='mock', available=False, reasons=('mock_backend',))


def test_support_decision_validator():
    with pytest.raises(ValidationError):
        SupportDecision(backend='occt', mode='native', supported=False)
    with pytest.raises(ValidationError):
        SupportDecision(backend='occt', mode='native', supported=True, reasons=('plan_invalid',))


def test_execution_context_bounds_and_no_credentials():
    ExecutionContext(deadline_s=3600, max_artifact_bytes=1, workspace='scope')
    for bad in (0, -1, 3600.1):
        with pytest.raises(ValidationError):
            ExecutionContext(deadline_s=bad, max_artifact_bytes=1, workspace='scope')
    with pytest.raises(ValidationError):
        ExecutionContext(deadline_s=1, max_artifact_bytes=0, workspace='scope')
    for extra in ('api_key', 'env', 'token', 'credentials'):
        with pytest.raises(ValidationError):
            ExecutionContext(deadline_s=1, max_artifact_bytes=1, workspace='scope', **{extra: 'x'})


def test_build_plan_exports_bounds_and_unique():
    cand = beam_candidate()
    plan = plan_for('rectangular_cantilever', '1.0.0', 'occt', cand, ('step', 'glb'))
    for exports in ((), ('step', 'step'), ('step', 'glb', 'stl', 'step')):
        with pytest.raises(ValidationError):
            BuildPlan(capability_id='rectangular_cantilever', capability_version='1.0.0',
                      backend='occt', candidate=cand, exports=exports, op_stream=plan.op_stream)


def test_completed_validators():
    ok = result(artifacts=(ref(),), measurements=(
        Measurement(name='beam.bbox_x', value=1.0, unit='mm', source='report'),))
    assert ok.outcome == 'completed'
    with pytest.raises(ValidationError):
        result(reasons=('busy',))
    for missing in ('domain_outcome', 'claim_scope', 'op_stream', 'artifact_graph'):
        with pytest.raises(ValidationError):
            result(**{missing: None})
    other = OpStream(schema_version='mirum_op_stream_v0_1', entries=(
        OpStreamEntry(seq=0, operation='capability.build', operation_version='0.1.0'),
        OpStreamEntry(seq=1, operation='capability.inspect', operation_version='0.1.0')))
    with pytest.raises(ValidationError):
        result(artifact_graph=graph(st=other))
    with pytest.raises(ValidationError):
        result(artifacts=(ref('beam.other'),))
    with pytest.raises(ValidationError):
        result(artifacts=(ref(),), artifact_graph=graph(kind='face'))


def test_planned_validators():
    kw = dict(mode='mock', outcome='planned', domain_outcome=None, claim_scope=None,
              artifact_graph=None)
    assert result(**kw).outcome == 'planned'
    with pytest.raises(ValidationError):
        result(**dict(kw, mode='native'))
    with pytest.raises(ValidationError):
        result(**dict(kw, reasons=('mock_backend',)))
    with pytest.raises(ValidationError):
        result(**dict(kw, domain_outcome='accepted'))
    with pytest.raises(ValidationError):
        result(**dict(kw, claim_scope='checked_design'))
    with pytest.raises(ValidationError):
        result(**dict(kw, artifacts=(ref(),)))
    with pytest.raises(ValidationError):
        result(**dict(kw, measurements=(
            Measurement(name='a', value=1.0, unit='mm', source='report'),)))


def test_unsupported_and_unavailable_validators():
    bare = dict(domain_outcome=None, claim_scope=None, op_stream=None, artifact_graph=None)
    result(outcome='unsupported', reasons=('plan_invalid',), **bare)
    result(outcome='unavailable', reasons=('busy',), **bare)
    for outcome, reason in (('unsupported', 'plan_invalid'), ('unavailable', 'busy')):
        with pytest.raises(ValidationError):
            result(outcome=outcome, reasons=(), **bare)
        with pytest.raises(ValidationError):
            result(outcome=outcome, reasons=(reason,), **dict(bare, domain_outcome='rejected'))
        with pytest.raises(ValidationError):
            result(outcome=outcome, reasons=(reason,), **dict(bare, claim_scope='screening_only'))
        with pytest.raises(ValidationError):
            result(outcome=outcome, reasons=(reason,), artifacts=(ref(),), **bare)
        with pytest.raises(ValidationError):
            result(outcome=outcome, reasons=(reason,), measurements=(
                Measurement(name='a', value=1.0, unit='mm', source='report'),), **bare)


def test_mock_result_cannot_be_completed_or_carry_evidence():
    with pytest.raises(ValidationError):
        result(mode='mock')  # completed
    with pytest.raises(ValidationError):
        result(mode='mock', outcome='unavailable', reasons=('mock_backend',), domain_outcome=None,
               claim_scope=None, op_stream=None, artifact_graph=None)
    for extra in (dict(domain_outcome='accepted'), dict(claim_scope='geometry_checks_only'),
                  dict(artifacts=(ref(),))):
        with pytest.raises(ValidationError):
            result(**dict(dict(mode='mock', outcome='planned', domain_outcome=None,
                               claim_scope=None, artifact_graph=None), **extra))


def test_inspect_result_validators():
    base = dict(backend='occt', mode='native', artifact_sha256=H)
    InspectResult(outcome='consistent', **base)
    InspectResult(outcome='precision_failure', reasons=('reread_mismatch',), **base)
    InspectResult(outcome='unavailable', reasons=('busy',), **base)
    InspectResult(outcome='unsupported', reasons=('plan_invalid',), **base)
    with pytest.raises(ValidationError):
        InspectResult(outcome='consistent', reasons=('busy',), **base)
    with pytest.raises(ValidationError):
        InspectResult(outcome='precision_failure', reasons=('busy',), **base)
    with pytest.raises(ValidationError):
        InspectResult(outcome='unavailable', reasons=('reread_mismatch',), **base)
    with pytest.raises(ValidationError):
        InspectResult(outcome='unsupported', reasons=('busy',), **base)
    with pytest.raises(ValidationError):
        InspectResult(outcome='unavailable', **base)
    with pytest.raises(ValidationError):
        InspectResult(outcome='consistent', backend='occt', mode='mock', artifact_sha256=H)
    with pytest.raises(ValidationError):
        InspectResult(outcome='consistent', backend='occt', mode='native', artifact_sha256='ABC')


def test_export_result_validators():
    ExportResult(backend='occt', mode='native', outcome='exported', format='step', artifact=ref())
    ExportResult(backend='occt', mode='mock', outcome='unsupported', format='step',
                 reasons=('export_not_supported',))
    with pytest.raises(ValidationError):
        ExportResult(backend='occt', mode='native', outcome='exported', format='step')
    with pytest.raises(ValidationError):
        ExportResult(backend='occt', mode='native', outcome='exported', format='glb', artifact=ref())
    with pytest.raises(ValidationError):
        ExportResult(backend='occt', mode='mock', outcome='exported', format='step', artifact=ref())
    with pytest.raises(ValidationError):
        ExportResult(backend='occt', mode='native', outcome='unsupported', format='step')
    with pytest.raises(ValidationError):
        ExportResult(backend='occt', mode='native', outcome='unsupported', format='step',
                     reasons=('busy',))


def test_artifact_ref_rejects_bad_hash_size_and_node_id():
    with pytest.raises(ValidationError):
        ArtifactRef(name='a', format='step', representation='brep', sha256='z' * 64, size=1,
                    node_id='beam.solid')
    with pytest.raises(ValidationError):
        ArtifactRef(name='a', format='step', representation='brep', sha256=H, size=-1,
                    node_id='beam.solid')
    with pytest.raises(ValidationError):
        ArtifactRef(name='a', format='step', representation='brep', sha256=H, size=1,
                    node_id='Beam Solid')


def test_artifact_checks_hash_and_size():
    data = b'abc'
    good = ArtifactRef(name='m', format='step', representation='brep',
                       sha256=hashlib.sha256(data).hexdigest(), size=3, node_id='beam.solid')
    assert Artifact(good, data).data == data
    with pytest.raises(ValueError):
        Artifact(good, b'abd')
    with pytest.raises(ValueError):
        Artifact(good, b'abcd')


def test_built_wraps_evaluation_read_only():
    r = result(mode='mock', outcome='planned', domain_outcome=None, claim_scope=None,
               artifact_graph=None)
    b = Built(r, (), {'k': 1})
    assert b.evaluation['k'] == 1
    with pytest.raises(TypeError):
        b.evaluation['k'] = 2
    assert Built(r).evaluation is None
    with pytest.raises(Exception):
        b.result = r


def test_backend_lock_requires_available_matching_probe():
    desc = make_description('occt', mode='native', adapter_version='occt-1')
    probe = ProbeResult(backend='occt', mode='native', available=True,
                        native_versions=(NativeVersion(name='cadquery', version='2.4.0'),))
    lock = backend_lock(desc, probe)
    assert isinstance(lock, BackendLock)
    assert lock.export_formats == ('step', 'glb') and lock.native_versions[0].name == 'cadquery'
    with pytest.raises(ValueError):
        backend_lock(desc, ProbeResult(backend='occt', mode='native', available=False,
                                       reasons=('runtime_missing',)))
    with pytest.raises(ValueError):
        backend_lock(desc, ProbeResult(backend='picogk', mode='native', available=True))
    mock_desc = make_description('occt', mode='mock', adapter_version='mock-0.1.0')
    mock_probe = ProbeResult(backend='occt', mode='mock', available=False, reasons=('mock_backend',))
    with pytest.raises(ValueError):
        backend_lock(mock_desc, mock_probe)
    with pytest.raises(ValueError):
        backend_lock(mock_desc, probe)


def test_descriptions_declare_per_backend_capabilities():
    occt = make_description('occt', mode='native', adapter_version='v')
    pico = make_description('picogk', mode='native', adapter_version='v')
    assert occt.capabilities == ('rectangular_cantilever@1.0.0',)
    assert pico.capabilities == ('regen_chamber@1.0.0',)
    assert pico.export_formats == ('stl',) and 'step' not in pico.export_formats
    assert pico.representations == ('voxel', 'mesh', 'report')
    json.dumps(occt.model_dump(mode='json'))
