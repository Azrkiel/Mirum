"""Generator-family contracts, policy, refusal and registry hygiene.

Everything here is pure (no native CAD, no SQLite store) except the one package
publication test, which needs the Linux store and runs in CI only.
"""
import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

BACKEND = str(Path(__file__).resolve().parents[1])
SEARCH_DIR = str(Path(__file__).resolve().parents[2])
SHA = 'a' * 64


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def reference(descriptor):
    return dict(id=descriptor.id, version=descriptor.version,
                numerical_policy_version=descriptor.numerical_version,
                resource_policy_version=descriptor.resources.version)


def glider_spec(**requirements):
    base = dict(launch=dict(method='drop'), discovery=dict(families=['conventional']))
    base.update(requirements)
    return dict(requirements=base, calibration_version=SHA)


def regen_spec(**changes):
    from engineering.regen_presets import EXAMPLES
    return dict(EXAMPLES['small'], **changes)


def content_payload(capability_id, specification):
    from engineering.capabilities import get_generator
    descriptor = get_generator(capability_id)
    normalized = descriptor.parse_spec(specification).model_dump(mode='json')
    return dict(schema_version='engineering_generator_brief_v1', capability=reference(descriptor),
                specification=normalized, required_checks=list(descriptor.required_checks(normalized)))


def content(capability_id='regen_chamber', specification=None):
    from engineering.contracts import GeneratorBriefContent
    specification = regen_spec() if specification is None else specification
    return GeneratorBriefContent.model_validate_json(json.dumps(content_payload(capability_id, specification)))


def brief_for(generator_content):
    from engineering.contracts import BriefDraft, EngineeringBrief, Provenance, ReviewAcknowledgement
    provenance = Provenance.model_validate_json(json.dumps(dict(
        record_id='gen-record', created_at='2026-11-17T12:00:00Z', input_mode='form',
        field_origins=[dict(field=f, origin='user_supplied', reference='Typed input')
                       for f in generator_content.review_fields])))
    draft = BriefDraft(draft_id='gen-draft', revision=1, content=generator_content,
                       clarifications=(), provenance=provenance)
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id='gen-draft', draft_revision=1, brief_sha256=generator_content.brief_sha256,
        acknowledged_fields=list(generator_content.review_fields),
        reviewed_at='2026-11-17T12:05:00Z', reviewed_by='local-user')))
    return EngineeringBrief.from_draft(draft, review, brief_id='gen-brief')


def candidate_for(brief):
    from engineering.contracts import GeneratorCandidate
    return GeneratorCandidate.from_brief(brief, sha(b'spec:' + brief.brief_sha256.encode()))


def _passing_value(limit):
    return {'le': limit.value / 2, 'ge': limit.value * 2 + 1, 'eq': limit.value, 'abs_le': 0.0}[limit.comparison]


def _failing_value(limit):
    return {'le': limit.value * 2 + 1, 'ge': limit.value - 1, 'eq': limit.value + 1,
            'abs_le': limit.value * 2 + 1}[limit.comparison]


def make_check(candidate, check_id, *, state='passed', artifact_bytes=None, value=None, limits=None):
    """A check whose limits are the descriptor's closed table unless ``limits`` overrides them."""
    from engineering.contracts import (ArtifactIdentity, CheckState, GeneratorCheckResult, Measurement)
    descriptor = candidate.content.descriptor
    primary = (artifact_bytes or {}).get(descriptor.primary_artifact, b'primary')
    passed = state == 'passed'
    if limits is None:
        limits = descriptor.expected_limits(candidate.content.specification)[check_id]
    measurements = tuple(
        Measurement(name=l.name, unit=l.unit, value=(_passing_value(l) if passed else _failing_value(l))
                    if value is None or i else value) for i, l in enumerate(limits))
    return GeneratorCheckResult(
        capability_id=descriptor.id, check_id=check_id, state=CheckState(state),
        method='fixture', method_version='1', brief_sha256=candidate.brief_sha256,
        candidate_sha256=candidate.candidate_sha256, measurements=measurements, limits=tuple(limits),
        artifacts=(ArtifactIdentity(path=descriptor.primary_artifact, sha256=sha(primary), bytes=len(primary)),),
        reason=None if passed else f'{check_id} out of tolerance')


