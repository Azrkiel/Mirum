"""U6.3 dragster adapter.

Host-runnable: limit table completeness, report -> evidence mapping from recorded v2 reports,
fault injection through ``assess_generator``, refusals, and ``evaluate`` against stubbed
``dragsters.service``/``artifacts`` modules (no CadQuery, no fastapi, no store). CI only (posix,
native image): the identity test that drives the old ``/dragsters/generate`` endpoint and
``/engineering/v1`` in one process, and the wiring tests through ``create_runtime`` and the
app lifespan. It is named ``test_engineering_*`` so the native gate runs it without a workflow edit.
"""
import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

from backend.tests.test_engineering_generator_contracts import brief_for, content

FIXTURE = Path(__file__).parent / 'fixtures' / 'dragster' / 'v2_reports.json'
STYLES = ('wedge', 'teardrop', 'narrow_waist')
STEP = b'ISO-10303-21; fixture step bytes'
GLB = b'glTF fixture preview bytes'
RUN_ID = 'a' * 32


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def spec_for(style='wedge', **changes):
    from dragsters.styles import load_preset
    return dict(load_preset(style).model_dump(mode='json'), **changes)


def spec_model(style='wedge'):
    from dragsters.spec_v2 import parse_v2
    return parse_v2(json.dumps(spec_for(style)))


def spec_bytes(style='wedge') -> bytes:
    from dragsters.spec_v2 import canonical_spec_v2
    return canonical_spec_v2(spec_model(style)).encode()


