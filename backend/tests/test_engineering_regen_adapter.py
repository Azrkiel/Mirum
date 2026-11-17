"""U6.1 regen chamber adapter.

Host-runnable: limit table completeness, report -> evidence mapping, and fault injection
through ``assess_generator`` (no fastapi, no PicoGK, no store). CI only (posix, fastapi): the
identity test that drives the old ``/engineering/regen`` endpoint and ``/engineering/v1``
in one process on one shared runner. It is named ``test_engineering_*`` because
``Dockerfile.verify`` ignores ``test_regen_*``.
"""
import copy
import hashlib
import json
import os
import struct
import sys
import time

import pytest

from backend.tests.test_engineering_generator_contracts import brief_for, content, regen_spec

SPEC_BYTES_NOTE = 'canonical spec bytes'


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def spec_model(**changes):
    from engineering.regen_spec import RegenChamberSpec
    return RegenChamberSpec.model_validate(regen_spec(**changes))


def spec_bytes(**changes) -> bytes:
    from engineering.regen_spec import canonical_spec
    return canonical_spec(spec_model(**changes)).encode()


def candidate():
    from engineering.regen_adapter import RegenAdapter
    candidates = RegenAdapter().candidates(brief_for(content()))
    assert len(candidates) == 1
    return candidates[0]


STL = b'fixture-stl-bytes'


def accepted_report(stl=STL, **top):
    """The format ``regen_mesh.verify`` + ``regen_cli`` write for an accepted run (recorded layout)."""
    from engineering import regen_cli
    from engineering.regen_spec import specification_digest
    spec = spec_model()
    floor = spec.min_feature_mm - spec.voxel_size_mm
    wall = lambda: dict(state='pass', per_station=[2.0] * 7, minimum=2.0, threshold_mm=floor)
    klass = dict(state='pass', violation_count=0, marginal_count=0, too_deep=0, exhausted=False)
    report = dict(
        family='regen_chamber', spec_sha256=specification_digest(spec), stl_sha256=sha(stl),
        raw_stl_sha256='', runtime_so_sha256='',
        measurements=dict(input_triangles=12, cleaned_triangles=12, stations=[], min_feature_mm=spec.min_feature_mm),
        checks=dict(
            components=dict(state='pass', found=1, removed_specks=[], unexpected_components=[],
                            speck_volume_threshold_mm3=0.001),
            edge_manifold=dict(state='pass', edges=18, edges_not_used_twice=0),
            winding=dict(state='pass', directed_edge_conflicts=0, signed_volume_mm3=1000.0),
            bbox=dict(state='pass', max_error_mm=0.1, tolerance_mm=0.8),
            volume=dict(state='pass', measured_mm3=1.0, analytic_mm3=1.0, relative_error=-0.001, tolerance=0.02),
            channel_count=dict(state='pass', per_station=[spec.channel_count] * 7, minimum=spec.channel_count,
                               threshold_mm=None),
            hot_wall=wall(), cold_wall=wall(), rib=wall(),
            circuit_fidelity=dict(state='pass', nonconforming_patches=0, unsplit_over_radius_target=0),
            circuit_coverage=dict(state='pass', failed_samples=0, void_components=1, samples=10),
            wall_thickness=dict(state='pass', classes=dict(wall=klass, rib=dict(klass)), violations=0)),
        failures=[], passed=True, geometry_source='picogk_builder', safety_notice=regen_cli.SAFETY_NOTICE,
        design_intent=dict(state='geometry_matches_spec'), physics=dict(state='not_evaluated'),
        standards=dict(state='not_evaluated'), engineering_certified=False, step_export='not_available')
    report.update(top)
    return report


def real_rejected_report():
    """A real ``regen_mesh.verify`` report: a unit cube against the small preset."""
    np = pytest.importorskip('numpy')
    pytest.importorskip('scipy')
    from engineering import regen_mesh
    from backend.tests.test_regen_mesh import box
    report, _ = regen_mesh.verify(spec_model(), np.asarray(box(0, 0, 0, 1, 1, 1), dtype=np.float32))
    report['geometry_source'] = 'picogk_builder'
    assert report['passed'] is False
    return report


