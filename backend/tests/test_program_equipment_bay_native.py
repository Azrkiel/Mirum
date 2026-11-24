"""Equipment-bay conformance on the real F08 route, adapter, publisher and revision service."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from engineering.language.semantic import compile_source
from tests.test_program_interface_native import poll
from tests.test_program_interface_parity import BASE, TOKEN, V1, bridge, http_headers, parity_app

SOURCE = (Path(__file__).parents[1] / 'engineering/fixtures/equipment_bay/nominal.mir').read_bytes()
CLIENT = 'bay-review'


def test_bay_canonical_millimetres_and_meters_have_one_reviewed_meaning():
    mm = compile_source(SOURCE.replace(b'560 mm', b'1000 mm'))
    m = compile_source(SOURCE.replace(b'560 mm', b'1 m'))
    changed = compile_source(SOURCE.replace(b'"nominal"', b'"supplier-shifted"'))
    nominal = compile_source(SOURCE)
    assert mm.semantic_sha256 == m.semantic_sha256
    assert mm.brief_sha256 == m.brief_sha256
    assert nominal.brief_sha256 != changed.brief_sha256
    assert nominal.brief_content.fixed_requirements_sha256 == changed.brief_content.fixed_requirements_sha256


def stage(w, program, source, parent=None, guard=None):
    headers = http_headers(CLIENT)
    if parent is not None:
        headers['X-Mirum-Expected-Parent'] = parent
    if guard is not None:
        headers['X-Mirum-Require-Requirements'] = guard
    return w.c.post(f'{V1}/programs/{program}/revisions', headers=headers, content=source)


def review(w, value, fields=None):
    staged = value.json()
    rid = staged['revision']['revision_id']
    response = w.c.post(f'{V1}/revisions/{rid}/review', headers=http_headers(CLIENT), json={
        'brief_sha256': staged['revision']['brief_sha256'], 'reviewed_by': 'fixture reviewer',
        'acknowledged_fields': fields or staged['review_fields']})
    assert response.status_code == 200, response.text
    return rid


def run(w, rid):
    response = w.c.post(f'{V1}/revisions/{rid}/run', headers=http_headers(CLIENT))
    assert response.status_code == 200, response.text
    return poll(w, CLIENT, rid)


def export(w, rid, fmt):
    response = w.c.get(f'{V1}/revisions/{rid}/artifacts/{fmt}', headers=http_headers(CLIENT))
    assert response.status_code == 200, response.text
    assert response.headers['x-content-sha256'] == hashlib.sha256(response.content).hexdigest()
    return response.content


@pytest.mark.skipif(os.name != 'posix', reason='native worker uses process groups')
def test_bay_promote_reject_and_preserve_immutable_package(tmp_path, monkeypatch):
    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as w:
        headers = http_headers(CLIENT)
        created = w.c.post(f'{V1}/programs', headers=headers)
        assert created.status_code == 201, created.text
        program = created.json()['program_id']
        staged = stage(w, program, SOURCE)
        assert staged.status_code == 201, staged.text
        rid = review(w, staged)
        published = run(w, rid)
        assert published['state'] == 'published', published
        evidence = w.c.get(f'{V1}/revisions/{rid}/evidence', headers=headers).json()['evidence']
        assert evidence['verdict'] == 'verified', evidence
        step = export(w, rid, 'generator-body-step')
        assert evidence['step_sha256'] == hashlib.sha256(step).hexdigest()
        bom = export(w, rid, 'generator-bom')
        items = json.loads(bom)['items']
        assert sorted(item['quantity'] for item in items) == [1, 10]
        report = json.loads(export(w, rid, 'generator-report'))
        assert report['search']['selected_rotation_deg'] == 90
        assert report['envelope']['measured_mm'] == [480.0, 250.0]
        assert report['mounting']['counts']['pass'] == 40

        from engineering.program.client import ProgramClient
        from engineering.program.replay import capture, replay
        client = ProgramClient(BASE, token=TOKEN, transport=bridge(w, CLIENT))
        package = capture(client, rid)
        assert 'generator-bom.json' in replay(package)['verified_files']
        with pytest.raises(ValueError):
            replay(package[:len(package)//2])
        guard = published['fixed_requirements_sha256']
        change = stage(w, program, SOURCE.replace(b'560 mm', b'561 mm'), rid, guard)
        assert change.status_code == 409 and change.json()['code'] == 'requirements_changed'
        shifted = stage(w, program, SOURCE.replace(b'"nominal"', b'"supplier-shifted"'), rid, guard)
        assert shifted.status_code == 201, shifted.text
        failed_id = review(w, shifted)
        rejected = run(w, failed_id)
        assert rejected['state'] == 'rejected', rejected
        bad_report = json.loads(export(w, failed_id, 'generator-report'))
        failures = [row for row in bad_report['mounting']['correspondences'] if row['outcome'] == 'fail']
        assert len(failures) == 4
        assert {(row['occurrence_id'], row['offset_mm']) for row in failures} == {('module-01', 2.0)}
        assert bad_report['envelope']['outcome'] == 'pass'
        assert stage(w, program, SOURCE).status_code == 409
        assert export(w, rid, 'generator-body-step') == step
        assert export(w, rid, 'generator-bom') == bom
        history = w.c.get(f'{V1}/programs/{program}/history', headers=headers).json()
        assert history['head_revision_id'] == rid


@pytest.mark.skipif(os.name != 'posix', reason='native worker uses process groups')
def test_bay_unit_equivalence_through_adapter(tmp_path, monkeypatch):
    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as w:
        measured = []
        for value in (b'1 m', b'1000 mm'):
            program = w.c.post(f'{V1}/programs', headers=http_headers(CLIENT)).json()['program_id']
            staged = stage(w, program, SOURCE.replace(b'560 mm', value))
            assert staged.status_code == 201, staged.text
            rid = review(w, staged)
            published = run(w, rid)
            assert published['state'] == 'published'
            report = json.loads(export(w, rid, 'generator-report'))
            assert report['envelope']['allowance_mm'][0] == 1000
            measured.append(report['envelope']['measured_mm'])
        assert measured == [[580, 210], [580, 210]]
        rotated = SOURCE.replace(b'560 mm', b'1000 mm').replace(b'= 0 deg', b'= 90 deg')
        guard = published['fixed_requirements_sha256']
        candidate = stage(w, program, rotated, rid, guard)
        assert candidate.status_code == 201, candidate.text
        rotated_id = review(w, candidate)
        new_head = run(w, rotated_id)
        assert new_head['state'] == 'published' and new_head['is_current']
        assert new_head['fixed_requirements_sha256'] == guard
        new_report = json.loads(export(w, rotated_id, 'generator-report'))
        assert new_report['envelope']['measured_mm'] == [480, 250]


def test_bay_interrupted_link_recovers_and_retry_preserves_artifacts(tmp_path, monkeypatch):
    def interrupt(seam):
        if seam == 'after_link_before_start':
            raise RuntimeError('simulated process interruption after the durable job link')

    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as w:
        headers = http_headers(CLIENT)
        pid = w.c.post(f'{V1}/programs', headers=headers).json()['program_id']
        head = review(w, stage(w, pid, SOURCE))
        assert run(w, head)['state'] == 'published'
        step, bom = export(w, head, 'generator-body-step'), export(w, head, 'generator-bom')
        pending = review(w, stage(w, pid, SOURCE, head))
        monkeypatch.setattr(w.service, '_crash_point', interrupt)
        response = w.c.post(f'{V1}/revisions/{pending}/run', headers=headers)
        assert response.status_code == 503
        assert w.c.get(f'{V1}/programs/{pid}', headers=headers).json()['head_revision_id'] == head

    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as w:
        headers = http_headers(CLIENT)
        recovered = w.c.get(f'{V1}/revisions/{pending}', headers=headers).json()['revision']
        assert recovered['state'] == 'cancelled'
        assert export(w, head, 'generator-body-step') == step
        assert export(w, head, 'generator-bom') == bom
        rebuilt = w.c.post(f'{V1}/revisions/{pending}/rebuild',
                           headers={**headers, 'X-Mirum-Expected-Parent': head})
        assert rebuilt.status_code == 200, rebuilt.text
        revision = rebuilt.json()['revision']
        fields = compile_source(SOURCE).brief_content.review_fields
        retry = review(w, rebuilt, fields)
        assert revision['kind'] == 'rebuild'
        assert run(w, retry)['state'] == 'published'
        assert export(w, head, 'generator-body-step') == step
        assert export(w, head, 'generator-bom') == bom
