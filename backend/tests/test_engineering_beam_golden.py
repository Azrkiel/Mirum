"""Pins the exact serialized bytes of the beam v1 records.

The beam contract must stay byte-identical while the checked-design core is
generalized for other capabilities. Records are built from pure models and
synthetic evidence (no native CAD, no SQLite store), so this runs on any host.
Each pin is the SHA-256 and length of the exact JSON bytes.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

FIXTURES = Path(__file__).with_name('fixtures') / 'engineering'
A = 'a' * 64
PAYLOAD_PATHS = ('brief.json', 'selected_spec.json', 'model.py', 'model.step',
                 'preview.glb', 'attempts.json', 'report.json', 'report.md')

PINS = {
    'draft': (2371, 'e9dcad10fbf27224ff46c75a2aedbab7459868ac33816725799ea45bf3a23f3a'),
    'brief': (3024, 'ed960989d80cdd9f498543931a1b4ce6e6a3e1554d86062a722a510272fb85ee'),
    'attempt': (13777, 'e68f33a80a2eb6fa94491edae6ef4419b2b8ae12d9cac1c05262f08eb13d93c5'),
    'run': (15550, '832f7be25a2f9780bc4d2c1406f3fe79aa614ebe38cbc3e8b97c28a52a637785'),
    'manifest': (1783, '6c367da2dd3fb869b54fd6dd5534dd0839e4c6bd5399984d31cbde0db3d51c72'),
    'capability': (3486, '274022396dbaf425df99abbe10f58cfb9805b54d229380dedd7662f9f6190241'),
}


def _draft():
    from engineering.contracts import BriefDraft
    return BriefDraft.model_validate_json((FIXTURES / 'brief-draft.json').read_text())


def _brief():
    from engineering.contracts import EngineeringBrief, REVIEW_FIELDS, ReviewAcknowledgement
    d = _draft()
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id=d.draft_id, draft_revision=d.revision, brief_sha256=d.content.brief_sha256,
        acknowledged_fields=REVIEW_FIELDS, reviewed_at='2026-11-12T12:05:00Z',
        reviewed_by='local-user')))
    return EngineeringBrief.from_draft(d, review, brief_id='beam-brief-1')


def _attempt():
    from engineering.capabilities import CAPABILITY
    from engineering.contracts import (ArtifactIdentity, AttemptRecord, CandidateSpec, CheckResult,
                                       CheckState, ComparisonLimit, Measurement, Outcome)
    brief = _brief()
    candidate = CandidateSpec.from_brief(brief, 5.0)
    checks = tuple(CheckResult(
        check_id=name, state=CheckState.PASSED, method='golden_fixture', method_version='1',
        brief_sha256=candidate.brief_sha256, candidate_sha256=candidate.candidate_sha256,
        measurements=(Measurement(name='value', value=1.0, unit='count'),),
        limits=(ComparisonLimit(name='value', value=2.0, unit='count', comparison='le'),),
        artifacts=(ArtifactIdentity(path='model.step', sha256=A, bytes=10),))
        for name in CAPABILITY.required_checks)
    return AttemptRecord(ordinal=0, candidate=candidate, outcome=Outcome.ACCEPTED, checks=checks)


def _run():
    from engineering.contracts import (ArtifactIdentity, BundleIdentity, EngineeringRun, Lifecycle,
                                       Outcome)
    brief, attempt = _brief(), _attempt()
    payloads = tuple(ArtifactIdentity(path=p, sha256=A if p == 'model.step' else 'b' * 64, bytes=10)
                     for p in PAYLOAD_PATHS)
    bundle = BundleIdentity(payloads=payloads, manifest_sha256='c' * 64, manifest_bytes=100,
                            zip_sha256='d' * 64, zip_bytes=1000, stored_bytes=2000000)
    when = datetime(2026, 11, 12, 12, 10, tzinfo=timezone.utc)
    return EngineeringRun(
        schema_version='engineering_run_v1', job_id='beam-job-1', capability=brief.content.capability,
        brief_sha256=brief.brief_sha256, fixed_requirements_sha256=brief.fixed_requirements_sha256,
        lifecycle=Lifecycle.COMPLETED, outcome=Outcome.ACCEPTED, attempts=(attempt,),
        created_at=when, updated_at=when, selected_candidate_sha256=attempt.candidate.candidate_sha256,
        bundle=bundle)


def _manifest():
    from engineering.bundles import BundleManifest, MIME, encode
    brief, attempt = _brief(), _attempt()
    payloads = [dict(path=p, sha256='b' * 64, bytes=10, mime_type=MIME[p]) for p in sorted(PAYLOAD_PATHS)]
    manifest = BundleManifest.model_validate_json(json.dumps(dict(
        schema_version='engineering_manifest_v1', job_id='a' * 32, brief_sha256=brief.brief_sha256,
        selected_candidate_sha256=attempt.candidate.candidate_sha256, kind='accepted',
        payloads=payloads)))
    return encode(manifest.model_dump(mode='json'))


def _capability():
    from engineering.capabilities import CAPABILITY
    # The bytes FastAPI serializes for the beam entry of /capabilities.
    return json.dumps(CAPABILITY.model_dump(mode='json'), ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode()


def _bytes():
    return {
        'draft': _draft().model_dump_json().encode(),
        'brief': _brief().model_dump_json().encode(),
        'attempt': _attempt().model_dump_json().encode(),
        'run': _run().model_dump_json().encode(),
        'manifest': _manifest(),
        'capability': _capability(),
    }


def test_beam_v1_serialized_bytes_are_pinned():
    actual = {name: (len(value), hashlib.sha256(value).hexdigest()) for name, value in _bytes().items()}
    assert actual == PINS


def test_beam_records_round_trip_from_their_own_bytes():
    from engineering.contracts import AttemptRecord, BriefDraft, EngineeringBrief, EngineeringRun
    for model, build in ((BriefDraft, _draft), (EngineeringBrief, _brief),
                         (AttemptRecord, _attempt), (EngineeringRun, _run)):
        record = build()
        assert model.model_validate_json(record.model_dump_json()) == record
        assert model.model_validate(record.model_dump()) == record


def test_beam_is_the_first_capability_and_keeps_its_descriptor_bytes():
    from engineering.capabilities import CAPABILITY, list_capabilities
    assert list_capabilities()[0] is CAPABILITY