def make_evaluation(candidate, *, bad_checks=None, failed=(), drop=(), duplicate=(), extra=(), tamper=None,
                    report_flag=None):
    """A fake generator evaluation in the shape ``assess_generator`` documents."""
    from engineering.policy import report_digest
    descriptor = candidate.content.descriptor
    ids = [i for i in candidate.content.required_checks if i not in drop]
    ids += list(duplicate) + list(extra)
    rejected = bool(failed)
    artifacts = {} if rejected else {n: f'bytes:{n}'.encode() for n in descriptor.payloads}
    diagnostics = {n: f'bytes:{n}'.encode() for n in descriptor.diagnostic_payloads} if rejected else {}
    stated = {n: sha(v) for n, v in {**artifacts, **diagnostics}.items()}
    checks = tuple(make_check(candidate, i, state='failed' if i in failed else 'passed',
                              artifact_bytes=artifacts, **(bad_checks or {}).get(i, {})) for i in ids)
    report = dict(failures=[c.reason for c in checks if c.reason],
                  measurements={c.check_id: {m.name: m.value for m in c.measurements} for c in checks})
    result = dict(candidate=candidate, checks=checks, outcome='rejected' if rejected else 'accepted',
                  passed=(not rejected) if report_flag is None else report_flag,
                  spec_sha256=candidate.candidate_sha256, report=report, report_sha256=report_digest(report),
                  report_bytes=json.dumps(report).encode(),
                  artifacts=artifacts, diagnostics=diagnostics, stated_digests=stated, extra_digests=())
    if tamper:
        tamper(result)
    return result


class ParsingAdapter:
    """Test-only adapter: parses measurements out of the fake generator report."""
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def measurements(self, report_bytes):
        return json.loads(report_bytes)['measurements']


from engineering import capabilities as _capabilities
from engineering.capabilities import GeneratorDescriptor as _Descriptor
REAL_GET_ADAPTER = _capabilities.get_adapter
REAL_EXPECTED_LIMITS = _Descriptor.expected_limits


@pytest.fixture(autouse=True)
def fake_adapter_and_complete_limit_table(monkeypatch):
    """Register a parsing adapter for the test only, and fill the checks whose real limit
    set an adapter PR has not pinned yet with one synthetic limit (the real table is
    exercised by its own tests through ``REAL_EXPECTED_LIMITS``)."""
    from engineering.contracts import ComparisonLimit

    def complete(self, spec):
        real = REAL_EXPECTED_LIMITS(self, spec)
        filler = (ComparisonLimit(name='value', value=2.0, unit='count', comparison='le'),)
        return {c: real.get(c, filler) for c in self.required_checks(spec)}
    monkeypatch.setattr(_Descriptor, 'expected_limits', complete)
    monkeypatch.setattr(_capabilities, 'get_adapter',
                        lambda cid, version, **p: ParsingAdapter(_capabilities.get_capability(cid, version, **p)))


# --- content ----------------------------------------------------------------

def test_generator_brief_content_is_the_normalized_spec_and_its_identity():
    from cem.core import digest
    c = content()
    assert c.descriptor.id == 'regen_chamber'
    assert c.required_checks == c.descriptor.required_checks(c.specification)
    assert c.fixed_requirements_sha256 == c.brief_sha256 == digest(c.fixed_meaning())
    assert c.review_fields == tuple(sorted(('specification.' + k) for k in c.specification))


def test_generator_content_rejects_incomplete_defaults_wrong_checks_and_unknown_fields():
    from engineering.contracts import GeneratorBriefContent
    payload = content_payload('regen_chamber', regen_spec())
    for broken in (
            dict(payload, required_checks=payload['required_checks'][:-1]),
            dict(payload, required_checks=list(reversed(payload['required_checks']))),
            dict(payload, specification=dict(payload['specification'], surprise=1)),
            dict(payload, specification={k: v for k, v in payload['specification'].items() if k != 'units'}),
            dict(payload, specification=dict(payload['specification'], channel_count=24.0)),
            dict(payload, capability=dict(payload['capability'], version='9.9.9')),
            dict(payload, unexpected=True)):
        with pytest.raises(ValueError):
            GeneratorBriefContent.model_validate_json(json.dumps(broken))


def test_glider_required_checks_follow_the_reviewed_construction():
    screening = content('glider_cem', glider_spec())
    foam = content('glider_cem', glider_spec(construction='foam_core_wing_v1', process='sheet_foam'))
    segmented = content('glider_cem', glider_spec(construction='segmented_foam_wing_v1', process='sheet_foam'))
    assert 'foam_core' not in screening.required_checks and 'segmented_structure' not in screening.required_checks
    assert foam.required_checks == screening.required_checks + ('foam_core',)
    assert segmented.required_checks == screening.required_checks + ('segmented_structure',)