def report_bytes_of(report) -> bytes:
    """Exactly what ``dragsters.service.generate`` writes for ``report.json``."""
    return (json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def recorded_report(style='wedge', step=STEP, glb=GLB, **top):
    """A real v2 report (recorded from the three presets), bound to this test's bytes."""
    report = json.loads(FIXTURE.read_text())[style]
    report.update(spec_sha256=sha(spec_bytes(style)), step_sha256=sha(step),
                  revision_id=RUN_ID,
                  coordinate_frame={'cad': 'mm; X nose to rear, Y transverse, Z up', 'preview': 'metres; glTF Y up'})
    report['preview'] = dict(report['preview'], source_step_sha256=sha(step), glb_sha256=sha(glb))
    report.update(top)
    return report


def candidate(style='wedge'):
    from engineering.dragster_adapter import DragsterAdapter
    candidates = DragsterAdapter().candidates(brief_for(content('co2_dragster', spec_for(style))))
    assert len(candidates) == 1
    return candidates[0]


def build(candidate_, report, *, accepted, style='wedge', step=STEP, glb=GLB):
    from engineering import dragster_adapter
    return dragster_adapter.build_evaluation(
        candidate_, report, report_bytes=report_bytes_of(report), spec_bytes=spec_bytes(style),
        step_bytes=step if accepted else None, glb_bytes=glb if accepted else None)


def assess(candidate_, evaluation):
    from engineering.generator_policy import assess_generator
    return assess_generator(candidate_, evaluation)


# --- descriptor and limit table ----------------------------------------------------------

def test_every_required_check_has_a_pinned_limit_entry():
    from engineering import capabilities
    from engineering.capabilities import get_generator
    descriptor = get_generator('co2_dragster')
    for style in STYLES:
        table = descriptor.expected_limits(spec_for(style))
        required = descriptor.required_checks(spec_for(style))
        assert len(required) == 14
        assert set(table) == set(required)
        assert all(table[c] for c in required)
    assert not any('TODO U6.3' in line for line in open(capabilities.__file__, encoding='utf-8'))


def test_the_dragster_is_available_once_its_adapter_exists():
    from engineering.capabilities import adapter_installed, get_generator
    assert adapter_installed(get_generator('co2_dragster'))


def test_limit_values_come_from_the_pinned_constants_and_the_reviewed_spec():
    from dragsters import tolerances
    from engineering.capabilities import get_generator
    descriptor = get_generator('co2_dragster')
    pinned = {r.name: r.value for r in descriptor.numerical}
    for style in STYLES:
        spec = spec_for(style)
        by = {c: {l.name: l for l in limits} for c, limits in descriptor.expected_limits(spec).items()}
        bounds_tol = tolerances.WEDGE_BOUNDS_TOL_MM if style == 'wedge' else tolerances.LOFT_BOUNDS_TOL_MM
        assert by['absolute_bounds']['bounds_deviation'].value == bounds_tol
        assert by['symmetry']['ymin_plus_ymax'].value == bounds_tol
        assert by['symmetry']['ymin_plus_ymax'].comparison == 'abs_le'
        assert by['symmetry']['centroid_y'].value == pinned['CENTROID_Y_TOL_MM']
        assert by['reference_volume']['volume_relative_error'].value == tolerances.LOFT_VOLUME_REL_TOL
        for check in ('pocket_void', 'front_axle_void', 'rear_axle_void'):
            assert by[check]['residual_void'].value == tolerances.VOID_TOLERANCE_MM3
        floor = spec['min_wall_mm'] - tolerances.WALL_DISTANCE_TOL_MM
        for check in ('pocket_wall', 'front_bore_wall', 'rear_bore_wall'):
            assert by[check]['min_wall_mm'].value == floor and by[check]['min_wall_mm'].comparison == 'ge'
        clearance = spec['wheels']['side_clearance_mm'] - tolerances.WALL_DISTANCE_TOL_MM
        for check in ('wheel_clearance_front', 'wheel_clearance_rear'):
            assert by[check]['min_distance_mm'].value == clearance
            assert by[check]['max_intersection_volume_mm3'].value == tolerances.VOID_TOLERANCE_MM3
        assert by['body_above_ground']['zmin_mm'].value == spec['ground_clearance_mm'] - bounds_tol
        assert by['single_valid_solid']['solid_count'].value == 1.0
        assert by['spec_revalidation']['revalidated'].value == 1.0


def test_adapter_units_match_the_limit_table():
    from engineering import dragster_adapter
    from engineering.capabilities import get_generator
    for style in STYLES:
        for limits in get_generator('co2_dragster').expected_limits(spec_for(style)).values():
            for limit in limits:
                assert dragster_adapter.UNITS[limit.name] == limit.unit


# --- report -> evidence ------------------------------------------------------------------

@pytest.mark.parametrize('style', STYLES)
def test_parse_covers_exactly_the_required_checks_with_numeric_values(style):
    from engineering import dragster_adapter
    from engineering.capabilities import get_generator
    parsed = dragster_adapter.parse_report(recorded_report(style))
    assert set(parsed) == set(get_generator('co2_dragster').required_checks(spec_for(style)))
    assert all(type(v) is float for values in parsed.values() for v in values.values())
    assert parsed['single_valid_solid'] == dict(solid_count=1.0)
    assert parsed['spec_revalidation'] == dict(revalidated=1.0)
    assert set(parsed['symmetry']) == {'ymin_plus_ymax', 'centroid_y'}


def test_measurements_parses_the_report_bytes():
    from engineering import dragster_adapter
    report = recorded_report()
    assert dragster_adapter.DragsterAdapter().measurements(report_bytes_of(report)) == \
        dragster_adapter.parse_report(report)


@pytest.mark.parametrize('style', STYLES)
def test_a_recorded_report_is_accepted_with_all_four_artifacts(style):
    cand = candidate(style)
    evaluation = build(cand, recorded_report(style), accepted=True, style=style)
    decision = assess(cand, evaluation)
    assert decision.available and decision.outcome.value == 'accepted', decision.reasons
    assert len(decision.checks) == 14 and all(c.state.value == 'passed' for c in decision.checks)
    assert set(evaluation['artifacts']) == {'generator-spec.json', 'generator-report.json',
                                            'generator-body.step', 'generator-preview.glb'}
    assert evaluation['diagnostics'] == {} and evaluation['artifacts']['generator-body.step'] == STEP
    assert all(c.artifacts and c.artifacts[0].path == 'generator-body.step' for c in evaluation['checks'])
    assert evaluation['spec_sha256'] == cand.candidate_sha256


def test_candidate_identity_is_the_v2_specification_digest_and_the_report_digest():
    from dragsters.spec_v2 import specification_digest_v2
    cand = candidate()
    assert cand.candidate_sha256 == specification_digest_v2(spec_model()) == recorded_report()['spec_sha256']


def test_measurement_values_are_the_reports_numbers():
    from engineering import dragster_adapter
    report = recorded_report('teardrop')
    parsed = dragster_adapter.parse_report(report)
    checks = report['checks']
    assert parsed['pocket_wall']['min_wall_mm'] == checks['pocket_wall']['measured']['min_wall_mm']
    assert parsed['front_bore_wall']['min_wall_mm'] == min(a['wall_mm'] for a in checks['front_bore_wall']['measured']['axes'])
    assert parsed['wheel_clearance_rear']['min_distance_mm'] == min(checks['wheel_clearance_rear']['measured_distance_mm'].values())
    assert parsed['body_above_ground']['zmin_mm'] == checks['body_above_ground']['zmin_mm']
    assert parsed['absolute_bounds']['bounds_deviation'] == max(abs(v) for v in checks['absolute_bounds']['deviation_mm'].values())
    assert parsed['pocket_void']['residual_void'] == abs(checks['pocket_void']['residual_body_mm3'])


# --- fault injection through assess_generator --------------------------------------------

def test_a_dropped_check_makes_the_run_unavailable_with_no_package():
    cand = candidate()
    evaluation = build(cand, recorded_report(), accepted=True)
    evaluation['checks'] = tuple(c for c in evaluation['checks'] if c.check_id != 'pocket_wall')
    decision = assess(cand, evaluation)
    assert not decision.available and decision.outcome.value == 'not_evaluated' and not decision.checks


def test_a_check_dropped_from_the_report_is_unavailable_before_any_evidence_exists():
    from engineering import dragster_adapter
    cand = candidate()
    report = recorded_report()
    del report['checks']['rear_bore_wall']
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        build(cand, report, accepted=True)
    report = recorded_report()
    report['checks']['invented'] = dict(passed=True)
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        build(cand, report, accepted=True)


def test_an_unrequired_stock_check_in_the_report_is_unavailable():
    from engineering import dragster_adapter
    report = recorded_report()
    report['checks']['stock_containment'] = dict(passed=True, margin_mm={}, tolerance_mm=0.01)
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        build(candidate(), report, accepted=True)


def test_one_flipped_check_is_a_rejection_with_only_diagnostics():
    cand = candidate()
    report = recorded_report(passed=False)
    report['checks']['pocket_void'].update(passed=False, measured=0.5, residual_body_mm3=0.5)
    evaluation = build(cand, report, accepted=False)
    decision = assess(cand, evaluation)
    assert decision.available and decision.outcome.value == 'rejected', decision.reasons
    assert {c.check_id for c in decision.checks if c.state.value == 'failed'} == {'pocket_void'}
    assert evaluation['artifacts'] == {}
    assert set(evaluation['diagnostics']) == {'generator-spec.json', 'generator-report.json'}
    assert all(c.artifacts == () for c in evaluation['checks'])
    assert 'generator-body.step' not in evaluation['diagnostics']


def test_a_failing_report_cannot_be_delivered_as_an_accepted_run():
    from engineering import dragster_adapter
    cand = candidate()
    report = recorded_report(passed=False)
    report['checks']['pocket_void'].update(passed=False, measured=0.5, residual_body_mm3=0.5)
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        build(cand, report, accepted=True)
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        build(cand, recorded_report(), accepted=False)     # a passing report is never a rejection


@pytest.mark.parametrize('tamper', ['step', 'glb', 'report', 'spec', 'stated_digest'])
def test_one_changed_byte_makes_the_run_unavailable(tamper):
    cand = candidate()
    evaluation = build(cand, recorded_report(), accepted=True)
    if tamper == 'step':
        evaluation['artifacts']['generator-body.step'] += b'!'
    elif tamper == 'glb':
        evaluation['artifacts']['generator-preview.glb'] += b'!'
    elif tamper == 'report':
        evaluation['artifacts']['generator-report.json'] += b' '
    elif tamper == 'spec':
        evaluation['artifacts']['generator-spec.json'] += b' '
    else:
        evaluation['stated_digests']['generator-body.step'] = 'f' * 64
    assert not assess(cand, evaluation).available


def test_bytes_that_disagree_with_the_report_digests_are_unavailable():
    from engineering import dragster_adapter
    cand = candidate()
    report = recorded_report()
    kwargs = dict(report_bytes=report_bytes_of(report), spec_bytes=spec_bytes(), step_bytes=STEP, glb_bytes=GLB)
    for change in (dict(step_bytes=STEP + b'x'), dict(glb_bytes=GLB + b'x'),
                   dict(spec_bytes=spec_bytes('teardrop')), dict(report_bytes=b'{}\n')):
        with pytest.raises(dragster_adapter.DragsterUnavailable):
            dragster_adapter.build_evaluation(cand, report, **dict(kwargs, **change))


def test_a_report_for_another_style_or_an_unbound_flag_is_unavailable():
    from engineering import dragster_adapter
    cand = candidate()
    for change in (dict(style='teardrop'), dict(engineering_certified=True), dict(schema_version=1)):
        with pytest.raises(dragster_adapter.DragsterUnavailable):
            build(cand, recorded_report(**change), accepted=True)


def test_a_report_that_contradicts_the_reviewed_spec_is_unavailable():
    from engineering import dragster_adapter
    cand = candidate()
    # the report claims pass, but its recorded margins no longer match the reviewed wheels / wall
    report = recorded_report()
    report['checks']['wheel_clearance_front']['side_clearance_mm'] = 0.5
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        build(cand, report, accepted=True)


def test_a_loosened_report_threshold_cannot_pass_a_thin_wall():
    cand = candidate()
    report = recorded_report()
    axes = report['checks']['pocket_wall']['measured']['axes']
    axes[0]['wall_mm'] = 0.2
    report['checks']['pocket_wall']['measured']['min_wall_mm'] = 0.2
    report['checks']['pocket_wall']['threshold'] = 0.0       # the report loosened its own threshold
    # the report claims pass under its own threshold; the pinned floor says otherwise
    assert not assess(cand, build(cand, report, accepted=True)).available


def test_a_state_that_hides_a_measured_violation_is_unavailable():
    cand = candidate()
    report = recorded_report()
    report['checks']['reference_volume']['deviation_mm3'] = 50000.0
    assert not assess(cand, build(cand, report, accepted=True)).available


def test_a_nonfinite_or_missing_measurement_is_unavailable():
    from engineering import dragster_adapter
    report = recorded_report()
    report['checks']['symmetry']['measured']['centroid_y_mm'] = 'nan'
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        dragster_adapter.parse_report(report)
    report = recorded_report()
    del report['checks']['body_above_ground']['zmin_mm']
    with pytest.raises(dragster_adapter.DragsterUnavailable):
        dragster_adapter.parse_report(report)


# --- refusal and candidates --------------------------------------------------------------

def test_version_1_specs_styles_outside_the_registry_and_stock_are_unsupported_issues():
    from engineering.capabilities import get_generator
    descriptor = get_generator('co2_dragster')
    assert [f for f, _ in descriptor.refuse(dict(spec_for(), schema_version=1))] == ['specification.schema_version']
    assert descriptor.refuse(spec_for()) == ()
    stock = dict(spec_for(), stock=dict(length_mm=200.0, width_mm=50.0, height_mm=40.0))
    assert [f for f, _ in descriptor.refuse(stock)] == ['specification.stock']
    for style in STYLES:
        assert descriptor.refuse(spec_for(style)) == ()


def test_revalidation_refuses_unsupported_specifications():
    from engineering import dragster_adapter
    from engineering.capabilities import UnsupportedCapability
    for bad in (dict(spec_for(), schema_version=1), dict(spec_for(), style='fastback'),
                dict(spec_for(), surprise=1), dict(spec_for(), stock=dict(length_mm=200.0, width_mm=50.0, height_mm=40.0))):
        with pytest.raises(UnsupportedCapability):
            dragster_adapter.revalidate(bad)
    assert dragster_adapter.revalidate(spec_for('teardrop')).style == 'teardrop'


def test_candidates_returns_one_candidate_bound_to_the_spec_digest():
    from dragsters.spec_v2 import specification_digest_v2
    from engineering.contracts import GeneratorCandidate
    from engineering.dragster_adapter import DragsterAdapter
    brief = brief_for(content('co2_dragster', spec_for()))
    (cand,) = DragsterAdapter().candidates(brief)
    assert cand.candidate_sha256 == specification_digest_v2(spec_model())
    assert cand == GeneratorCandidate.from_brief(brief, cand.candidate_sha256)


def test_candidates_refuses_another_capability():
    from engineering.capabilities import UnsupportedCapability
    from engineering.dragster_adapter import DragsterAdapter
    from backend.tests.test_engineering_generator_contracts import glider_spec
    with pytest.raises(UnsupportedCapability):
        DragsterAdapter().candidates(brief_for(content('glider_cem', glider_spec())))


def test_adapter_is_registered_and_imports_no_cad_or_web_framework():
    code = ("import sys; import engineering.dragster_adapter as m; "
            "from engineering.capabilities import get_adapter, get_generator; "
            "d=get_generator('co2_dragster'); "
            "a=get_adapter(d.id,d.version,numerical_policy_version=d.numerical_version,"
            "resource_policy_version=d.resources.version); "
            "assert type(a).__name__=='DragsterAdapter'; "
            "bad=[n for n in ('fastapi','cadquery','OCP','trimesh','scipy','google.generativeai','google.genai') if n in sys.modules]; "
            "assert not bad, bad")
    import subprocess
    backend = os.path.join(os.path.dirname(__file__), '..')
    result = subprocess.run([sys.executable, '-c', code], cwd=backend, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=backend))
    assert result.returncode == 0, result.stderr


