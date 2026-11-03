"""Exercise the real form-to-API conversion and completed/empty result handling."""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_glider_form_units_submission_and_empty_results(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    import requests
    submitted = []

    class Response:
        status_code = 202

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    def post(url, **kwargs):
        submitted.append(kwargs['json'])
        return Response({'job_id': 'test-job'})

    monkeypatch.setattr(requests, 'post', post)
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response({
        'status': 'completed', 'result': {'feasible': 0, 'evaluated': 8,
         'assumptions': 'Still-air screening estimate', 'rejections': {'too heavy': 8}, 'candidates': []}}))
    app = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run()
    app.radio[0].set_value('Passive glider distance search').run()
    assert not app.exception
    next(x for x in app.button if x.label == 'Search glider designs').click().run()
    assert not app.exception
    assert submitted[0]['payload_mass_kg'] == 0.06
    assert submitted[0]['payload_diameter_m'] == 0.045
    assert submitted[0]['static_margin_min'] == 0.05
    assert submitted[0]['stall_margin'] == 0.85
    from backend.gliders.spec import GliderRequest
    GliderRequest.model_validate(submitted[0])
    assert any('No feasible design' in warning.value for warning in app.warning)


def test_empty_configuration_does_not_submit(monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Unexpected submission')))
    app = AppTest.from_file(str(Path(__file__).with_name('app.py'))).run()
    app.radio[0].set_value('Passive glider distance search').run()
    app.multiselect[0].set_value([]).run()
    next(x for x in app.button if x.label == 'Search glider designs').click().run()
    assert not app.exception
    assert any('Could not start search' in error.value for error in app.error)