def test_review_fields_are_the_specification_leaves_and_must_all_be_acknowledged():
    from engineering.contracts import EngineeringBrief, ReviewAcknowledgement
    brief = brief_for(content())
    assert set(brief.review.acknowledged_fields) == set(brief.content.review_fields)
    short = brief.review.model_dump(mode='json')
    short['acknowledged_fields'] = short['acknowledged_fields'][:-1]
    with pytest.raises(ValueError):
        EngineeringBrief.model_validate_json(json.dumps(dict(brief.model_dump(mode='json'), review=short)))
    with pytest.raises(ValidationError):
        ReviewAcknowledgement.model_validate_json(json.dumps(dict(
            short, acknowledged_fields=['length_mm', 'not_a_field'])))
    assert EngineeringBrief.model_validate_json(brief.model_dump_json()) == brief


def test_generator_candidate_binds_the_reviewed_brief():
    from engineering.contracts import GeneratorCandidate
    brief = brief_for(content())
    candidate = candidate_for(brief)
    assert GeneratorCandidate.model_validate_json(candidate.model_dump_json()) == candidate
    with pytest.raises(ValueError):
        GeneratorCandidate(**dict(candidate.model_dump(), brief_sha256=SHA))


# --- check results and attempts ------------------------------------------------

def test_generator_check_result_validation():
    from engineering.contracts import (ArtifactIdentity, CheckState, GeneratorCheckResult)
    candidate = candidate_for(brief_for(content()))
    good = make_check(candidate, 'hot_wall')
    base = good.model_dump()
    for changes in (
            dict(check_id='not_a_check'),
            dict(capability_id='nope'),
            dict(measurements=()),
            dict(limits=()),
            dict(artifacts=()),
            dict(artifacts=(ArtifactIdentity(path='model.step', sha256=SHA, bytes=1),)),
            dict(state=CheckState.FAILED, reason=None),
            dict(state=CheckState.UNAVAILABLE, reason=None)):
        with pytest.raises(ValueError):
            GeneratorCheckResult(**dict(base, **changes))
    failed = make_check(candidate, 'hot_wall', state='failed')
    assert failed.state == CheckState.FAILED and failed.reason


def attempt(candidate, checks, outcome='accepted', failures=()):
    from engineering.contracts import AttemptRecord, Outcome
    return AttemptRecord(ordinal=0, candidate=candidate, outcome=Outcome(outcome),
                         checks=tuple(checks), failures=tuple(failures))


def test_acceptance_needs_every_required_check_once_and_passed():
    candidate = candidate_for(brief_for(content()))
    required = candidate.content.required_checks
    full = [make_check(candidate, i) for i in required]
    assert attempt(candidate, full).outcome.value == 'accepted'
    with pytest.raises(ValueError):
        attempt(candidate, full[:-1])
    with pytest.raises(ValueError):
        attempt(candidate, full + [full[0]])
    flipped = full[:-1] + [make_check(candidate, required[-1], state='failed')]
    with pytest.raises(ValueError):
        attempt(candidate, flipped)
    assert attempt(candidate, flipped, 'rejected', ['x']).outcome.value == 'rejected'
    with pytest.raises(ValueError):
        attempt(candidate, full, 'rejected', ['x'])


def test_a_declared_but_not_required_check_is_extra_and_blocks_acceptance():
    glider = candidate_for(brief_for(content('glider_cem', glider_spec())))
    checks = [make_check(glider, i) for i in glider.content.required_checks]
    from engineering.contracts import ComparisonLimit
    extra = make_check(glider, 'foam_core', limits=(ComparisonLimit(name='value', value=2.0, unit='count', comparison='le'),))   # declared by the capability, not required by this brief
    with pytest.raises(ValueError):
        attempt(glider, checks + [extra])


def test_generator_attempts_never_mix_with_beam_evidence():
    from engineering.contracts import AttemptRecord
    candidate = candidate_for(brief_for(content()))
    full = [make_check(candidate, i) for i in candidate.content.required_checks]
    with pytest.raises(ValueError):
        AttemptRecord(ordinal=1, candidate=candidate, outcome='accepted', checks=tuple(full))