# --- evaluate, against stubbed service and artifact store --------------------------------

class Stub:
    """Stands in for ``dragsters.service`` and ``artifacts`` (CadQuery stays out of the host)."""

    def __init__(self, tmp_path, monkeypatch, *, style='wedge', error=None, mutate=None):
        self.discarded, self.calls, self.dir = [], [], tmp_path
        report = recorded_report(style)
        self.files = dict(spec=spec_bytes(style), report=report_bytes_of(report), step=STEP, glb=GLB)
        self.report, self.error, self.style = report, error, style
        if mutate:
            mutate(self)
        service = types.ModuleType('dragsters.service')

        class GenerationError(RuntimeError):
            pass
        service.GenerationError = GenerationError
        self.GenerationError = GenerationError
        stub = self

        async def generate(spec, *, runner=None):
            stub.calls.append((spec, runner))
            if stub.error is not None:
                raise GenerationError(stub.error)
            for fmt, data in stub.files.items():
                (stub.dir / fmt).write_bytes(data)
            return dict(run_id=RUN_ID, revision_id=RUN_ID, spec=spec.model_dump(), report=stub.report,
                        downloads={f: True for f in stub.files}, digests={f: sha(d) for f, d in stub.files.items()})
        service.generate = generate
        artifacts = types.ModuleType('artifacts')
        artifacts.artifact_path = lambda run_id, fmt: (stub.dir / fmt) if fmt in stub.files else None
        artifacts.discard = lambda run_id: stub.discarded.append(run_id)
        monkeypatch.setitem(sys.modules, 'dragsters.service', service)
        monkeypatch.setitem(sys.modules, 'artifacts', artifacts)