def build(candidate_, report, *, accepted, stl=STL):
    from engineering import regen_adapter
    return regen_adapter.build_evaluation(
        candidate_, report,
        report_bytes=(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n').encode(),
        spec_bytes=spec_bytes(), stl_bytes=stl if accepted else None)


def assess(candidate_, evaluation):
    from engineering.generator_policy import assess_generator
    return assess_generator(candidate_, evaluation)


# --- descriptor and limit table ----------------------------------------------------------

def test_every_required_check_has_a_pinned_limit_entry():
    from engineering.capabilities import get_generator
    descriptor = get_generator('regen_chamber')
    table = descriptor.expected_limits(regen_spec())
    required = descriptor.required_checks(regen_spec())
    assert len(required) == 13 and 'evidence_binding' in required
    assert set(table) == set(required)
    assert all(table[c] for c in required)
    assert not any('TODO U6.1' in line for line in open(
        sys.modules[descriptor.__class__.__module__].__file__, encoding='utf-8'))


def test_limit_values_come_from_the_pinned_constants_and_the_reviewed_spec():
    from engineering.capabilities import get_generator
    from engineering import regen_mesh
    spec = spec_model()
    table = get_generator('regen_chamber').expected_limits(regen_spec())
    by_name = {c: {l.name: l for l in limits} for c, limits in table.items()}
    assert by_name['volume']['volume_relative_error'].value == regen_mesh.VOLUME_TOLERANCE
    assert by_name['bbox']['bbox_max_error'].value == regen_mesh.BBOX_TOLERANCE_VOXELS * spec.voxel_size_mm
    floor = spec.min_feature_mm - spec.voxel_size_mm
    for check in ('hot_wall', 'cold_wall', 'rib'):
        assert by_name[check]['floor_applied'].value == floor
    assert by_name['channel_count']['channel_count'].value == spec.channel_count
    assert by_name['channel_count']['channel_count_max'].value == spec.channel_count


def test_adapter_units_match_the_limit_table():
    from engineering import regen_adapter
    from engineering.capabilities import get_generator
    table = get_generator('regen_chamber').expected_limits(regen_spec())
    for limits in table.values():
        for limit in limits:
            assert regen_adapter.UNITS[limit.name] == limit.unit


# --- report -> evidence ------------------------------------------------------------------

def test_parse_covers_exactly_the_required_checks_with_numeric_values():
    from engineering import regen_adapter
    from engineering.capabilities import get_generator
    parsed = regen_adapter.parse_report(accepted_report())
    assert set(parsed) == set(get_generator('regen_chamber').required_checks(regen_spec()))
    assert all(type(v) is float for values in parsed.values() for v in values.values())
    assert parsed['evidence_binding'] == dict(passed=1.0, geometry_source_picogk_builder=1.0, not_certified=1.0)
    assert parsed['volume'] == dict(volume_relative_error=0.001)


def test_measurements_parses_the_report_bytes():
    from engineering import regen_adapter
    report = accepted_report()
    raw = json.dumps(report).encode()
    assert regen_adapter.RegenAdapter().measurements(raw) == regen_adapter.parse_report(report)


def test_accepted_fixture_is_accepted_with_all_artifacts():
    cand = candidate()
    evaluation = build(cand, accepted_report(), accepted=True)
    decision = assess(cand, evaluation)
    assert decision.available and decision.outcome.value == 'accepted'
    assert len(decision.checks) == 13 and all(c.state.value == 'passed' for c in decision.checks)
    assert set(evaluation['artifacts']) == {'generator-cleaned.stl', 'generator-report.json',
                                            'generator-spec.canonical.json'}
    assert evaluation['diagnostics'] == {} and evaluation['artifacts']['generator-cleaned.stl'] == STL


def test_real_rejected_report_is_a_rejection_with_diagnostics_only():
    cand = candidate()
    report = real_rejected_report()
    evaluation = build(cand, report, accepted=False)
    decision = assess(cand, evaluation)
    assert decision.available and decision.outcome.value == 'rejected', decision.reasons
    states = {c.check_id: c.state.value for c in decision.checks}
    assert states['bbox'] == states['volume'] == states['channel_count'] == 'failed'
    assert states['circuit_fidelity'] == 'failed'
    assert states['circuit_coverage'] == states['wall_thickness'] == 'failed'    # not_evaluated -> failed
    assert states['evidence_binding'] == 'failed' and states['components'] == 'passed'
    assert evaluation['artifacts'] == {}
    assert set(evaluation['diagnostics']) == {'generator-report.json', 'generator-spec.canonical.json'}
    assert json.loads(evaluation['diagnostics']['generator-report.json']) == report


def test_rejected_report_bytes_are_exactly_what_regen_cli_writes():
    from engineering import regen_adapter
    report = real_rejected_report()
    assert regen_adapter.rejected_report_bytes(report) == (
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    assert regen_adapter.rejected_report_bytes(json.loads(json.dumps(report))) == \
        regen_adapter.rejected_report_bytes(report)


# --- fault injection through assess_generator --------------------------------------------

def test_a_dropped_check_makes_the_run_unavailable_with_no_package():
    cand = candidate()
    evaluation = build(cand, accepted_report(), accepted=True)
    evaluation['checks'] = tuple(c for c in evaluation['checks'] if c.check_id != 'rib')
    decision = assess(cand, evaluation)
    assert not decision.available and decision.outcome.value == 'not_evaluated' and not decision.checks


def test_a_check_dropped_from_the_report_is_unavailable_before_any_evidence_exists():
    from engineering import regen_adapter
    cand = candidate()
    report = accepted_report()
    del report['checks']['rib']
    with pytest.raises(regen_adapter.RegenUnavailable):
        build(cand, report, accepted=True)
    report = accepted_report()
    report['checks']['invented'] = dict(state='pass')
    with pytest.raises(regen_adapter.RegenUnavailable):
        build(cand, report, accepted=True)


def test_one_flipped_check_is_a_rejection_with_only_diagnostics():
    cand = candidate()
    report = accepted_report(passed=False, failures=['rib below the floor'])
    report['checks']['rib'].update(state='fail', per_station=[2.0] * 6 + [0.5], minimum=0.5)
    evaluation = build(cand, report, accepted=False)
    decision = assess(cand, evaluation)
    assert decision.available and decision.outcome.value == 'rejected', decision.reasons
    failed = {c.check_id for c in decision.checks if c.state.value == 'failed'}
    assert failed == {'rib', 'evidence_binding'}
    assert evaluation['artifacts'] == {} and set(evaluation['diagnostics']) <= {
        'generator-report.json', 'generator-spec.canonical.json'}


def test_a_failing_report_cannot_be_delivered_as_an_accepted_run():
    from engineering import regen_adapter
    cand = candidate()
    report = accepted_report(passed=False)
    report['checks']['rib'].update(state='fail', per_station=[0.5] * 7, minimum=0.5)
    with pytest.raises(regen_adapter.RegenUnavailable):
        build(cand, report, accepted=True)


@pytest.mark.parametrize('tamper', ['stl', 'report', 'spec', 'stated_digest'])
def test_one_changed_byte_makes_the_run_unavailable(tamper):
    cand = candidate()
    evaluation = build(cand, accepted_report(), accepted=True)
    if tamper == 'stl':
        evaluation['artifacts']['generator-cleaned.stl'] += b'!'
    elif tamper == 'report':
        evaluation['artifacts']['generator-report.json'] += b' '
    elif tamper == 'spec':
        evaluation['artifacts']['generator-spec.canonical.json'] += b' '
    else:
        evaluation['stated_digests']['generator-cleaned.stl'] = 'f' * 64
    assert not assess(cand, evaluation).available


def test_stl_or_spec_that_disagree_with_the_report_digests_are_unavailable():
    from engineering import regen_adapter
    cand = candidate()
    report = accepted_report()
    with pytest.raises(regen_adapter.RegenUnavailable):
        regen_adapter.build_evaluation(cand, report, report_bytes=json.dumps(report).encode(),
                                       spec_bytes=spec_bytes(), stl_bytes=STL + b'x')
    with pytest.raises(regen_adapter.RegenUnavailable):
        regen_adapter.build_evaluation(cand, report, report_bytes=json.dumps(report).encode(),
                                       spec_bytes=spec_bytes(channel_count=25), stl_bytes=STL)


@pytest.mark.parametrize('change', [
    dict(geometry_source='prebuilt_test_hook'), dict(engineering_certified=True), dict(passed=False)])
def test_evidence_binding_fails_for_any_unbound_report(change):
    from engineering import regen_adapter
    cand = candidate()
    parsed = regen_adapter.parse_report(accepted_report(**change))
    assert 0.0 in parsed['evidence_binding'].values()
    # an accepted job whose report fails its own acceptance test never becomes an accepted run
    with pytest.raises(regen_adapter.RegenUnavailable):
        build(cand, accepted_report(**change), accepted=True)


def test_a_loosened_report_threshold_cannot_pass_a_thin_wall():
    cand = candidate()
    report = accepted_report()
    report['checks']['hot_wall'].update(per_station=[0.2] * 7, minimum=0.2, threshold_mm=0.0)
    # the report claims pass under its own threshold; the pinned floor says otherwise
    assert not assess(cand, build(cand, report, accepted=True)).available


def test_a_state_that_hides_a_measured_violation_is_unavailable():
    cand = candidate()
    report = accepted_report()
    report['checks']['volume'].update(relative_error=0.5)
    assert not assess(cand, build(cand, report, accepted=True)).available


# --- refusal and candidates --------------------------------------------------------------

def test_voxel_size_below_0_4_is_an_unsupported_issue():
    from engineering.capabilities import get_generator
    refused = get_generator('regen_chamber').refuse(regen_spec(voxel_size_mm=0.39))
    assert [field for field, _ in refused] == ['specification.voxel_size_mm']
    assert get_generator('regen_chamber').refuse(regen_spec(voxel_size_mm=0.4)) == ()


def test_revalidation_refuses_unsupported_specifications():
    from engineering import regen_adapter
    from engineering.capabilities import UnsupportedCapability
    with pytest.raises(UnsupportedCapability):
        regen_adapter.revalidate(regen_spec(voxel_size_mm=0.3))
    with pytest.raises(UnsupportedCapability):
        regen_adapter.revalidate(dict(regen_spec(), surprise=1))
    assert regen_adapter.revalidate(regen_spec()).channel_count == regen_spec()['channel_count']


def test_candidates_returns_one_candidate_bound_to_the_spec_digest():
    from engineering.contracts import GeneratorCandidate
    from engineering.regen_spec import specification_digest
    from engineering.regen_adapter import RegenAdapter
    brief = brief_for(content())
    (cand,) = RegenAdapter().candidates(brief)
    assert cand.candidate_sha256 == specification_digest(spec_model())
    assert cand == GeneratorCandidate.from_brief(brief, cand.candidate_sha256)


def test_candidates_refuses_another_capability():
    from engineering.capabilities import UnsupportedCapability
    from engineering.regen_adapter import RegenAdapter
    from backend.tests.test_engineering_generator_contracts import glider_spec
    with pytest.raises(UnsupportedCapability):
        RegenAdapter().candidates(brief_for(content('glider_cem', glider_spec())))


def test_adapter_is_registered_and_imports_no_web_framework():
    code = ("import sys; import engineering.regen_adapter as m; "
            "from engineering.capabilities import get_adapter, get_generator; "
            "d=get_generator('regen_chamber'); "
            "a=get_adapter(d.id,d.version,numerical_policy_version=d.numerical_version,"
            "resource_policy_version=d.resources.version); "
            "assert type(a).__name__=='RegenAdapter'; "
            "bad=[n for n in ('fastapi','cadquery','trimesh','scipy') if n in sys.modules]; "
            "assert not bad, bad")
    import subprocess
    backend = os.path.join(os.path.dirname(__file__), '..')
    result = subprocess.run([sys.executable, '-c', code], cwd=backend, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONPATH=backend))
    assert result.returncode == 0, result.stderr


def test_evaluate_without_a_runner_is_unavailable():
    import asyncio
    from engineering.regen_adapter import RegenAdapter, RegenUnavailable
    with pytest.raises(RegenUnavailable):
        asyncio.run(RegenAdapter().evaluate(candidate()))


# --- CI only: identity of the old endpoint and /engineering/v1 -----------------------------

posix_only = pytest.mark.skipif(os.name != 'posix', reason='Linux process groups and rlimits')


def _box_stl() -> bytes:
    corners = [(x, y, z) for z in (0, 1) for y in (0, 1) for x in (0, 1)]
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = []
    for a, b, c, d in quads:
        tris += [(a, b, c), (a, c, d)]
    out = b'box'.ljust(80, b'\0') + struct.pack('<I', len(tris))
    for t in tris:
        out += struct.pack('<12fH', 0, 0, 0, *[float(v) for i in t for v in corners[i]], 0)
    return out


@pytest.fixture
def stack(tmp_path, monkeypatch):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    from backend.tests.test_native_api_hardening_ports import main, offline_models  # noqa: F401
    from engineering import regen_web as web
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'engineering'))
    monkeypatch.setattr(main.limiter, 'enabled', False)
    monkeypatch.setattr(web, '_ready', lambda: True)
    with TestClient(main.app) as client:
        main.app.state.limiter.reset()
        yield client, main.app, web, tmp_path