def test_a_generator_run_is_accepted_only_with_its_own_payload_set():
    from engineering.contracts import (ArtifactIdentity, BundleIdentity, EngineeringRun, Lifecycle, Outcome)
    from engineering.capabilities import GENERATOR_BASE_PAYLOADS
    brief = brief_for(content())
    candidate = candidate_for(brief)
    primary = candidate.content.descriptor.primary_artifact
    full = [make_check(candidate, i) for i in candidate.content.required_checks]
    record = attempt(candidate, full)
    names = GENERATOR_BASE_PAYLOADS + candidate.content.descriptor.payloads
    when = datetime(2026, 11, 17, 12, 10, tzinfo=timezone.utc)

    def run(payload_names):
        bundle = BundleIdentity(
            payloads=tuple(ArtifactIdentity(path=n, sha256=sha(b'primary') if n == primary else 'b' * 64, bytes=7)
                           for n in payload_names),
            manifest_sha256='c' * 64, manifest_bytes=100, zip_sha256='d' * 64, zip_bytes=1000,
            stored_bytes=2000000)
        return EngineeringRun(schema_version='engineering_run_v1', job_id='gen-job', capability=brief.content.capability,
                              brief_sha256=brief.brief_sha256, fixed_requirements_sha256=brief.fixed_requirements_sha256,
                              lifecycle=Lifecycle.COMPLETED, outcome=Outcome.ACCEPTED, attempts=(record,),
                              created_at=when, updated_at=when,
                              selected_candidate_sha256=candidate.candidate_sha256, bundle=bundle)
    assert run(names).outcome == Outcome.ACCEPTED
    with pytest.raises(ValueError):
        run(names[:-1])
    with pytest.raises(ValueError):
        run(names + ('model.step',))


# --- assess_generator ----------------------------------------------------------

def candidate_and_assess():
    from engineering.policy import assess
    candidate = candidate_for(brief_for(content()))
    return candidate, lambda evaluation: assess(candidate, evaluation)


def test_assess_generator_accepts_only_complete_passed_and_matching_evidence():
    candidate, assess = candidate_and_assess()
    decision = assess(make_evaluation(candidate))
    assert decision.available and decision.outcome.value == 'accepted' and not decision.reasons
    assert [c.check_id for c in decision.checks] == list(candidate.content.required_checks)


@pytest.mark.parametrize('kwargs', [
    dict(drop=('hot_wall',)),
    dict(duplicate=('hot_wall',)),
    dict(drop=('hot_wall',), extra=('rib',)),
])
def test_missing_extra_or_duplicate_checks_make_the_run_unavailable(kwargs):
    candidate, assess = candidate_and_assess()
    decision = assess(make_evaluation(candidate, **kwargs))
    assert not decision.available and decision.outcome.value == 'not_evaluated' and not decision.checks


def test_an_undeclared_extra_check_id_is_unavailable_not_accepted():
    candidate, assess = candidate_and_assess()
    evaluation = make_evaluation(candidate)
    evaluation['checks'] = evaluation['checks'] + ({'check_id': 'invented'},)
    assert not assess(evaluation).available


def test_one_changed_byte_or_digest_makes_the_run_unavailable():
    candidate, assess = candidate_and_assess()
    primary = candidate.content.descriptor.primary_artifact

    def flip_byte(e): e['artifacts'][primary] = e['artifacts'][primary][:-1] + b'!'
    def flip_digest(e): e['stated_digests'][primary] = SHA
    def drop_digest(e): del e['stated_digests'][primary]
    def derived(e): e['extra_digests'] = ((b'part', SHA),)
    def spec_digest(e): e['spec_sha256'] = SHA
    def report_changed(e): e['report'] = dict(e['report'], failures=['changed'])
    def flag(e): e['passed'] = False
    def other_candidate(e): e['candidate'] = candidate_for(brief_for(content(specification=regen_spec(channel_count=25))))
    def extra_artifact(e): e['artifacts']['generator-extra.bin'] = b'x'
    for tamper in (flip_byte, flip_digest, drop_digest, derived, spec_digest, report_changed, flag,
                   other_candidate, extra_artifact):
        decision = assess(make_evaluation(candidate, tamper=tamper))
        assert not decision.available, tamper.__name__
    assert assess(make_evaluation(candidate, report_flag=False)).available is False


def test_one_failed_check_is_a_rejection_with_diagnostics_only():
    candidate, assess = candidate_and_assess()
    decision = assess(make_evaluation(candidate, failed=('hot_wall',)))
    assert decision.available and decision.outcome.value == 'rejected'
    assert decision.reasons == ('hot_wall out of tolerance',)
    leaked = make_evaluation(candidate, failed=('hot_wall',),
                             tamper=lambda e: e['artifacts'].update({'generator-cleaned.stl': b'geometry'}))
    assert not assess(leaked).available


def test_a_rejected_result_that_claims_a_pass_is_unavailable():
    candidate, assess = candidate_and_assess()
    assert not assess(make_evaluation(candidate, failed=('hot_wall',), report_flag=True)).available


# --- search with a fake adapter --------------------------------------------------

