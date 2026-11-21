"""G06: a calibration-update ask is a clarification, never a refusal (offline, fake provider)."""
from __future__ import annotations

import asyncio
import json

import pytest

from engineering import proposal_contract as pc
from engineering.glider_proposer import AiProposer, CALIBRATION_QUESTION
from engineering.limits import AiCallBudget
from engineering.proposer import ProviderReply, ProviderSettings

G06 = ('Maximize flight duration for a hand-toss glider in the t_tail family. '
       'Use the calibration from our latest flight test.')
G06_FIRST = 'Maximize flight duration for a hand-toss glider in the t_tail family.'
FIELDS = dict(objective='duration_s', launch_method='hand_toss', families=['t_tail'])
CAL_FIELD = 'specification.calibration_version'


class FakeProvider:
    def settings(self):
        return ProviderSettings(provider='fixture', sdk_version='fixture', model='fixture-model',
                                temperature=0, max_tokens=1024)

    def __init__(self, text):
        self.text, self.available = text, True

    async def generate(self, prompt, schema, settings):
        return ProviderReply.model_construct(text=self.text)


def reply(fields=None, **over):
    body = dict(out_of_family=False, fields=fields or {}, questions=[], unsupported_features=[])
    body.update(over)
    return json.dumps(body)


def run(text, prompt=G06):
    budget = AiCallBudget(daily_calls=5, concurrency=1)
    proposal = asyncio.run(AiProposer(budget, FakeProvider(text)).propose(prompt))
    pc.conform('glider_cem', proposal)
    return proposal


def assert_calibration_clarification(proposal):
    assert proposal.state == 'clarification_required'
    assert proposal.reason_id == 'clarification.calibration'
    assert any(i.field == CAL_FIELD and i.reason == CALIBRATION_QUESTION for i in proposal.issues)
    spec = proposal.payload['specification']
    assert 'calibration_version' not in spec
    req = spec['requirements']
    assert req['objective'] == 'duration_s'
    assert req['launch']['method'] == 'hand_toss'
    assert req['discovery']['families'] == ['t_tail']


@pytest.mark.parametrize('feature', [
    'Use the calibration from the latest flight test',
    'Updated flight-test calibration',
    'calibration data from flight testing',
    'Use the calibration source from our latest flight-test record',
])
def test_calibration_feature_becomes_clarification(feature):
    assert_calibration_clarification(run(reply(FIELDS, unsupported_features=[feature])))


def test_calibration_plus_thermal_stays_unsupported():
    p = run(reply(FIELDS, unsupported_features=['Use latest flight test calibration', 'Thermal soaring']))
    assert p.state == 'unsupported' and p.reason_id == 'unsupported.feature'
    assert all('calibration' not in i.reason.lower() for i in p.issues)


def test_calibration_feature_with_motor_stays_unsupported():
    p = run(reply(FIELDS, unsupported_features=['Calibrate the motor speed controller']))
    assert p.state == 'unsupported' and p.reason_id == 'unsupported.feature'


def test_calibration_feature_with_rc_flight_stays_unsupported():
    p = run(reply(FIELDS, unsupported_features=['Calibrate for RC flight from the flight test']))
    assert p.state == 'unsupported' and p.reason_id == 'unsupported.feature'


@pytest.mark.parametrize('feature', [
    'Calibrate from flight test for a rocket-boosted glider',
    'Use the flight test calibration with a jet turbine',
    'Calibrate from flight test for R/C control',
    'Calibrate from flight test for a drone',
    'Use the calibration from our latest flight test and run a python script',
    'Calibrate from flight test for a pоwered glider',
    'Calibrate from flight test for a pow​ered glider',
    'Calibrate from flight test for ＲＣ flight',
    'Use the cаlibration from the latest flight test',
])
def test_calibration_feature_with_any_other_ask_stays_unsupported(feature):
    p = run(reply(FIELDS, unsupported_features=[feature]))
    assert p.state == 'unsupported' and p.reason_id == 'unsupported.feature'


def test_dropped_calibration_with_model_questions_still_asks_calibration():
    p = run(reply({}, questions=['Which launch height?'],
                  unsupported_features=['Use the calibration from the latest flight test']))
    assert p.state == 'clarification_required'
    assert CAL_FIELD in [i.field for i in p.issues]
    assert any(i.reason == CALIBRATION_QUESTION for i in p.issues)


def test_prompt_without_calibration_mention_stays_unsupported():
    p = run(reply(FIELDS, unsupported_features=['Use the calibration from the latest flight test']), G06_FIRST)
    assert p.state == 'unsupported' and p.reason_id == 'unsupported.feature'


def test_empty_features_unchanged():
    assert_calibration_clarification(run(reply(FIELDS)))


def test_out_of_family_wins_over_calibration_feature():
    p = run(reply(FIELDS, out_of_family=True, unsupported_features=['calibration from flight test']))
    assert p.state == 'unsupported' and p.reason_id == 'unsupported.family'