def run_evaluate(runner=object()):
    from engineering.dragster_adapter import DragsterAdapter
    adapter = DragsterAdapter(lambda: runner)
    return asyncio.run(adapter.evaluate(candidate()))


def test_evaluate_runs_the_v2_service_on_the_shared_runner_and_discards_the_legacy_run(tmp_path, monkeypatch):
    stub = Stub(tmp_path, monkeypatch)
    runner = object()
    evaluation = run_evaluate(runner)
    assert len(stub.calls) == 1 and stub.calls[0][1] is runner and stub.calls[0][0].schema_version == 2
    assert stub.discarded == [RUN_ID]
    decision = assess(candidate(), evaluation)
    assert decision.available and decision.outcome.value == 'accepted', decision.reasons
    assert set(evaluation['artifacts']) == {'generator-spec.json', 'generator-report.json',
                                            'generator-body.step', 'generator-preview.glb'}


def test_evaluate_without_a_runner_is_unavailable():
    from engineering.dragster_adapter import DragsterAdapter, DragsterUnavailable
    with pytest.raises(DragsterUnavailable):
        asyncio.run(DragsterAdapter().evaluate(candidate()))


def test_a_verifier_failure_is_unavailable_and_names_only_required_check_ids(tmp_path, monkeypatch):
    from engineering.dragster_adapter import DragsterUnavailable
    Stub(tmp_path, monkeypatch, error='Dragster worker: STEP checks failed: pocket_void, front_axle_void')
    with pytest.raises(DragsterUnavailable, match=r'pocket_void, front_axle_void'):
        run_evaluate()
    for message in ('Dragster worker: STEP checks failed: pocket_void, drop_table',
                    'Dragster worker: something else <script>', 'Dragster worker: STEP checks failed: '):
        Stub(tmp_path, monkeypatch, error=message)
        with pytest.raises(DragsterUnavailable) as caught:
            run_evaluate()
        assert 'drop_table' not in str(caught.value) and 'script' not in str(caught.value)
        assert 'pocket_void' not in str(caught.value)