class FakeAdapter:
    def __init__(self, brief, **kwargs):
        self.brief, self.kwargs = brief, kwargs
        self.descriptor = brief.content.descriptor

    def candidates(self, brief):
        return (candidate_for(brief),)

    async def evaluate(self, candidate):
        return make_evaluation(candidate, **self.kwargs)


def run_search(**kwargs):
    from engineering.search import search
    brief = brief_for(content())
    return brief, asyncio.run(search(brief, adapter=FakeAdapter(brief, **kwargs)))


def test_search_selects_rejects_or_marks_unavailable_without_approximating():
    _, result = run_search()
    assert result.state == 'selected' and len(result.attempts) == 1
    assert result.attempts[0].outcome.value == 'accepted' and result.selected['artifacts']
    _, result = run_search(failed=('hot_wall',))
    assert result.state == 'rejected' and result.attempts[0].outcome.value == 'rejected'
    assert result.selected is None and result.evaluations[0]['artifacts'] == {}
    assert result.evaluations[0]['diagnostics']
    assert result.attempts[0].failures == ('hot_wall out of tolerance',)
    _, result = run_search(drop=('hot_wall',))
    assert result.state == 'unavailable' and not result.attempts


def test_search_refuses_a_different_capabilitys_adapter():
    from engineering.search import search
    brief = brief_for(content())
    other = FakeAdapter(brief_for(content('glider_cem', glider_spec())))
    with pytest.raises(ValueError, match='another capability'):
        asyncio.run(search(brief, adapter=other))


# --- refusal --------------------------------------------------------------------

def test_refuse_gives_unsupported_issue_and_finalize_refuses():
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.contracts import ReviewAcknowledgement
    service = BriefService(MemoryBriefRepository())
    payload = dict(capability=reference(__import__('engineering.capabilities', fromlist=['x']).get_generator('regen_chamber')),
                   specification=regen_spec(voxel_size_mm=0.2))
    prepared = service.create(payload)
    assert prepared.draft.content is None
    assert [i.state for i in prepared.issues] == ['unsupported']
    assert prepared.issues[0].field == 'specification.voxel_size_mm'
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id=prepared.draft.draft_id, draft_revision=1, brief_sha256=SHA,
        acknowledged_fields=['specification.voxel_size_mm'],
        reviewed_at='2026-11-17T12:05:00Z', reviewed_by='local-user')))
    with pytest.raises(ValueError, match='Unresolved clarification'):
        service.finalize(review)


def test_brief_service_normalizes_and_finalizes_a_supported_generator_draft():
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.capabilities import get_generator
    from engineering.contracts import ReviewAcknowledgement
    service = BriefService(MemoryBriefRepository())
    descriptor = get_generator('glider_cem')
    prepared = service.create(dict(capability=reference(descriptor), specification=glider_spec()))
    assert not prepared.issues and prepared.draft.content is not None
    assert prepared.draft.content.required_checks == descriptor.required_checks(prepared.draft.content.specification)
    origins = {o.field: o.origin for o in prepared.draft.provenance.field_origins}
    assert set(origins) == set(prepared.draft.content.review_fields)
    assert origins['specification.calibration_version'] == 'user_supplied'
    assert origins['specification.requirements.process'] == 'policy'   # default the user did not give
    review = ReviewAcknowledgement.model_validate_json(json.dumps(dict(
        draft_id=prepared.draft.draft_id, draft_revision=1, brief_sha256=prepared.draft.content.brief_sha256,
        acknowledged_fields=list(prepared.draft.content.review_fields),
        reviewed_at='2026-11-17T12:05:00Z', reviewed_by='local-user')))
    brief = service.finalize(review)
    assert brief.content == prepared.draft.content


@pytest.mark.parametrize('capability,spec,field', [
    ('co2_dragster', dict(schema_version=1, style='wedge'), 'specification.schema_version'),
    ('regen_chamber', regen_spec(voxel_size_mm=0.1), 'specification.voxel_size_mm'),
    ('glider_cem', glider_spec(environment=dict(kind='thermal', thermal=dict(
        core_uplift_m_s=1.0, radius_m=50.0, distance_m=10.0))), 'specification.requirements.environment'),
    ('glider_cem', glider_spec(environment=dict(kind='ridge', ridge=dict())), 'specification.requirements.environment'),
    ('glider_cem', glider_spec(discovery=dict(families=['conventional', 'canard'])),
     'specification.requirements.discovery.families'),
    ('glider_cem', glider_spec(discovery=dict(families='all')), 'specification.requirements.discovery.families'),
])
def test_descriptor_refuse_names_each_unsupported_request(capability, spec, field):
    from engineering.capabilities import get_generator
    refused = get_generator(capability).refuse(spec)
    assert field in {name for name, _ in refused}
    assert all(reason for _, reason in refused)


