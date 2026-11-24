"""M1 acceptance through the real HTTP route, OCCT adapter and revision journal."""

from base64 import b64encode
from hashlib import sha256
import json
from pathlib import Path

import pytest

from engineering.language.semantic import compile_source
from tests.test_program_interface_native import poll
from tests.test_program_interface_parity import BASE, TOKEN, V1, bridge, http_headers, parity_app

FIXTURES = Path(__file__).parents[1] / 'engineering' / 'fixtures' / 'editable_plate'
EDGE = (FIXTURES / 'edge.mir').read_bytes()
DATUM = (FIXTURES / 'datum.mir').read_bytes()
CLIENT = 'editable-plate-review'


def create(world):
    response = world.c.post(f'{V1}/programs', headers=http_headers(CLIENT))
    assert response.status_code == 201, response.text
    return response.json()['program_id']


def stage(world, program, source, parent=None):
    headers = http_headers(CLIENT)
    if parent is not None:
        headers['X-Mirum-Expected-Parent'] = parent
    return world.c.post(f'{V1}/programs/{program}/revisions', headers=headers, content=source)


def review(world, response, source=None):
    assert response.status_code in (200, 201), response.text
    staged = response.json()
    revision = staged['revision']
    fields = staged.get('review_fields') if source is None else compile_source(source).brief_content.review_fields
    result = world.c.post(f"{V1}/revisions/{revision['revision_id']}/review", headers=http_headers(CLIENT), json={
        'brief_sha256': revision['brief_sha256'], 'reviewed_by': 'editable plate fixture reviewer',
        'acknowledged_fields': fields})
    assert result.status_code == 200, result.text
    return revision['revision_id']


def run(world, revision):
    result = world.c.post(f'{V1}/revisions/{revision}/run', headers=http_headers(CLIENT))
    assert result.status_code == 200, result.text
    return poll(world, CLIENT, revision)


def edit(source):
    return source.replace(b'80 mm', b'100 mm')


def evidence(world, revision):
    result = world.c.get(f'{V1}/revisions/{revision}/evidence', headers=http_headers(CLIENT))
    assert result.status_code == 200, result.text
    return result.json()['evidence']


def download(world, revision, fmt):
    result = world.c.get(f'{V1}/revisions/{revision}/artifacts/{fmt}', headers=http_headers(CLIENT))
    assert result.status_code == 200, result.text
    assert result.headers['x-content-sha256'] == sha256(result.content).hexdigest()
    return result.content


def accepted(world, revision, *, width, x, margin, notes=None):
    state = run(world, revision)
    assert state['state'] == 'published' and state['is_current'], state
    receipt = evidence(world, revision)
    step = download(world, revision, 'generator-body-step')
    raw_report = download(world, revision, 'generator-report')
    report = json.loads(raw_report)
    assert receipt['revision_id'] == revision
    assert receipt['verdict'] == 'verified', receipt
    assert receipt['step_sha256'] == report['step']['sha256'] == sha256(step).hexdigest()
    assert receipt['candidate_sha256'] == report['candidate_sha256']
    assert state['brief_sha256'] == report['reviewed_brief_sha256']
    assert report['envelope_mm'] == pytest.approx([width, 50, 6], abs=1e-6)
    assert report['datum']['id'] == 'plate.datum'
    assert report['datum']['origin_mm'] == [0, 0, 0]
    assert report['tolerances'] == {'length_mm': 1e-6, 'axis_component': 1e-8, 'volume_mm3': 1e-5}
    assert report['pitch_x_mm'] == pytest.approx(2 * x, abs=1e-6)
    assert report['side_margins_mm'] == pytest.approx([margin, margin], abs=1e-6)
    assert len(report['bores']) == 4
    assert len({row['id'] for row in report['bores']}) == 4
    for row, center in zip(report['bores'], [(-x, -15), (-x, 15), (x, -15), (x, 15)]):
        assert row['center_mm'] == pytest.approx(center, abs=1e-6)
        assert row['diameter_mm'] == pytest.approx(6, abs=1e-6)
        assert row['axis'] == pytest.approx([0, 0, 1], abs=1e-8)
        assert row['z_range_mm'] == pytest.approx([0, 6], abs=1e-6)
        assert row['through'] is True
    if notes is not None:
        notes.mkdir()
        (notes / 'generator-body.step').write_bytes(step)
        (notes / 'generator-report.json').write_bytes(raw_report)
        (notes / 'revision.json').write_text(json.dumps(state, indent=2))
        (notes / 'evidence.json').write_text(json.dumps(receipt, indent=2))
    return step, raw_report, receipt


