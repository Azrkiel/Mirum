"""Exercise the main nozzle form and accepted-result delivery, without a native builder."""
import base64
import hashlib
import json
import re
import struct

import pytest
from streamlit.testing.v1 import AppTest

from engineering.regen_presets import EXAMPLES
from engineering.regen_web import BrowserSpec


def mesh_bytes():
    return b'\0' * 80 + struct.pack('<I12fH', 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)


@pytest.fixture
def service(monkeypatch):
    import requests
    state = dict(ready=True, status='queued', calls=[], submitted=[], tamper=False)

    class Response:
        def __init__(self, value=None, content=b''):
            self.value, self.content = value, content
        def raise_for_status(self): pass
        def json(self): return self.value
        def iter_content(self, chunk_size): yield self.content
        def close(self): pass

    def get(url, **kwargs):
        assert kwargs['headers'] == {'X-Mirum-Access-Token': 'private-test-token'}
        state['calls'].append(url)
        if url.endswith('/config'):
            return Response(dict(ready=state['ready'], examples=EXAMPLES,
                schema=BrowserSpec.model_json_schema(), notice='Geometry checks only. Do not manufacture or fire hardware.'))
        job = dict(id='a' * 32, status=state['status'], message='Fixture job', artifacts={}, report=None)
        spec = state['submitted'][-1] if state['submitted'] else EXAMPLES['small']
        canonical = json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()
        report = dict(passed=True, geometry_source='picogk_builder', engineering_certified=False,
            stl_sha256=hashlib.sha256(mesh_bytes()).hexdigest(), spec_sha256=hashlib.sha256(canonical).hexdigest())
        if '/artifacts/' in url:
            name = url.rsplit('/', 1)[-1]
            content = {'stl': mesh_bytes(), 'spec': canonical, 'report': json.dumps(report).encode()}[name]
            return Response(content=b'tampered' if state['tamper'] and name == 'stl' else content)
        if state['status'] == 'accepted':
            job.update(report=report, artifacts={name: f'/engineering/regen/jobs/{job["id"]}/artifacts/{name}'
                                               for name in ('stl', 'report', 'spec')})
        return Response(job)

    def post(url, **kwargs):
        assert url == 'http://backend/engineering/regen/jobs'
        assert kwargs['headers'] == {'X-Mirum-Access-Token': 'private-test-token'}
        state['submitted'].append(kwargs['json'])
        return Response(dict(id='a' * 32, status='queued', message='Queued', artifacts={}, report=None))

    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(requests, 'post', post)
    return state


def form():
    return AppTest.from_string('from frontend.regen_ui import render_regen\n'
        'render_regen("http://backend", lambda: {"X-Mirum-Access-Token": "private-test-token"})',
        default_timeout=15).run()


def generate(app):
    next(b for b in app.button if b.label == 'Generate nozzle mesh').click().run()
    assert not app.exception


def test_form_submits_edited_nozzle_dimensions_with_auth_and_no_model_call(service):
    app = form()
    assert not app.exception
    next(n for n in app.number_input if n.label == 'Channel count').set_value(20)
    generate(app)
    assert service['submitted'][-1] == dict(EXAMPLES['small'], channel_count=20)
    assert app.session_state['regen_job']['status'] == 'queued'
    assert not app.get('download_button')
    assert any('Do not manufacture or fire' in w.value for w in app.warning)


def test_missing_runtime_disables_generation(service):
    service['ready'] = False
    app = form()
    assert not app.exception
    assert next(b for b in app.button if b.label == 'Generate nozzle mesh').disabled
    assert any('PicoGK' in w.value for w in app.warning)
    assert not service['submitted']


def test_main_workflow_selector_opens_nozzle_generator(service, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv('BACKEND_URL', 'http://backend')
    monkeypatch.delenv('MIRUM_ACCESS_GATE', raising=False)
    app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=15)
    app.session_state['access_token'] = 'private-test-token'
    app.run()
    assert not app.exception
    next(r for r in app.radio if r.label == 'Design workflow').set_value('Deterministic rocket nozzle').run()
    assert not app.exception
    assert any(t.value == 'Regenerative cooling rocket nozzle' for t in app.title)
    assert next(b for b in app.button if b.label == 'Generate nozzle mesh')


def test_accepted_result_downloads_hash_bound_artifacts_and_embeds_offline_viewer(service):
    app = form()
    generate(app)
    service['status'] = 'accepted'
    app.run()
    assert not app.exception
    assert len(app.get('download_button')) == 3
    files = app.session_state['regen_files']
    assert files['stl'] == mesh_bytes()
    assert json.loads(files['spec']) == service['submitted'][-1]
    assert len(app.get('iframe')) == 1
    html = app.get('iframe')[0].proto.srcdoc
    assert 'makeViewer' in html and base64.b64encode(mesh_bytes()).decode() in html
    assert 'private-test-token' not in html and 'https://' not in html


def test_tampered_artifact_never_becomes_downloadable(service):
    app = form()
    generate(app)
    service.update(status='accepted', tamper=True)
    app.run()
    assert not app.exception
    assert not app.get('download_button') and not app.get('iframe')
    assert any('match' in e.value for e in app.error)


@pytest.mark.parametrize('status', ['rejected', 'unavailable'])
def test_failed_job_has_no_downloads_or_preview(service, status):
    app = form()
    generate(app)
    service['status'] = status
    app.run()
    assert not app.exception
    assert not app.get('download_button') and not app.get('iframe')
    assert not any('/artifacts/' in u for u in service['calls'])


def test_new_submission_drops_previous_accepted_files(service):
    app = form()
    generate(app)
    service['status'] = 'accepted'
    app.run()
    assert len(app.get('download_button')) == 3
    service['status'] = 'queued'
    generate(app)
    assert not app.get('download_button') and not app.get('iframe')
    assert 'regen_files' not in app.session_state


def test_large_mesh_preview_has_a_bounded_payload_without_changing_download():
    from frontend.regen_ui import _preview_html
    triangle = mesh_bytes()[84:]
    original = b'\0' * 80 + struct.pack('<I', 60001) + triangle * 60001
    original_hash = hashlib.sha256(original).hexdigest()
    html = _preview_html(original)
    preview = base64.b64decode(re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', html)[1])
    count = struct.unpack_from('<I', preview, 80)[0]
    assert 0 < count <= 60000 and len(preview) == 84 + 50 * count
    assert hashlib.sha256(original).hexdigest() == original_hash