def test_supported_requests_are_not_refused():
    from engineering.capabilities import get_generator
    assert get_generator('regen_chamber').refuse(regen_spec()) == ()
    assert get_generator('glider_cem').refuse(glider_spec()) == ()
    assert get_generator('co2_dragster').refuse(dict(schema_version=2)) == ()


# --- registry --------------------------------------------------------------------

@pytest.mark.parametrize('capability_id', ['co2_dragster', 'regen_chamber', 'glider_cem'])
def test_missing_adapter_is_unsupported_capability(capability_id):
    from engineering.capabilities import UnsupportedCapability, get_generator
    get_adapter = REAL_GET_ADAPTER
    descriptor = get_generator(capability_id)
    if importlib.util.find_spec(descriptor.adapter.partition(':')[0]) is not None:
        pytest.skip('adapter installed by a later PR')
    with pytest.raises(UnsupportedCapability, match='adapter not installed'):
        get_adapter(descriptor.id, descriptor.version, numerical_policy_version=descriptor.numerical_version)


def test_registry_lists_beam_first_and_resolves_each_generator_by_exact_versions():
    from engineering.capabilities import (CAPABILITY, GENERATORS, UnsupportedCapability, get_capability,
                                          list_capabilities)
    assert list_capabilities() == (CAPABILITY, *GENERATORS)
    assert [g.id for g in GENERATORS] == ['co2_dragster', 'regen_chamber', 'glider_cem']
    for g in GENERATORS:
        assert get_capability(g.id, g.version, numerical_policy_version=g.numerical_version) is g
        with pytest.raises(UnsupportedCapability):
            get_capability(g.id, g.version)   # beam numerical version is not theirs
        with pytest.raises(UnsupportedCapability):
            get_capability(g.id, '0.0.1', numerical_policy_version=g.numerical_version)
        assert g.engineering_certified is False and g.standards == 'not_evaluated'
        assert g.resources == CAPABILITY.resources
        assert g.primary_artifact in g.payloads
        assert all(p.startswith('generator-') for p in g.payloads)
    assert get_capability('co2_dragster', '2.0.0', numerical_policy_version='co2_dragster_numerical_v1').adapter


def test_required_check_sets_match_the_plan():
    from engineering.capabilities import get_generator
    assert len(get_generator('co2_dragster').base_checks) == 14
    assert len(get_generator('regen_chamber').base_checks) == 13
    assert len(get_generator('glider_cem').base_checks) == 13
    for g in ('co2_dragster', 'regen_chamber', 'glider_cem'):
        ids = get_generator(g).all_check_ids
        assert len(ids) == len(set(ids))


def test_resource_policy_is_unchanged():
    from engineering.capabilities import ResourcePolicy
    r = ResourcePolicy()
    assert (r.overall_seconds, r.run_bytes, r.storage_bytes, r.active_jobs, r.queue_size) == (
        420, 32 * 1024**2, 512 * 1024**2, 1, 0)
    assert r.version == 'engineering_resources_v1'


def test_importing_the_registry_loads_no_provider_native_or_web_modules():
    code = ('import sys\n'
            'import engineering.capabilities as c\n'
            'assert len(c.list_capabilities()) == 4\n'
            'bad = sorted(m for m in sys.modules if m.split(".")[0] in '
            '("google", "cadquery", "OCP", "aerosandbox", "trimesh", "scipy", "fastapi"))\n'
            'assert not bad, bad\n')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([BACKEND, SEARCH_DIR]))
    subprocess.run([sys.executable, '-c', code], check=True, env=env, cwd=BACKEND)


def _references():
    from engineering.capabilities import GENERATORS
    return [(g.id, r) for g in GENERATORS for r in g.numerical + g.runtime_limits]


@pytest.mark.parametrize('owner,ref', _references(), ids=lambda v: v if isinstance(v, str) else v.name)
def test_policy_references_have_not_drifted(owner, ref):
    if ref.module is None:
        assert ref.source.startswith('backend/') and ':' in ref.source   # inline literal: file:line source
        with pytest.raises(ValueError):
            ref.resolve()
        return
    try:
        actual = ref.resolve()
    except ImportError as error:
        pytest.skip(f'{ref.module} needs a native runtime unavailable on this host: {error}')
    assert actual == ref.value


def test_glider_supported_families_have_not_drifted():
    from engineering.capabilities import GLIDER_SUPPORTED_FAMILIES
    try:
        from cem.glider.model import SUPPORTED_FAMILIES
    except ImportError as error:
        pytest.skip(f'glider model needs its solver runtime: {error}')
    assert set(GLIDER_SUPPORTED_FAMILIES) == set(SUPPORTED_FAMILIES)