def preserved(world, program, revision, original, *, current=True):
    if current:
        head = world.c.get(f'{V1}/programs/{program}', headers=http_headers(CLIENT)).json()['head_revision_id']
        assert head == revision
    assert (download(world, revision, 'generator-body-step'),
            download(world, revision, 'generator-report'), evidence(world, revision)) == original


def test_editable_plate_intents_and_delivered_step_gate(tmp_path, monkeypatch):
    from engineering import editable_plate_adapter as adapter
    from engineering.program.client import ProgramClient
    from engineering.program.replay import capture, remeasure, replay

    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as world:
        edge, datum = create(world), create(world)
        first_edge = review(world, stage(world, edge, EDGE))
        first_datum = review(world, stage(world, datum, DATUM))
        original_edge = accepted(world, first_edge, width=80, x=30, margin=10, notes=tmp_path / 'edge-80')
        original_datum = accepted(world, first_datum, width=80, x=30, margin=10, notes=tmp_path / 'datum-80')
        assert original_edge[0] == original_datum[0]

        for field, old, new, code in [
            ('width', b'80 mm', b'100 N', 'MEL-S005'),
            ('width', b'80 mm', b'24 mm', 'MEL-S021'),
            ('datum_ref', b'plate.datum', b'plate.unbound', 'MEL-S020'),
            (None, b'50 mm', b'51 mm', 'requirements_changed'),
        ]:
            refusal = stage(world, edge, EDGE.replace(old, new), first_edge)
            assert refusal.status_code in (409, 422), refusal.text
            result = refusal.json()
            assert result['code'] == code, result
            if field:
                assert result['field'] == field, result
            preserved(world, edge, first_edge, original_edge)

        datum_edit = review(world, stage(world, datum, edit(DATUM), first_datum))
        datum_step, _, _ = accepted(world, datum_edit, width=100, x=30, margin=20, notes=tmp_path / 'datum-100')
        preserved(world, datum, first_datum, original_datum, current=False)

        # Simulate a build publishing authentic STEP bytes with an internally consistent, incorrect report.
        # The normal publication and lease verify the substituted digest; only a fresh read detects geometry.
        native_worker = adapter.worker

        def substitute_step(substitute):
            async def substituted(python, operation, spec, **kwargs):
                result = await native_worker(python, operation, spec, **kwargs)
                if operation == 'build':
                    result['step'] = b64encode(substitute).decode('ascii')
                    result['report']['step'] = {'bytes': len(substitute), 'sha256': sha256(substitute).hexdigest()}
                return result
            return substituted

        from vendor.mirum_editable_plate import Plate
        import cadquery as cq
        plate = Plate(100, 50, 6, 'edge-driven', 'plate.datum', 'mm')
        ambiguous_path = tmp_path / 'ambiguous.step'
        ambiguous = (cq.Workplane('XY').box(100, 50, 6, centered=(True, True, False))
                     .faces('>Z').workplane().pushPoints(list(plate.centers.values())).hole(6)
                     .faces('>Z').workplane().pushPoints([(-40, -15)]).hole(8, 2))
        cq.exporters.export(ambiguous, str(ambiguous_path))
        for step, reason, code, verdict in [
            (datum_step, 'delivered_check_failed', 'feature_missing', 'failed'),
            (ambiguous_path.read_bytes(), 'delivered_binding_ambiguous', 'ambiguous_feature', 'ambiguous'),
        ]:
            with monkeypatch.context() as patched:
                patched.setattr(adapter, 'worker', substitute_step(step))
                pending = review(world, stage(world, edge, edit(EDGE), first_edge))
                failed = run(world, pending)
            assert (failed['state'], failed['reason']) == ('failed', reason), failed
            record = evidence(world, pending)
            assert record['verdict'] == verdict, record
            assert record['step_sha256'] == sha256(step).hexdigest()
            assert 'plate.' + code in {check['check_id'] for check in record['checks']}
            (tmp_path / f'{code}.json').write_text(json.dumps(record, indent=2))
            response = world.c.get(f'{V1}/revisions/{pending}/artifacts/generator-body-step',
                                   headers=http_headers(CLIENT))
            assert response.status_code == 404, response.text
            preserved(world, edge, first_edge, original_edge)

        edge_edit = review(world, stage(world, edge, edit(EDGE), first_edge))
        edge_step, _, _ = accepted(world, edge_edit, width=100, x=40, margin=10, notes=tmp_path / 'edge-100')
        assert edge_step != datum_step
        preserved(world, edge, first_edge, original_edge, current=False)
        refused = stage(world, edge, edit(EDGE), first_edge)
        assert refused.status_code == 409 and refused.json()['code'] == 'stale_parent', refused.text

        client = ProgramClient(BASE, token=TOKEN, transport=bridge(world, CLIENT))
        package = capture(client, edge_edit)
        (tmp_path / 'edge-100' / 'replay.zip').write_bytes(package)
        assert 'generator-body.step' in replay(package)['verified_files']
        assert world.await_(remeasure(package))['native_reread'] == 'consistent'


