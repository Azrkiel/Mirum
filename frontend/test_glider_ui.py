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
    from backend.gliders.preview import metadata
    monkeypatch.setattr(requests, 'get', lambda *a, **k: Response(metadata() if a[0].endswith('/metadata') else {
        'status': 'completed', 'result': {'feasible': 0, 'evaluated': 8,
         'assumptions': 'Still-air screening estimate', 'rejections': {'too heavy': 8}, 'candidates': []}}))
    app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=15).run()
    app.sidebar.radio(key='workspace_workflow').set_value('glider').run()
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
    from backend.gliders.preview import metadata
    class MetadataResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return metadata()
    monkeypatch.setattr(requests, 'get', lambda *a, **k: MetadataResponse())
    monkeypatch.setattr(requests, 'post', lambda *a, **k: (_ for _ in ()).throw(AssertionError('Unexpected submission')))
    app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=15).run()
    app.sidebar.radio(key='workspace_workflow').set_value('glider').run()
    app.multiselect[0].set_value([]).run()
    next(x for x in app.button if x.label == 'Search glider designs').click().run()
    assert not app.exception
    assert any('Could not start search' in error.value for error in app.error)


def test_candidate_load_preserves_request_and_placement(monkeypatch):
    from backend.gliders.design_spec import preset
    from backend.gliders.preview import metadata
    import requests
    design = preset()
    bootstrap = metadata(design)
    bootstrap['preset']['placement'].update(wing_x=.18, payload_x=.16, ballast_kg=.005, tail_incidence_deg=-2)
    candidate = dict(rank=1, id='candidate', L_over_D=12, glide_distance_m=120,
                     mass_kg=.16, glide_speed_m_s=5, static_margin=.12,
                     CL_trim=.6, CLmax_est=1, tail_incidence_deg=-2,
                     parameters=design.model_dump()['parameters'])
    class Response:
        def __init__(self, data):
            self.data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self.data
    def get(url, **kwargs):
        if url.endswith('/metadata'):
            return Response(metadata())
        if '/design/' in url:
            return Response(bootstrap)
        return Response(dict(status='completed', result=dict(feasible=1, evaluated=8,
            assumptions='Still air', rejections={}, candidates=[candidate])))
    monkeypatch.setattr(requests, 'get', get)
    app = AppTest.from_file(str(Path(__file__).with_name('app.py')), default_timeout=15).run()
    app.sidebar.radio(key='workspace_workflow').set_value('glider').run()
    app.session_state['glider_job'] = 'fixture'
    app.session_state['glider_downloads'] = {}
    app.run()
    next(b for b in app.button if b.label == 'Explore this candidate').click().run()
    assert not app.exception
    assert app.session_state['explorer_bootstrap']['preset']['placement']['wing_x'] == .18
    assert app.session_state['explorer_bootstrap']['preset']['placement']['tail_incidence_deg'] == -2
    assert app.session_state['glider_job'] == 'fixture'