def test_inline_glider_floors_have_not_drifted_from_their_sources():
    from engineering.capabilities import GLIDER
    root = Path(__file__).resolve().parents[2]
    for ref in GLIDER.numerical:
        path, _, line = ref.source.rpartition(':')
        text = (root / path).read_text().splitlines()[int(line) - 1]
        literal = {'min_station_reynolds': '30000.', 'min_confidence': '.8', 'mass_relative_error_max': '.05',
                   'spar_stress_allowable_factor_slack': '1e-5', 'uncertainty_samples': '200',
                   'parts_min': '3', 'parts_max': '16', 'parts_max_segmented': '32',
                   'artifact_bytes_max': '64*1024*1024'}[ref.name]
        assert literal in text, (ref.name, text)


# --- proposers ---------------------------------------------------------------------

def test_flag_off_builds_no_proposer_provider_or_key_read(monkeypatch):
    from engineering import proposer as module
    from engineering.briefs import BriefService, MemoryBriefRepository
    reads, built = [], []

    class Watching(dict):
        def get(self, key, *args): reads.append(key); return super().get(key, *args)
        def __getitem__(self, key): reads.append(key); return super().__getitem__(key)
        def __contains__(self, key): reads.append(key); return super().__contains__(key)
    monkeypatch.setattr(module.os, 'environ', Watching({'GEMINI_API_KEY_HEAVY': 'must-not-be-read'}))
    monkeypatch.setattr(module.GeminiProposalProvider, '__init__', lambda self: built.append(1))
    service = BriefService(MemoryBriefRepository())
    assert module.make_proposer(service, ledger=module.MemoryProposalLedger(), enabled=False,
                                provider_factory=lambda: built.append(2)) is None
    assert built == [] and reads == []


def test_flag_on_returns_a_per_capability_dispatcher_keeping_the_beam_proposer_as_ai(monkeypatch):
    from engineering import proposer as module
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.capabilities import UnsupportedCapability
    monkeypatch.setattr(module.GeminiProposalProvider, '__init__', lambda self: None)
    dispatcher = module.make_proposer(BriefService(MemoryBriefRepository()),
                                      ledger=module.MemoryProposalLedger(), enabled=True)
    assert isinstance(dispatcher.ai, module.BriefProposer)
    assert dispatcher.provider is dispatcher.ai.provider and dispatcher.budget is dispatcher.ai.budget
    fixture = object()
    dispatcher.provider = fixture
    assert dispatcher.ai.provider is fixture
    with pytest.raises(UnsupportedCapability):
        dispatcher.rules('glider_cem')           # declares no rules proposer
    with pytest.raises(UnsupportedCapability, match='adapter not installed|proposer'):
        dispatcher.rules('co2_dragster')         # declared, module not installed in U6.0
    with pytest.raises(UnsupportedCapability):
        dispatcher.rules('rectangular_cantilever')


# --- publication (Linux store; CI only) ------------------------------------------

@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
def test_generator_package_publishes_exact_bytes_and_diagnostics_hold_no_geometry(tmp_path):
    import io
    import zipfile
    from engineering.bundles import BundlePublisher
    from engineering.store import SQLiteStore
    store = SQLiteStore(tmp_path)
    publisher = BundlePublisher(store)
    brief, result = run_search()
    bundle = asyncio.run(publisher.publish('a' * 32, brief, result))
    publisher.validate('a' * 32, bundle)
    with publisher.lease('a' * 32, 'zip', envelope=bundle) as download:
        archive = download.file.read()
    descriptor = brief.content.descriptor
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        assert set(z.namelist()) == {p.path for p in bundle.payloads} | {'manifest.json'}
        for name, value in result.selected['artifacts'].items():
            assert z.read(name) == value
        assert json.loads(z.read('selected_spec.json')) == brief.content.specification
        assert 'Checked' in z.read('report.md').decode()
    assert {p.path for p in bundle.payloads} == {
        'brief.json', 'selected_spec.json', 'attempts.json', 'report.json', 'report.md', *descriptor.payloads}
    brief, rejected = run_search(failed=('hot_wall',))
    diagnostic = asyncio.run(publisher.publish_diagnostics('b' * 32, brief, rejected))
    assert diagnostic['kind'] == 'diagnostic'
    names = {p['path'] for p in diagnostic['payloads']}
    assert descriptor.primary_artifact not in names and 'generator-report.json' in names
    store.close()


# --- review findings ----------------------------------------------------------------

