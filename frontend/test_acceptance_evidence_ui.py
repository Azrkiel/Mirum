"""Evidence shown by the UI must match the delivered bytes and declared scope."""
import ast
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from frontend.test_refinement_review_regressions import apply_refinement


def certificate(data):
    return dict(passed=True, scope='structural_solid_set_v1', engineering_certified=False,
                checks={'complete_solid_accounting': True}, failures=[],
                artifacts={'glb_sha256': hashlib.sha256(data).hexdigest()})


def test_refinement_displays_fresh_evidence_instead_of_discarding_it(monkeypatch):
    data = b'changed geometry'
    cert = certificate(data)
    state, _, _, st = apply_refinement(monkeypatch, {
        'status': 'ok', 'glb': base64.b64encode(data).decode(), 'geometry_certificate': cert,
        'script': 'new runnable script', 'regenerated_parts': [], 'reused_parts': [],
    })
    assert state.geometry_certificate == cert
    assert state.struct_report is None
    st.error.assert_not_called()


def test_refinement_rejects_unbound_evidence_without_mutating_accepted_state(monkeypatch):
    state, before, _, st = apply_refinement(monkeypatch, {
        'status': 'ok', 'glb': base64.b64encode(b'changed geometry').decode(),
        'geometry_certificate': certificate(b'other geometry'),
    })
    assert state == before
    st.rerun.assert_not_called()


def test_geometry_card_calls_it_structural_not_engineering_certification():
    app = Path(__file__).with_name('app.py')
    fn = next(n for n in ast.parse(app.read_text(encoding='utf-8')).body
              if isinstance(n, ast.FunctionDef) and n.name == '_render_geometry_cert_card')
    st = SimpleNamespace(expander=MagicMock(), columns=Mock(return_value=[MagicMock() for _ in range(3)]),
                         metric=Mock(), caption=Mock(), markdown=Mock(), error=Mock())
    ns = {'st': st}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(app), 'exec'), ns)
    ns['_render_geometry_cert_card'](certificate(b'fixture'))
    assert 'Structural' in st.expander.call_args.args[0]
    captions = ' '.join(call.args[0] for call in st.caption.call_args_list)
    assert 'not engineering certification' in captions.lower()
    assert 'Complete solid accounting' in st.markdown.call_args.args[0]


import json
from copy import deepcopy
import pytest
from frontend.test_refinement_review_regressions import generated_ui


@pytest.mark.parametrize('variant', ['valid', 'wrong_hash', 'failed'])
def test_generation_adopts_only_matching_current_structural_evidence(monkeypatch, variant):
    state, requests, st, ns, _ = generated_ui(monkeypatch)
    before = deepcopy(dict(state))
    data = b'new generated geometry'
    cert = certificate(data if variant != 'wrong_hash' else b'other geometry')
    if variant == 'failed':
        cert['passed'] = False
    response = MagicMock(status_code=200)
    response.__enter__.return_value = response
    response.iter_lines.return_value = ['data: ' + json.dumps({
        'step': 'complete', 'status': 'done', 'glb': base64.b64encode(data).decode(),
        'geometry_certificate': cert, 'script': 'new source',
    })]
    requests.post.return_value = response
    ns['stream_pipeline']('new part', Mock())
    if variant == 'valid':
        assert state.glb_bytes == data
        assert state.geometry_certificate == cert
    else:
        assert state.glb_bytes == before['glb_bytes']
        assert state.geometry_certificate == before['geometry_certificate']
        st.error.assert_called()