def accepted_worker(web, monkeypatch, tmp_path, delay=0.0):
    """The fixture-worker idea of test_regen_web, with a complete verifier report."""
    fixtures = tmp_path / 'fixtures'
    fixtures.mkdir(exist_ok=True)
    report = accepted_report()
    (fixtures / 'report.json').write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')
    (fixtures / 'cleaned.stl').write_bytes(STL)
    script = ("import pathlib, shutil, sys, time\n"
              "out, spec, fixtures, delay = pathlib.Path(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3]), float(sys.argv[4])\n"
              "time.sleep(delay)\n"
              "shutil.copy(fixtures / 'cleaned.stl', out / 'cleaned.stl')\n"
              "shutil.copy(fixtures / 'report.json', out / 'report.json')\n"
              "shutil.copy(spec, out / 'spec.canonical.json')\n")
    monkeypatch.setattr(web, '_worker_command', lambda spec, out: [
        sys.executable, '-c', script, str(out), str(spec), str(fixtures), str(delay)])


def rejecting_builder(monkeypatch, tmp_path):
    """The real worker, regen_cli and regen_mesh.verify, with a fake builder writing a fixed STL."""
    folder = tmp_path / 'builder'
    folder.mkdir(exist_ok=True)
    (folder / 'box.stl').write_bytes(_box_stl())
    builder = folder / 'builder'
    builder.write_text(f"#!{sys.executable}\nimport json, shutil, sys\n"
                       f"shutil.copy({str(folder / 'box.stl')!r}, sys.argv[2])\nprint(json.dumps({{'fixture': True}}))\n")
    builder.chmod(0o700)
    (folder / 'picogk.26.2.so').write_bytes(b'fixture runtime')
    monkeypatch.setenv('REGEN_BUILDER', str(builder))