def test_a_passed_state_cannot_override_a_measurement_outside_its_limit():
    candidate, assess = candidate_and_assess()
    # PASSED with value far outside the pinned limit
    assert not assess(make_evaluation(candidate, bad_checks={'volume': dict(value=5.0)})).available
    inside = make_evaluation(candidate, failed=('volume',), bad_checks={'volume': dict(value=0.0)})
    assert not assess(inside).available


def _volume_limit(candidate):
    return candidate.content.descriptor.expected_limits(candidate.content.specification)['volume'][0]


def test_closed_limit_table_is_built_from_the_pinned_constants():
    candidate, _ = candidate_and_assess()
    limit = _volume_limit(candidate)
    ref = next(r for r in candidate.content.descriptor.numerical if r.name == 'VOLUME_TOLERANCE')
    assert (limit.name, limit.unit, limit.comparison, limit.value) == (
        'volume_relative_error', 'ratio', 'le', ref.value)


def _limits_tamper(change):
    def build(candidate):
        limit = _volume_limit(candidate)
        return dict(limits=change(limit))
    return build


@pytest.mark.parametrize('name,change', [
    ('extra_unpinned_limit', lambda l: (l, type(l)(name='extra', value=1.0, unit='count', comparison='le'))),
    ('missing_limit', lambda l: ()),
    ('flipped_comparison', lambda l: (type(l)(**dict(l.model_dump(), comparison='ge')),)),
    ('changed_unit', lambda l: (type(l)(**dict(l.model_dump(), unit='mm')),)),
    ('loosened_value', lambda l: (type(l)(**dict(l.model_dump(), value=l.value * 10)),)),
    ('renamed', lambda l: (type(l)(**dict(l.model_dump(), name='other')),)),
    ('tolerance_added', lambda l: (type(l)(**dict(l.model_dump(), tolerance=1.0)),)),
])
def test_any_deviation_from_the_closed_limit_table_is_unavailable(name, change):
    candidate, assess = candidate_and_assess()
    overrides = _limits_tamper(change)(candidate)
    state = 'passed'
    if not overrides['limits']:
        # a passing check cannot exist without limits, so the contract itself refuses it
        with pytest.raises(ValueError):
            make_check(candidate, 'volume', **overrides)
        return
    assert not assess(make_evaluation(candidate, bad_checks={'volume': overrides})).available, state


def test_a_check_whose_limits_are_not_pinned_yet_is_never_accepted(monkeypatch):
    candidate, assess = candidate_and_assess()
    evaluation = make_evaluation(candidate)
    assert assess(evaluation).available
    monkeypatch.setattr(_Descriptor, 'expected_limits', REAL_EXPECTED_LIMITS)
    assert not assess(evaluation).available   # most regen checks lack a pinned table until U6.1


def test_check_measurements_must_equal_what_the_adapter_parses_from_the_report(monkeypatch):
    from engineering.policy import report_digest
    candidate, assess = candidate_and_assess()
    assert assess(make_evaluation(candidate)).available

    def fabricate(e):
        e['report']['measurements']['volume'] = {'volume_relative_error': 0.0001}
        e['report_bytes'] = json.dumps(e['report']).encode()
        e['report_sha256'] = report_digest(e['report'])
    assert not assess(make_evaluation(candidate, tamper=fabricate)).available

    def extra_check(e):
        e['report']['measurements']['phantom'] = {}
        e['report_bytes'] = json.dumps(e['report']).encode()
        e['report_sha256'] = report_digest(e['report'])
    assert not assess(make_evaluation(candidate, tamper=extra_check)).available

    def other_bytes(e): e['report_bytes'] = b'{"measurements": {}}'
    assert not assess(make_evaluation(candidate, tamper=other_bytes)).available

    def uninstalled(*a, **k):
        from engineering.capabilities import UnsupportedCapability
        raise UnsupportedCapability('adapter not installed')
    monkeypatch.setattr(_capabilities, 'get_adapter', uninstalled)
    assert not assess(make_evaluation(candidate)).available


def test_report_markdown_escapes_pipes_and_newlines_in_reasons():
    from engineering.bundles import md_cell
    assert md_cell('a|b\nc\r\nd') == 'a\\|b c d'


def test_report_markdown_escapes_the_trailing_reasons_too():
    from engineering.bundles import BundlePublisher
    brief = brief_for(content())
    result = type('R', (), dict(attempts=(), evaluations=(), reasons=('bad | reason\nnext',)))()
    out = BundlePublisher._generator_reports(object.__new__(BundlePublisher), 'a' * 32, brief, result, 'rejected')
    assert 'bad \\| reason next' in out['report.md'].decode()
