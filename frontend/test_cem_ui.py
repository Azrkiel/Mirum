"""Real mission-form submission for the opt-in planform search."""
import json

import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize('mode', ['baseline', 'multi_station'])
def test_form_submits_selected_shape_mode_and_keeps_screening_limits(monkeypatch, mode):
    import requests
    from cem.glider.requirements import GliderMissionRequirements
    from cem.glider.calibration import initial
    from cem.glider.feasibility import feasibility
    submitted = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return feasibility(GliderMissionRequirements.model_validate(submitted[-1]['requirements']), initial())
    def post(url, **kwargs):
        assert url.endswith('/cem/glider/feasibility')
        submitted.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    app = AppTest.from_string('from frontend.cem_ui import render_cem\nrender_cem("http://backend", lambda: {})',
                              default_timeout=15).run()
    assert not app.exception
    next(x for x in app.selectbox if x.label == 'Wing planform search').set_value(mode)
    next(x for x in app.button if x.label == 'Check feasibility').click().run()
    assert not app.exception
    assert submitted[-1]['requirements']['discovery']['wing_shape'] == mode
    assert submitted[-1]['requirements']['constraints']['payload']['mass_kg'] == .06
    if mode == 'multi_station':
        assert any('does not cover this wing shape search' in x.value for x in app.info)


def test_advanced_mission_still_overrides_the_shape_form(monkeypatch):
    import requests
    from cem.glider.requirements import GliderMissionRequirements
    from cem.glider.calibration import initial
    from cem.glider.feasibility import feasibility
    submitted = []
    mission = {'launch': {'method': 'drop'}, 'process': 'balsa',
               'discovery': {'wing_shape': 'multi_station', 'budget_evals': 8}}
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return feasibility(GliderMissionRequirements.model_validate(mission), initial())
    def post(url, **kwargs):
        submitted.append(kwargs['json']['requirements'])
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    app = AppTest.from_string('from frontend.cem_ui import render_cem\nrender_cem("http://backend", lambda: {})',
                              default_timeout=15).run()
    next(x for x in app.text_area if x.label.startswith('Mission JSON')).set_value(json.dumps(mission))
    next(x for x in app.button if x.label == 'Check feasibility').click().run()
    assert not app.exception
    assert submitted == [mission]


def test_shaped_elite_submits_archive_bound_comparison_and_renders_inconclusive(monkeypatch):
    import requests
    submitted = []
    elite = dict(elite_id='a'*16, process='sheet_foam', parameters={'wing': {'chord_factors': [1., 1.1, 1., 1., 1.]}},
                 result=dict(tail_kind='conventional', objective_value=120., aspect_ratio=8., wing_loading_g_dm2=5.))
    class Response:
        def __init__(self, value): self.value = value
        def raise_for_status(self): pass
        def json(self): return self.value
    def get(url, **kwargs):
        if url.endswith('b'*32):
            return Response(dict(status='completed', result=dict(kind='shape_comparison', status='inconclusive',
                comparison_sha256='c'*64, objective_delta=None, baseline=dict(result=dict(valid=False, fail_reasons='trim failed')),
                shaped=dict(result=dict(valid=True, fail_reasons='', objective_value=120.)),
                independent_recheck={'state': 'incomplete'}, verified_gain=False)))
        return Response(dict(status='completed', result=dict(per_process={'sheet_foam': {'best': elite, 'archive': [elite]}})))
    def post(url, **kwargs):
        assert url.endswith('/cem/glider/compare-shape')
        submitted.append(kwargs['json'])
        return Response(dict(job_id='b'*32))
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(requests, 'post', post)
    app = AppTest.from_string('from frontend.cem_ui import render_cem\nrender_cem("http://backend", lambda: {})',
                              default_timeout=15)
    app.session_state['cem_job'] = 'a'*32
    app.session_state['cem_status'] = 'completed'
    app.session_state['cem_kind'] = 'discovery'
    app.run()
    assert not app.exception
    next(x for x in app.checkbox if x.label == 'Run independent XFOIL and AVL rechecks').check()
    next(x for x in app.button if x.label == 'Compare shape with fixed hardware').click().run()
    assert not app.exception
    assert submitted == [dict(discovery_job_id='a'*32, elite_id='a'*16, independent_recheck=True)]
    assert any('inconclusive' in x.value for x in app.markdown)
    assert any('No verified performance gain' in x.value for x in app.info)