def test_nothing_is_discarded_when_no_run_was_published(tmp_path, monkeypatch):
    from engineering.dragster_adapter import DragsterUnavailable
    stub = Stub(tmp_path, monkeypatch, error='Dragster worker: boom')
    with pytest.raises(DragsterUnavailable):
        run_evaluate()
    assert stub.discarded == []


def test_a_published_run_is_discarded_even_when_reading_it_fails(tmp_path, monkeypatch):
    from engineering.dragster_adapter import DragsterUnavailable
    stub = Stub(tmp_path, monkeypatch)
    sys.modules['artifacts'].artifact_path = lambda run_id, fmt: tmp_path / 'absent'
    with pytest.raises(DragsterUnavailable):
        run_evaluate()
    assert stub.discarded == [RUN_ID]


def test_evaluate_refuses_symlinked_and_oversize_files(tmp_path, monkeypatch):
    from engineering.dragster_adapter import DragsterUnavailable, read_bounded
    (tmp_path / 'a.bin').write_bytes(b'x' * 10)
    assert read_bounded(tmp_path / 'a.bin', 10) == b'x' * 10
    with pytest.raises(DragsterUnavailable):
        read_bounded(tmp_path / 'a.bin', 9)
    with pytest.raises(DragsterUnavailable):
        read_bounded(tmp_path / 'missing.bin', 10)
    with pytest.raises(DragsterUnavailable):
        read_bounded(tmp_path, 10)
    try:
        (tmp_path / 'link.bin').symlink_to(tmp_path / 'a.bin')
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(DragsterUnavailable):
        read_bounded(tmp_path / 'link.bin', 10)


