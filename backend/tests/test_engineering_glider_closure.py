"""Revision and selection binding for the engineering glider report."""
import io
import json
import zipfile

import pytest

from engineering.glider_adapter import BUNDLE, RESULT, GliderInconsistent, assemble_evaluation
from engineering.policy import report_digest
from tests.test_engineering_glider_adapter import assembled, assess, discovery_report, enc, sha


def replace_design_json(bundle: bytes, document: bytes | None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(bundle)) as source, zipfile.ZipFile(out, 'w') as target:
        for name in source.namelist():
            if name != 'design.json':
                target.writestr(name, source.read(name))
        if document is not None:
            target.writestr('design.json', document)
    return out.getvalue()


def assert_revision_rejected(candidate, discovery, design, bundle):
    # Refresh the outer bundle hash to isolate the revision check from artifact binding.
    design = dict(design, status='rejected', bundle_sha256=sha(bundle))
    evaluation = assemble_evaluation(candidate, discovery=enc(discovery), design=enc(design), bundle=bundle)
    assert evaluation['outcome'] == 'rejected'
    assert evaluation['report']['binding'] == dict(mismatches=1, failed=['design revision'])
    assert [check.check_id for check in evaluation['checks'] if check.state.value == 'failed'] == ['design_binding']
    assert evaluation['artifacts'] == {} and evaluation['diagnostics'] == {RESULT: enc(design)}


def test_wrong_report_revision_rejects_the_design():
    candidate, evaluation = assembled()
    design = dict(evaluation['report']['design'], design_revision_sha256='0' * 64)
    assert_revision_rejected(candidate, discovery_report(candidate), design, evaluation['artifacts'][BUNDLE])


def test_wrong_revision_on_a_screened_report_is_unavailable():
    with pytest.raises(GliderInconsistent, match='CEM verdict contradicts'):
        assembled(design_changes=dict(design_revision_sha256='0' * 64))


@pytest.mark.parametrize('changes', [dict(design_revision_sha256='0' * 64), dict(final_placement={})],
                         ids=['bundle-revision', 'placement'])
def test_tampered_design_json_rejects_the_design(changes):
    candidate, evaluation = assembled()
    bundle = evaluation['artifacts'][BUNDLE]
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        document = json.loads(archive.read('design.json'))
    bundle = replace_design_json(bundle, enc(dict(document, **changes)))
    assert_revision_rejected(candidate, discovery_report(candidate), evaluation['report']['design'], bundle)


@pytest.mark.parametrize('document', [None, b'{', b'{}'], ids=['missing', 'invalid-json', 'missing-keys'])
def test_invalid_design_json_fails_revision_binding(document):
    candidate, evaluation = assembled()
    bundle = replace_design_json(evaluation['artifacts'][BUNDLE], document)
    assert_revision_rejected(candidate, discovery_report(candidate), evaluation['report']['design'], bundle)


@pytest.mark.parametrize('field', ['parameters', 'process'])
def test_incomplete_selected_elite_fails_revision_binding(field):
    candidate, evaluation = assembled()
    discovery = discovery_report(candidate)
    selected = max(discovery['candidates'], key=lambda elite: elite['result']['objective_value'])
    del selected[field]
    assert_revision_rejected(candidate, discovery, evaluation['report']['design'], evaluation['artifacts'][BUNDLE])


def test_matching_revisions_pass_design_binding():
    candidate, evaluation = assembled()
    assert evaluation['report']['binding'] == dict(mismatches=0, failed=[])
    decision = assess(candidate, evaluation)
    assert decision.available and decision.outcome.value == 'accepted'


def test_selection_is_from_the_winning_elite_and_bound_to_the_canonical_report():
    candidate, evaluation = assembled()
    discovery = discovery_report(candidate, capabilities=dict(physical_calibration='measured'))
    first, selected = discovery['candidates']
    first.update(process='sheet_foam')   # losing elite: V tail, must not leak into the selection
    evaluation = assemble_evaluation(candidate, discovery=enc(discovery),
                                     design=evaluation['artifacts'][RESULT], bundle=evaluation['artifacts'][BUNDLE])
    expected = dict(family='t_tail', process='balsa', calibration_state='measured')
    report = evaluation['report']
    assert report['selection'] == expected
    assert evaluation['report_bytes'] == enc(report)
    assert json.loads(evaluation['report_bytes'])['selection'] == expected
    assert evaluation['report_sha256'] == report_digest(report) == sha(evaluation['report_bytes'])
    assert evaluation['report_sha256'] != report_digest(dict(report, selection=dict(expected, family='v_tail')))


def test_selection_falls_back_to_calibration_state_and_maps_the_tail_kind_to_the_family():
    _, evaluation = assembled()
    assert evaluation['report']['selection'] == dict(family='t_tail', process='balsa', calibration_state='fake')


def test_discovery_only_still_rejects_with_discovery_diagnostics():
    candidate, evaluation = assembled(candidates=(), no_design=True)
    assert evaluation['report']['selection'] == dict(family=None, process=None, calibration_state='fake')
    assert evaluation['report']['design'] is None
    assert evaluation['report']['binding'] == dict(mismatches=0, failed=[])
    assert evaluation['artifacts'] == {} and evaluation['diagnostics'] == {RESULT: enc(discovery_report(candidate, ()))}
    decision = assess(candidate, evaluation)
    assert decision.available and decision.outcome.value == 'rejected'
