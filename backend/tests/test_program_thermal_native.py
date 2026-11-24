"""M3 acceptance through the real revision and archive paths."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from engineering.program.client import ProgramClient
from engineering.program.replay import capture, remeasure, replay
from tests.test_program_equipment_bay_native import CLIENT, export, review, run, stage
from tests.test_program_interface_parity import BASE, TOKEN, V1, bridge, http_headers, parity_app
from tests.test_program_thermal_units import SOURCE


@pytest.mark.skipif(os.name != 'posix', reason='native subprocess isolation requires POSIX')
def test_thermal_review_verification_and_archive(tmp_path, monkeypatch):
    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as world:
        headers = http_headers(CLIENT)
        pid = world.c.post(f'{V1}/programs', headers=headers).json()['program_id']

        def evidence(revision):
            response = world.c.get(f'{V1}/revisions/{revision}/evidence', headers=headers)
            assert response.status_code == 200, response.text
            return response.json()['evidence']

        def assert_head(revision):
            assert world.c.get(f'{V1}/programs/{pid}/history', headers=headers).json()['head_revision_id'] == revision

        staged = stage(world, pid, SOURCE)
        assert staged.status_code == 201, staged.text
        fields = staged.json()['review_fields']
        required = {'sink_region', 'heat_region', 'insulation_region', 'conductivity_w_m_k', 'power_w',
                    'sink_k', 'alpha_per_k', 'reference_k', 'restraint', 'transfer_model', 'field_reference',
                    'cold_gap_mm', 'clearance_allowance_mm'}
        assert {f'specification.{name}' for name in required} <= set(fields)
        rid = staged.json()['revision']['revision_id']
        for missing in ('sink_region', 'conductivity_w_m_k', 'transfer_model', 'clearance_allowance_mm'):
            refusal = world.c.post(f'{V1}/revisions/{rid}/review', headers=headers, json={
                'brief_sha256': staged.json()['revision']['brief_sha256'], 'reviewed_by': 'fixture reviewer',
                'acknowledged_fields': [f for f in fields if f != f'specification.{missing}'],
            })
            assert refusal.status_code == 422, refusal.text

        assert review(world, staged) == rid
        publication = run(world, rid)
        assert publication['state'] == 'published', (publication, evidence(rid))
        original_evidence = evidence(rid)
        assert original_evidence['verdict'] == 'verified'
        original_report = export(world, rid, 'generator-report')
        original_step = export(world, rid, 'generator-body-step')
        report = json.loads(original_report)
        assert report['step_sha256'] == original_evidence['step_sha256'] == hashlib.sha256(original_step).hexdigest()
        original = report['analysis']
        assert original['stages']['conduction']['result']['samples'][-1]['rise_k'] == pytest.approx(1)
        assert original['stages']['expansion']['result']['samples'][-1]['expansion_m'] == pytest.approx(1.15e-6)
        assert original['stages']['clearance']['result']['gap_m'] == pytest.approx(3.85e-6)

        client = ProgramClient(BASE, token=TOKEN, transport=bridge(world, CLIENT))
        package = capture(client, rid)
        archived = asyncio.run(remeasure(package))
        assert archived['native_reread'] == 'consistent'

        for source in (SOURCE.replace(b'bar.cold_end', b'bar.ends'),
                       SOURCE.replace(b'conduction.temperature', b'conduction.missing')):
            response = stage(world, pid, source, rid)
            assert response.status_code == 422, response.text
            assert_head(rid)

        edited = SOURCE.replace(b'2 W;', b'2.5 W;')
        stale_id = review(world, stage(world, pid, edited, rid))
        lease_all = world.service.verifier._lease_all

        def with_stale_field(job_id, bundle, payloads=None):
            blobs = lease_all(job_id, bundle, payloads)
            delivered = json.loads(blobs['generator-report.json'])
            actual = delivered['analysis']['stages']['conduction']
            before = original['stages']['conduction']
            assert actual['dependency_sha256'] != before['dependency_sha256']
            actual['result']['samples'][-1]['field'] = before['result']['samples'][-1]['field']
            blobs['generator-report.json'] = json.dumps(delivered).encode()
            return blobs

        with monkeypatch.context() as scoped:
            scoped.setattr(world.service.verifier, '_lease_all', with_stale_field)
            stale = run(world, stale_id)
        assert stale['state'] == 'failed', stale
        assert 'report_mismatch' in evidence(stale_id)['reasons']
        assert_head(rid)

        tight = SOURCE.replace(b'5 um;', b'0.50 um;')
        tight_id = review(world, stage(world, pid, tight, rid))
        rejected = run(world, tight_id)
        assert rejected['state'] == 'rejected', rejected
        diagnostic = json.loads(export(world, tight_id, 'generator-report'))
        assert diagnostic['analysis']['stages']['clearance']['result']['gap_m'] == pytest.approx(-0.65e-6)
        assert_head(rid)

        new_id = review(world, stage(world, pid, edited, rid))
        publication = run(world, new_id)
        assert publication['state'] == 'published', publication
        assert_head(new_id)
        assert evidence(new_id)['verdict'] == 'verified'
        assert evidence(rid) == original_evidence
        assert export(world, rid, 'generator-report') == original_report
        assert export(world, rid, 'generator-body-step') == original_step
        assert replay(capture(client, rid)) == replay(package)

        if destination := os.environ.get('MIRUM_THERMAL_EVIDENCE_DIR'):
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'mirum-thermal-replay.zip').write_bytes(package)
            (output / 'mirum-thermal-receipt.json').write_text(json.dumps(dict(
                revision=client.inspect(rid).model_dump(mode='json')['revision'], evidence=original_evidence,
                subsequent_revision=new_id, stale_revision=stale_id, stale_evidence=evidence(stale_id),
                rejected_revision=tight_id, rejected_clearance=diagnostic['analysis']['stages']['clearance'],
                archive_sha256=hashlib.sha256(package).hexdigest(), replay=archived,
            ), indent=2, sort_keys=True) + '\n', encoding='utf-8')