def test_evaluate_rejects_bytes_that_differ_from_the_service_result(tmp_path, monkeypatch):
    from engineering.dragster_adapter import DragsterUnavailable

    stub = Stub(tmp_path, monkeypatch)
    service = sys.modules['dragsters.service']
    original = service.generate

    async def lying(spec, *, runner=None):
        result = await original(spec, runner=runner)
        result['digests']['step'] = 'f' * 64
        return result
    service.generate = lying
    with pytest.raises(DragsterUnavailable):
        run_evaluate()
    assert stub.discarded == [RUN_ID]


def test_cancelling_evaluate_propagates_and_leaves_no_run(tmp_path, monkeypatch):
    stub = Stub(tmp_path, monkeypatch)
    service = sys.modules['dragsters.service']

    async def slow(spec, *, runner=None):
        await asyncio.sleep(30)
    service.generate = slow

    async def scenario():
        from engineering.dragster_adapter import DragsterAdapter
        task = asyncio.ensure_future(DragsterAdapter(lambda: object()).evaluate(candidate()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert stub.discarded == []


# --- CI only: identity of the old endpoint and /engineering/v1 -----------------------------

posix_only = pytest.mark.skipif(os.name != 'posix', reason='Linux process groups and rlimits')


@pytest.fixture
def stack(tmp_path, monkeypatch):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from backend.tests.test_native_api_hardening_ports import main, offline_models  # noqa: F401
    import artifacts
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    monkeypatch.setattr(artifacts, '_RUNS_DIR', tmp_path / 'runs')
    monkeypatch.setattr(main.limiter, 'enabled', False)
    with TestClient(main.app) as client:
        main.app.state.limiter.reset()
        yield client, main.app, tmp_path


def old_path(client, spec):
    response = client.post('/dragsters/generate', json=spec)
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[-1]['status'] == 'done', events[-1]
    run_id = events[-1]['run_id']
    files = {fmt: client.get(f'/download/{run_id}/{fmt}').content for fmt in ('spec', 'report', 'step', 'glb')}
    return files


def v1_submit(client, spec, key):
    from engineering.capabilities import get_generator
    from engineering.contracts import BriefDraft
    from backend.tests.test_engineering_generator_contracts import reference
    ref = reference(get_generator('co2_dragster'))
    made = client.post('/engineering/v1/drafts', json={'capability': 'co2_dragster',
                                                       'input': {'capability': ref, 'specification': spec}})
    assert made.status_code == 201, made.text
    draft = BriefDraft.model_validate_json(json.dumps(made.json()['draft']))
    review = dict(draft_id=draft.draft_id, draft_revision=draft.revision, brief_sha256=draft.content.brief_sha256,
                  acknowledged_fields=list(draft.content.review_fields), reviewed_at='2026-11-17T12:05:00Z',
                  reviewed_by='local-user')
    brief = client.post('/engineering/v1/briefs', json=review)
    assert brief.status_code == 201, brief.text
    job = client.post('/engineering/v1/jobs', json={'brief_id': brief.json()['brief_id'], 'idempotency_key': key})
    assert job.status_code == 202, job.text
    return job.json()['job_id']


def v1_wait(client, job_id):
    import time
    for _ in range(1800):
        value = client.get('/engineering/v1/jobs/' + job_id).json()
        if value['lifecycle'] in ('completed', 'unavailable', 'cancelled'):
            return value
        time.sleep(0.1)
    pytest.fail('v1 job did not finish')


def v1_file(client, job_id, format):
    return client.get(f'/engineering/v1/jobs/{job_id}/artifacts/{format}')


def canonical_without(report, *drop):
    report = copy.deepcopy(report)
    for path in drop:
        node = report
        *parents, leaf = path.split('.')
        for part in parents:
            node = node[part]
        node.pop(leaf, None)
    return json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)


def mask_file_name(step: bytes) -> bytes:
    """Only the ``FILE_NAME`` header record (OCCT writes a timestamp there) is masked."""
    return re.sub(rb"FILE_NAME\(.*?\);", b'FILE_NAME(MASKED);', step, count=1, flags=re.S)


def mask_mesh_names(glb: bytes) -> bytes:
    return re.sub(rb'"name":"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"', b'"name":"MASKED"', glb)


def boundary_spec():
    """Smallest ground clearance and side clearance the v2 model admits, on the lofted narrow waist."""
    spec = spec_for('narrow_waist')
    spec['ground_clearance_mm'] = 1.0
    spec['wheels'] = dict(spec['wheels'], side_clearance_mm=0.5)
    return spec


IDENTITY_SPECS = [('wedge', lambda: spec_for('wedge')), ('teardrop', lambda: spec_for('teardrop')),
                  ('narrow_waist', lambda: spec_for('narrow_waist')), ('boundary', boundary_spec)]


@posix_only
@pytest.mark.parametrize('name,make', IDENTITY_SPECS, ids=[n for n, _ in IDENTITY_SPECS])
def test_identity_old_endpoint_and_v1_give_the_same_bytes(stack, name, make):
    client, app, tmp_path = stack
    spec = make()
    first, second = old_path(client, spec), old_path(client, spec)
    result = v1_wait(client, v1_submit(client, spec, f'identity-{name}'))
    assert result['lifecycle'] == 'completed' and result['outcome'] == 'accepted', result
    job_id = result['job_id']
    new = {fmt: v1_file(client, job_id, f'generator-{name_}').content for fmt, name_ in
           dict(spec='spec', report='report', step='body-step', glb='preview-glb').items()}
    assert first['spec'] == second['spec'] == new['spec']
    # preview.glb: byte-equal if the two old runs agree. The exporter names each mesh with a
    # time-based uuid, so otherwise only those names are masked and every other byte must match.
    glb_agree = first['glb'] == second['glb']
    if glb_agree:
        assert new['glb'] == first['glb']
    else:
        assert mask_mesh_names(new['glb']) == mask_mesh_names(first['glb']) == mask_mesh_names(second['glb'])
    steps_agree = first['step'] == second['step']
    # report.json: canonical, with only the random revision_id excluded (plus the digests of bytes
    # that legitimately differ; each of those is instead checked against its own bytes)
    drop = ['revision_id']
    if not steps_agree:
        drop += ['step_sha256', 'preview.source_step_sha256']
    if not glb_agree:
        drop += ['preview.glb_sha256']
    old_reports = [json.loads(f['report']) for f in (first, second)]
    new_report = json.loads(new['report'])
    assert canonical_without(old_reports[0], *drop) == canonical_without(old_reports[1], *drop)         == canonical_without(new_report, *drop)
    for report, files in ((old_reports[0], first), (old_reports[1], second), (new_report, new)):
        assert report['step_sha256'] == sha(files['step']) == report['preview']['source_step_sha256']
        assert report['preview']['glb_sha256'] == sha(files['glb'])
    if steps_agree:
        assert new['step'] == first['step']
    else:
        # OCCT's STEP header timestamp: mask only the FILE_NAME record, then require equal bytes
        assert mask_file_name(new['step']) == mask_file_name(first['step']) == mask_file_name(second['step'])
        for key in ('volume_mm3', 'bounds_mm', 'checks', 'hardware', 'mass'):
            assert old_reports[0][key] == old_reports[1][key] == new_report[key]
    assert [c['state'] for c in result['attempts'][0]['checks']] == ['passed'] * 14


@posix_only
def test_v1_failure_leaves_no_orphan_legacy_run(stack, monkeypatch):
    client, app, tmp_path = stack
    spec = spec_for('wedge')
    result = v1_wait(client, v1_submit(client, spec, 'orphans'))
    assert result['outcome'] == 'accepted'
    runs = tmp_path / 'runs'
    assert not runs.exists() or not any(runs.iterdir())     # the legacy run was discarded after capture


@posix_only
def test_a_dragster_brief_reaches_the_adapter_through_create_runtime(tmp_path, monkeypatch):
    pytest.importorskip('fastapi')
    from engineering.api_v1 import create_runtime
    from engineering.dragster_adapter import DragsterAdapter
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path / 'work'))
    sentinel = object()
    runtime = create_runtime(generators={'co2_dragster': lambda: sentinel})
    try:
        adapter = runtime.jobs._adapter_for(brief_for(content('co2_dragster', spec_for())))
        assert isinstance(adapter, DragsterAdapter) and adapter._runner() is sentinel
    finally:
        runtime.store.close()


@posix_only
def test_the_app_lifespan_registers_the_dragster_adapter_on_the_shared_runner(stack):
    client, app, tmp_path = stack
    adapter = app.state.engineering.jobs.generators['co2_dragster']
    assert type(adapter).__name__ == 'DragsterAdapter'
    assert adapter._runner() is app.state.dragster_runner
    assert 'regen_chamber' in app.state.engineering.jobs.generators
    entries = {e['id']: e for e in client.get('/engineering/v1/capabilities').json()['generators']} \
        if 'generators' in client.get('/engineering/v1/capabilities').json() else {}
    if entries:
        assert entries['co2_dragster']['available'] is True
