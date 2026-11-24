"""Run M3 through native review, publication, independent verification and replay."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from engineering.language.diagnostics import MelError
from engineering.language.semantic import compile_source
from engineering.program.client import ProgramClient
from engineering.program.replay import capture, remeasure
from tests.test_program_equipment_bay_native import CLIENT, export, review, run, stage
from tests.test_program_interface_parity import BASE, TOKEN, V1, bridge, http_headers, parity_app
from vendor.mirum_kernel.structural import Case, prepare, resolve_result, result_state


SOURCE = (Path(__file__).parents[1] / 'engineering/fixtures/structural/cantilever.mir').read_bytes()


def report_from(data):
    (evaluation,) = json.loads(data)['evaluations']
    return evaluation['native_report']


@pytest.mark.parametrize(('old', 'new', 'code'), [
    (b'load_region = "beam.free_end";', b'', 'MEL-S023'),
    (b'beam.free_end', b'beam.missing', 'MEL-S023'),
    (b'beam.root_section', b'beam.ends', 'MEL-S022'),
])
def test_m3_requires_unique_named_boundaries(old, new, code):
    with pytest.raises(MelError) as error:
        compile_source(SOURCE.replace(old, new))
    assert error.value.diagnostic.code == code


def test_m3_reviewed_revision_checks_structure_and_preserves_prior_evidence(tmp_path, monkeypatch):
    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as w:
        headers = http_headers(CLIENT)
        pid = w.c.post(f'{V1}/programs', headers=headers).json()['program_id']

        def evidence(rid):
            response = w.c.get(f'{V1}/revisions/{rid}/evidence', headers=headers)
            assert response.status_code == 200, response.text
            return response.json()['evidence']

        def assert_head(rid):
            history = w.c.get(f'{V1}/programs/{pid}/history', headers=headers).json()
            assert history['head_revision_id'] == rid

        staged = stage(w, pid, SOURCE)
        assert staged.status_code == 201, staged.text
        fields = staged.json()['review_fields']
        assert {'load_region', 'support_region'} <= set(fields)
        rid = staged.json()['revision']['revision_id']
        unreviewed_support = w.c.post(f'{V1}/revisions/{rid}/review', headers=headers, json={
            'brief_sha256': staged.json()['revision']['brief_sha256'],
            'reviewed_by': 'fixture reviewer',
            'acknowledged_fields': [field for field in fields if field != 'support_region'],
        })
        assert unreviewed_support.status_code == 422, unreviewed_support.text
        assert review(w, staged) == rid
        published = run(w, rid)
        assert published['state'] == 'published', published
        original_evidence = evidence(rid)
        assert original_evidence['verdict'] == 'verified', original_evidence
        original_report = export(w, rid, 'report')
        original_step = export(w, rid, 'step')
        native = report_from(original_report)
        assert native['step_sha256'] == original_evidence['step_sha256'] == hashlib.sha256(original_step).hexdigest()
        original = native['structural_analysis']
        cold, action = resolve_result(prepare(Case(
            span_m=0.20, width_m=0.020, depth_m=0.004, youngs_modulus_pa=70e9, force_n=1,
            load_region='beam.free_end', support_region='beam.root_section', units='SI')))
        assert action == 'computed' and original == cold

        client = ProgramClient(BASE, token=TOKEN, transport=bridge(w, CLIENT))
        package = capture(client, rid)
        assert asyncio.run(remeasure(package))['native_reread'] == 'consistent'

        for bad in (
            SOURCE.replace(b'beam.free_end', b'beam.missing'),
            SOURCE.replace(b'beam.root_section', b'beam.ends'),
        ):
            refused = stage(w, pid, bad, rid)
            assert refused.status_code == 422, refused.text
        assert_head(rid)

        # Substitute earlier analysis at the verifier's post-lease seam. The new job
        # itself ran and published valid bytes for the 1.5 N case before this injection.
        edited = SOURCE.replace(b'1 N', b'1.5 N')
        load_id = review(w, stage(w, pid, edited, rid))
        lease_all = w.service.verifier._lease_all

        def with_stale_analysis(job_id, bundle, payloads=None):
            blobs = lease_all(job_id, bundle, payloads)
            report = json.loads(blobs['report.json'])
            current = report['evaluations'][0]['native_report']
            assert current['structural_analysis']['state'] == 'passed'
            assert result_state(current['structural_analysis']['inputs'], original) == 'stale'
            current['structural_analysis'] = original
            blobs['report.json'] = json.dumps(report).encode()
            return blobs

        with monkeypatch.context() as scoped:
            scoped.setattr(w.service.verifier, '_lease_all', with_stale_analysis)
            stale = run(w, load_id)
        assert stale['state'] == 'failed', stale
        stale_evidence = evidence(load_id)
        assert stale_evidence['verdict'] == 'failed', stale_evidence
        assert 'report_mismatch' in stale_evidence['reasons']
        assert_head(rid)

        # A physically solved revision still needs to meet its reviewed design limit.
        bad_limit = SOURCE.replace(b'max_tip_deflection = 1 mm', b'max_tip_deflection = 0.1 mm')
        rejected_id = review(w, stage(w, pid, bad_limit, rid))
        rejected = run(w, rejected_id)
        assert rejected['state'] == 'rejected', rejected
        assert_head(rid)

        # Review a support-only edit and check observed solver output against a cold run.
        moved = SOURCE.replace(b'beam.root_section', b'beam.inboard_section')
        moved_id = review(w, stage(w, pid, moved, rid))
        published = run(w, moved_id)
        assert published['state'] == 'published', published
        analysis = report_from(export(w, moved_id, 'report'))['structural_analysis']
        moved_cold, _ = resolve_result(prepare(Case(
            span_m=0.20, width_m=0.020, depth_m=0.004, youngs_modulus_pa=70e9, force_n=1,
            load_region='beam.free_end', support_region='beam.inboard_section', units='SI')))
        assert result_state(analysis['inputs'], original) == 'stale'
        assert analysis == moved_cold
        assert analysis['inputs']['geometry_revision_sha256'] == original['inputs']['geometry_revision_sha256']

        renamed = moved.replace(b'design m3_cantilever', b'design renamed_cantilever')
        renamed_id = review(w, stage(w, pid, renamed, moved_id))
        published = run(w, renamed_id)
        assert published['state'] == 'published', published
        renamed_analysis = report_from(export(w, renamed_id, 'report'))['structural_analysis']
        assert renamed_analysis == analysis
        assert_head(renamed_id)
        assert evidence(rid) == original_evidence
        assert export(w, rid, 'step') == original_step
        assert export(w, rid, 'report') == original_report