def test_editable_plate_cancellation_interruption_and_retry(tmp_path, monkeypatch):
    def interrupt(seam):
        if seam == 'after_link_before_start':
            raise RuntimeError('simulated interruption after the durable job link')

    headers = http_headers(CLIENT)
    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as world:
        program = create(world)
        head = review(world, stage(world, program, EDGE))
        original = accepted(world, head, width=80, x=30, margin=10)
        pending = review(world, stage(world, program, edit(EDGE), head))
        cancelled = world.c.post(f'{V1}/revisions/{pending}/cancel', headers=headers)
        assert cancelled.status_code == 200 and cancelled.json()['revision']['state'] == 'cancelled'
        preserved(world, program, head, original)

        interrupted = review(world, stage(world, program, edit(EDGE), head))
        monkeypatch.setattr(world.service, '_crash_point', interrupt)
        response = world.c.post(f'{V1}/revisions/{interrupted}/run', headers=headers)
        assert response.status_code == 503, response.text
        preserved(world, program, head, original)

    with parity_app(tmp_path, monkeypatch, 'native', strict=True) as world:
        recovered = world.c.get(f'{V1}/revisions/{interrupted}', headers=headers).json()['revision']
        assert recovered['state'] == 'cancelled'
        preserved(world, program, head, original)
        rebuilt = world.c.post(f'{V1}/revisions/{interrupted}/rebuild',
                               headers={**headers, 'X-Mirum-Expected-Parent': head})
        retry = review(world, rebuilt, edit(EDGE))
        duplicate = review(world, stage(world, program, edit(EDGE), head))
        accepted(world, retry, width=100, x=40, margin=10)
        assert run(world, retry)['revision_id'] == retry
        refusal = world.c.post(f'{V1}/revisions/{duplicate}/run', headers=headers)
        assert refusal.status_code == 409 and refusal.json()['code'] == 'stale_parent', refusal.text
        assert stage(world, program, edit(EDGE), head).json()['code'] == 'stale_parent'
        history = world.c.get(f'{V1}/programs/{program}/history', headers=headers).json()
        assert history['head_revision_id'] == retry
        assert {row['revision_id'] for row in history['revisions'] if row['state'] == 'published'} == {head, retry}
        preserved(world, program, head, original, current=False)