def wait_old(client, job_id):
    for _ in range(600):
        job = client.get(f'/engineering/regen/jobs/{job_id}').json()
        if job['status'] not in ('queued', 'running'):
            return job
        time.sleep(0.1)
    pytest.fail('Old regen job did not finish')


def old_path(client, app, spec):
    response = client.post('/engineering/regen/jobs', json=spec)
    assert response.status_code == 202, response.text
    job = wait_old(client, response.json()['id'])
    files = {}
    if job['status'] == 'accepted':
        files = {name: client.get(url).content for name, url in job['artifacts'].items()}
    return job, files, (app.state.regen_runner.root / job['id'] / 'input.json').read_bytes()


def v1_submit(client, spec, key):
    from engineering.capabilities import get_generator
    from engineering.contracts import BriefDraft
    from backend.tests.test_engineering_generator_contracts import reference
    ref = reference(get_generator('regen_chamber'))
    made = client.post('/engineering/v1/drafts', json={'capability': 'regen_chamber',
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
    for _ in range(900):
        value = client.get('/engineering/v1/jobs/' + job_id).json()
        if value['lifecycle'] in ('completed', 'unavailable', 'cancelled'):
            return value
        time.sleep(0.1)
    pytest.fail('v1 job did not finish')


def v1_file(client, job_id, format):
    return client.get(f'/engineering/v1/jobs/{job_id}/artifacts/{format}')


@posix_only
def test_identity_accepted_old_endpoint_and_v1_give_the_same_bytes(stack, monkeypatch):
    client, app, web, tmp_path = stack
    accepted_worker(web, monkeypatch, tmp_path)
    spec = regen_spec()
    job, files, _ = old_path(client, app, spec)
    assert job['status'] == 'accepted', job
    result = v1_wait(client, v1_submit(client, spec, 'identity-accepted'))
    assert result['lifecycle'] == 'completed' and result['outcome'] == 'accepted', result
    job_id = result['job_id']
    # Only the job id, URLs, ``updated`` and ``message`` are excluded from the comparison.
    assert v1_file(client, job_id, 'generator-report').content == files['report']
    assert v1_file(client, job_id, 'generator-cleaned-stl').content == files['stl']
    assert v1_file(client, job_id, 'generator-spec-canonical').content == files['spec']
    assert json.loads(files['report']) == job['report']
    assert hashlib.sha256(files['stl']).hexdigest() == job['report']['stl_sha256']
    assert result['attempts'][0]['outcome'] == 'accepted'
    assert [c['state'] for c in result['attempts'][0]['checks']] == ['passed'] * 13


@posix_only
def test_identity_rejected_old_endpoint_and_v1_give_the_same_report(stack, monkeypatch):
    client, app, web, tmp_path = stack
    rejecting_builder(monkeypatch, tmp_path)
    spec = regen_spec()
    job, _, old_spec = old_path(client, app, spec)
    assert job['status'] == 'rejected' and job['report'] and not job['artifacts'], job
    result = v1_wait(client, v1_submit(client, spec, 'identity-rejected'))
    assert result['lifecycle'] == 'completed' and result['outcome'] == 'rejected', result
    job_id = result['job_id']
    expected = (json.dumps(job['report'], indent=2, sort_keys=True, allow_nan=False) + '\n').encode()
    assert v1_file(client, job_id, 'generator-report').content == expected
    assert v1_file(client, job_id, 'generator-spec-canonical').content == old_spec
    assert v1_file(client, job_id, 'generator-cleaned-stl').status_code == 404    # no geometry is exposed
    states = {c['check_id']: c['state'] for c in result['attempts'][0]['checks']}
    assert states['bbox'] == 'failed' and states['components'] == 'passed'


@posix_only
def test_v1_shares_the_old_runner_slot_and_a_busy_runner_is_unavailable(stack, monkeypatch):
    client, app, web, tmp_path = stack
    accepted_worker(web, monkeypatch, tmp_path, delay=30)
    assert client.post('/engineering/regen/jobs', json=regen_spec()).status_code == 202
    result = v1_wait(client, v1_submit(client, regen_spec(), 'busy'))
    assert result['lifecycle'] == 'unavailable' and result['outcome'] == 'not_evaluated', result
    assert v1_file(client, result['job_id'], 'generator-report').status_code in (404, 410, 503)


@posix_only
def test_cancelling_a_v1_run_discards_the_result(stack, monkeypatch):
    client, app, web, tmp_path = stack
    accepted_worker(web, monkeypatch, tmp_path, delay=30)
    job_id = v1_submit(client, regen_spec(), 'cancel')
    for _ in range(100):
        if any(j['status'] == 'running' for j in app.state.regen_runner.jobs.values()):
            break
        time.sleep(0.1)
    assert client.post(f'/engineering/v1/jobs/{job_id}/cancel').status_code == 200
    result = v1_wait(client, job_id)
    assert result['lifecycle'] == 'cancelled', result
    assert v1_file(client, job_id, 'generator-cleaned-stl').status_code in (404, 410, 503)
