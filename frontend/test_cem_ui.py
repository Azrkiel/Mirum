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
    app = AppTest.from_string('from frontend.cem_ui import render_cem\nrender_cem("http://backend", lambda: {})').run()
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
    app = AppTest.from_string('from frontend.cem_ui import render_cem\nrender_cem("http://backend", lambda: {})').run()
    next(x for x in app.text_area if x.label.startswith('Mission JSON')).set_value(json.dumps(mission))
    next(x for x in app.button if x.label == 'Check feasibility').click().run()
    assert not app.exception
    assert submitted == [mission]
