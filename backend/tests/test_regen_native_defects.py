"""Defect cases through the regen adapter, fed by REAL native builder output.

Skipped unless REGEN_NATIVE=1 and REGEN_BUILDER exists; run inside the picogk-regen image by
.github/workflows/picogk-runtime.yml (--network none). Self-contained: imports only ``engineering``
(the image has /app and /app/backend on the path, not ``backend.tests``).

Every defect case sits beside ``test_control_*``, which feeds the same real artifacts through the
same code path and must be accepted, so a broken fixture cannot make a defect case pass vacuously.
"""
import copy
import hashlib
import json
import os
import struct
from pathlib import Path

import pytest

from engineering import regen_adapter, regen_cli
from engineering.capabilities import REGEN, UnsupportedCapability

BUILDER = Path(os.environ.get('REGEN_BUILDER', '/opt/regen/app/builder'))
EXAMPLES = Path(__file__).resolve().parents[2] / 'picogk-builder' / 'examples'

pytestmark = pytest.mark.skipif(
    os.environ.get('REGEN_NATIVE') != '1' or not BUILDER.exists(),
    reason='needs the native PicoGK builder (REGEN_NATIVE=1, REGEN_BUILDER)')


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _candidate(spec_dict):
    """Minimal inline brief -> the single regen candidate (mirrors the contract-test helpers)."""
    from engineering.contracts import (BriefDraft, EngineeringBrief, GeneratorBriefContent, Provenance,
                                       ReviewAcknowledgement)
    descriptor = REGEN
    normalized = descriptor.parse_spec(spec_dict).model_dump(mode='json')
    reference = dict(id=descriptor.id, version=descriptor.version,
                     numerical_policy_version=descriptor.numerical_version,
                     resource_policy_version=descriptor.resources.version)
    gen = GeneratorBriefContent.model_validate_json(json.dumps(dict(
        schema_version='engineering_generator_brief_v1', capability=reference, specification=normalized,
        required_checks=list(descriptor.required_checks(normalized)))))
    provenance = Provenance.model_validate_json(json.dumps(dict(
        record_id='native-record', created_at='2026-11-17T12:00:00Z', input_mode='form',
        field_origins=[dict(field=f, origin='user_supplied', reference='Typed input')
                       for f in gen.review_fields])))
    draft = BriefDraft(draft_id='native-draft', revision=1, content=gen, clarifications=(),
                       provenance=provenance)
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id='native-draft', draft_revision=1, brief_sha256=gen.brief_sha256,
        acknowledged_fields=list(gen.review_fields), reviewed_at='2026-11-17T12:05:00Z',
        reviewed_by='local-user')))
    brief = EngineeringBrief.from_draft(draft, review, brief_id='native-brief')
    candidates = regen_adapter.RegenAdapter().candidates(brief)
    assert len(candidates) == 1
    return candidates[0]


@pytest.fixture(scope='module')
def real(tmp_path_factory):
    """One real native build of examples/small.json, shared by every case in this module."""
    out = tmp_path_factory.mktemp('native_small')
    assert regen_cli.main([str(EXAMPLES / 'small.json'), str(out)]) == regen_cli.EXIT_ACCEPTED
    report_bytes = (out / 'report.json').read_bytes()
    stl = (out / 'cleaned.stl').read_bytes()
    spec_bytes = (out / 'spec.canonical.json').read_bytes()
    assert len(stl) > 84 and spec_bytes
    spec_dict = json.loads((EXAMPLES / 'small.json').read_text())
    return dict(report=json.loads(report_bytes), report_bytes=report_bytes, stl=stl, spec_bytes=spec_bytes,
                candidate=_candidate(spec_dict))


def _bytes(report):
    return (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def _half_mesh_stl(stl: bytes) -> bytes:
    """A well-formed binary STL holding only the first half of the triangles (open surface)."""
    n = struct.unpack('<I', stl[80:84])[0]
    keep = n // 2
    assert keep > 0
    return stl[:80] + struct.pack('<I', keep) + stl[84:84 + 50 * keep]


# --- control ----------------------------------------------------------------------------

def test_control_real_small_run_is_accepted_through_the_adapter(real):
    evaluation = regen_adapter.build_evaluation(
        real['candidate'], real['report'], report_bytes=real['report_bytes'],
        spec_bytes=real['spec_bytes'], stl_bytes=real['stl'])
    assert evaluation['outcome'] == 'accepted' and evaluation['passed'] is True
    assert evaluation['artifacts']
    assert evaluation['artifacts'][regen_adapter.STL] == real['stl']
    assert evaluation['diagnostics'] == {}
    assert all(c.state.value == 'passed' for c in evaluation['checks'])


# --- 1. too many channels is refused before any build -----------------------------------

def test_too_many_channels_is_refused_before_any_build(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('the builder must not run for a refused specification')
    monkeypatch.setattr(regen_cli, 'run_builder', forbidden)
    # control: the supported spec passes the same gate
    assert regen_adapter.revalidate(json.loads((EXAMPLES / 'small.json').read_text())) is not None
    over = json.loads((EXAMPLES / 'too_many_channels.json').read_text())
    assert over['channel_count'] > json.loads((EXAMPLES / 'small.json').read_text())['channel_count']
    with pytest.raises(UnsupportedCapability):
        regen_adapter.revalidate(over)


# --- 3. truncated cleaned STL -----------------------------------------------------------

def test_truncated_cleaned_stl_is_unavailable_with_no_package(real):
    cut = real['stl'][:len(real['stl']) // 2]
    assert 0 < len(cut) < len(real['stl'])
    with pytest.raises(regen_adapter.RegenUnavailable):
        regen_adapter.build_evaluation(
            real['candidate'], real['report'], report_bytes=real['report_bytes'],
            spec_bytes=real['spec_bytes'], stl_bytes=cut)


def test_truncated_mesh_run_natively_is_rejected_with_diagnostics_only(real, tmp_path):
    half = tmp_path / 'half.stl'
    half.write_bytes(_half_mesh_stl(real['stl']))
    out = tmp_path / 'o'
    code = regen_cli.main([str(EXAMPLES / 'small.json'), str(out), '--prebuilt-stl', str(half)])
    assert code == regen_cli.EXIT_REJECTED
    assert not (out / 'report.json').exists() and not (out / 'cleaned.stl').exists()
    rejected = out / 'rejected_report.json'
    assert rejected.is_file()
    report_bytes = rejected.read_bytes()
    evaluation = regen_adapter.build_evaluation(
        real['candidate'], json.loads(report_bytes), report_bytes=report_bytes,
        spec_bytes=real['spec_bytes'])
    assert evaluation['outcome'] == 'rejected' and evaluation['passed'] is False
    assert evaluation['artifacts'] == {}
    assert set(evaluation['diagnostics']) == {regen_adapter.REPORT, regen_adapter.SPEC}
    assert any(c.state.value == 'failed' for c in evaluation['checks'])


# --- 4. dropped check -------------------------------------------------------------------

def test_report_with_a_dropped_check_is_unavailable(real):
    report = copy.deepcopy(real['report'])
    assert 'rib' in report['checks']
    del report['checks']['rib']
    # the bytes are rewritten consistently, so only the missing check is wrong
    with pytest.raises(regen_adapter.RegenUnavailable):
        regen_adapter.build_evaluation(
            real['candidate'], report, report_bytes=_bytes(report),
            spec_bytes=real['spec_bytes'], stl_bytes=real['stl'])
