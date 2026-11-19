"""Provider-free preparation with atomic revision/finalization repository seam."""
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys

import pytest

from backend.tests.test_engineering_contracts import data, review_for


def service():
    from engineering.briefs import BriefService, MemoryBriefRepository
    return BriefService(MemoryBriefRepository())


def test_reference_create_review_finalize_export_and_duplicate():
    from engineering.cantilever import CantileverAdapter
    svc = service()
    prepared = svc.create(data()['content'])
    assert prepared.issues == ()
    assert prepared.draft.content.candidate_heights_mm == (5, 10, 15)
    review = review_for(prepared.draft)
    brief = svc.finalize(review)
    assert svc.finalize(review) == brief
    assert svc.read_brief(brief.brief_id) == brief
    assert svc.read(prepared.draft.draft_id) == prepared
    assert svc.export(prepared.draft.draft_id)['input'] == data()['content']
    assert [c.specification.height_mm for c in CantileverAdapter().candidates(brief)] == [5, 10, 15]
    with pytest.raises(ValueError):
        brief.content.requirements.tip_force_n = 100


@pytest.mark.parametrize('field', ['tip_force_n', 'material', 'assumptions', 'units'])
def test_incomplete_inputs_remain_visible_but_cannot_finalize(field):
    svc = service()
    payload = data()['content']
    del payload['requirements'][field]
    prepared = svc.create(payload)
    assert prepared.draft.content is None
    assert any(field in i.field and i.state == 'clarification_required' for i in prepared.issues)
    assert svc.export(prepared.draft.draft_id)['input'] == payload
    from engineering.contracts import ReviewAcknowledgement, REVIEW_FIELDS
    from datetime import datetime, timezone
    review = ReviewAcknowledgement(draft_id=prepared.draft.draft_id, draft_revision=1,
        brief_sha256='0'*64, acknowledged_fields=REVIEW_FIELDS,
        reviewed_at=datetime.now(timezone.utc), reviewed_by='reviewer')
    with pytest.raises(ValueError):
        svc.finalize(review)


@pytest.mark.parametrize('change', ['holes', 'version', 'non_slender', 'kg', 'unknown_source'])
def test_unsupported_and_ambiguous_inputs_are_not_dropped(change):
    svc = service()
    payload = data()['content']
    if change == 'holes':
        payload['features'] = ['holes']
    elif change == 'version':
        payload['capability']['version'] = 'next'
    elif change == 'non_slender':
        payload['candidate_heights_mm'] = [30.0]
    elif change == 'kg':
        payload['requirements']['tip_force_n'] = {'value': 5, 'unit': 'kg'}
    else:
        payload['requirements']['material']['reference'] = ''
    prepared = svc.create(payload)
    assert prepared.issues and prepared.draft.content is None
    if change in ('holes', 'version', 'non_slender'):
        assert any(i.state == 'unsupported' for i in prepared.issues)


def test_edit_always_invalidates_confirmation_and_stale_edit_conflicts():
    from engineering.briefs import RevisionConflict
    svc = service()
    first = svc.create(data()['content'])
    old_review = review_for(first.draft)
    second = svc.edit(first.draft.draft_id, expected_revision=1, payload=data()['content'])
    assert second.draft.revision == 2
    with pytest.raises(RevisionConflict):
        svc.finalize(old_review)
    with pytest.raises(RevisionConflict):
        svc.edit(first.draft.draft_id, expected_revision=1, payload=data()['content'])
    assert svc.finalize(review_for(second.draft)).draft_revision == 2


def test_concurrent_edits_have_one_atomic_winner():
    from engineering.briefs import RevisionConflict
    svc = service()
    first = svc.create(data()['content'])
    def edit(_):
        try:
            return svc.edit(first.draft.draft_id, expected_revision=1, payload=data()['content']).draft.revision
        except RevisionConflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(map(str, pool.map(edit, range(2)))) == ['2', 'conflict']


def test_concurrent_finalize_reuses_one_immutable_record():
    svc = service()
    prepared = svc.create(data()['content'])
    review = review_for(prepared.draft)
    with ThreadPoolExecutor(4) as pool:
        records = list(pool.map(lambda _: svc.finalize(review), range(4)))
    assert len({r.brief_id for r in records}) == 1


def test_changed_values_and_normalized_choices_are_the_reviewed_meaning():
    svc = service()
    first = svc.create(data()['content'])
    payload = data()['content']
    payload['candidate_heights_mm'] = [15.0, 5.0, 15.0, 10.0]
    same = svc.edit(first.draft.draft_id, expected_revision=1, payload=payload)
    assert same.draft.content.brief_sha256 == first.draft.content.brief_sha256
    payload['requirements']['tip_force_n'] = 60.0
    changed = svc.edit(first.draft.draft_id, expected_revision=2, payload=payload)
    assert changed.draft.content.brief_sha256 != first.draft.content.brief_sha256
    brief = svc.finalize(review_for(changed.draft))
    assert brief.content.requirements.tip_force_n == 60.0


@pytest.mark.parametrize('revision', [True, 0, -1, 1.0, '1'])
def test_expected_revision_is_a_positive_integer(revision):
    svc = service()
    prepared = svc.create(data()['content'])
    with pytest.raises(ValueError):
        svc.edit(prepared.draft.draft_id, expected_revision=revision, payload=data()['content'])


def test_repository_detects_edit_during_finalization():
    from engineering.briefs import BriefService, MemoryBriefRepository, RevisionConflict
    class RacingRepository(MemoryBriefRepository):
        def finalize_if_current(self, brief):
            svc.edit(brief.draft_id, expected_revision=brief.draft_revision, payload=data()['content'])
            return super().finalize_if_current(brief)
    svc = BriefService(RacingRepository())
    prepared = svc.create(data()['content'])
    with pytest.raises(RevisionConflict):
        svc.finalize(review_for(prepared.draft))


def test_import_and_complete_typed_flow_never_load_provider_or_native():
    code = '''
import sys
from engineering.briefs import BriefService, MemoryBriefRepository
from backend.tests.test_engineering_contracts import data, review_for
s = BriefService(MemoryBriefRepository())
d = s.create(data()['content']).draft
s.finalize(review_for(d))
assert not any(n in sys.modules for n in ['agents', 'google.genai', 'cadquery', 'OCP', 'numpy'])
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
