"""Glider CEM adapter for /engineering/v1: limit table, report split, fail-closed policy.

Host-runnable (pure Python): no CEM solver runtime is needed. ``cem.api`` (FastAPI) and the
worker are replaced by a fake job manager. The identity test at the end runs only on
Linux (CI native gate) and drives the real worker through the old endpoints and the v1
adapter in one process.
"""
import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
import types
import zipfile
from pathlib import Path

import pytest

from cem.core import canonical_json, digest
from tests.test_engineering_generator_contracts import brief_for, content, glider_spec

SHA = 'a' * 64
ELITE_LOW, ELITE_HIGH = '1' * 16, '2' * 16
PARTS = ('wing_0', 'wing_1', 'spar')


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def adapter():
    from engineering.glider_adapter import GliderAdapter
    return GliderAdapter()


def make_candidate(**requirements):
    brief = brief_for(content('glider_cem', glider_spec(**requirements)))
    return adapter().candidates(brief)[0]


def band(p10, p50, p90):
    return dict(p10=p10, p50=p50, p90=p90)


def part_bytes(name):
    return f'stl:{name}'.encode()


def bundle_zip(parts=PARTS, tamper=None):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name in parts:
            data = part_bytes(name) + (b'!' if name == tamper else b'')
            z.writestr(f'0123456789ab_{name}.stl', data)
        z.writestr('design.json', b'{}')
        z.writestr('performance_report.json', b'{}')
    return out.getvalue()


def design_report(candidate, bundle: bytes, **changes):
    """A screened design report shaped like ``cem.glider.service._package`` output."""
    manifest = [dict(name=n, file=f'0123456789ab_{n}.stl', sha256=sha(part_bytes(n)), material='foam')
                for n in PARTS]
    report = dict(
        requirements_sha256=candidate.candidate_sha256,
        calibration_version=candidate.content.specification['calibration_version'],
        output_id='0123456789ab', cem_version='c' * 64, elite_id=ELITE_HIGH,
        design_revision_sha256='d' * 64, status='screened', minimum_performance_passed=True,
        accepted_for_manufacture=False, engineering_certified=False,
        closure=dict(
            state='passed', failures=[], mass_relative_error=0.012, measured_mass_kg=0.31,
            measured_spar_stress_pa=4.0e7, measured_spar_root_inertia_m4=2.5e-9,
            parts=[dict(name=n) for n in PARTS],
            part_accounting=dict(state='passed'),
            spar_enclosure=dict(state='passed', tolerance_m=2e-7, max_outside_m=0.0,
                                method='delivered-wing-face-halfspaces-v1', load_path_state='unverified'),
            spar_structure=dict(state='passed'),
            reanalysis=dict(valid=True, fail_reasons='',
                            validity_envelope=dict(min_station_reynolds=45000.5, min_confidence=0.91),
                            spar=dict(allowable_with_sf_pa=6.0e7)),
            manufacturing_ready=False),
        uncertainty=dict(samples=200, sustained_samples=0, invalid_samples=1,
                         bands=dict(duration_s=band(8.5, 10.25, 12.0), distance_m=band(80.5, 100.25, 120.75))),
        bundle=dict(parts=manifest, manufacturing_ready=False),
        bundle_sha256=sha(bundle))
    report.update(changes)
    return report


def discovery_report(candidate, candidates=(ELITE_LOW, ELITE_HIGH), **changes):
    objective = {ELITE_LOW: 90.0, ELITE_HIGH: 140.0}
    elites = [dict(elite_id=e, result=dict(objective_value=objective[e])) for e in candidates]
    report = dict(requirements_sha256=candidate.candidate_sha256,
                  calibration_version=candidate.content.specification['calibration_version'],
                  output_id='0123456789ab', cem_version='c' * 64, status='completed', candidates=elites,
                  requirements=candidate.content.specification['requirements'],
                  calibration=dict(state='fake', version=candidate.content.specification['calibration_version']),
                  per_process=dict(balsa=dict(archive=list(elites)), sheet_foam=dict(archive=[])))
    report.update(changes)
    return report


def enc(report) -> bytes:
    return canonical_json(report).encode()


def assembled(candidate=None, *, design_changes=None, tamper=None, discovery_changes=None, candidates=None,
              no_design=False):
    from engineering.glider_adapter import assemble_evaluation
    candidate = candidate or make_candidate()
    bundle = bundle_zip(tamper=tamper)
    discovery = discovery_report(candidate, **({} if candidates is None else dict(candidates=candidates)),
                                 **(discovery_changes or {}))
    design = None if no_design else enc(design_report(candidate, bundle, **(design_changes or {})))
    return candidate, assemble_evaluation(candidate, discovery=enc(discovery), design=design,
                                          bundle=None if no_design else bundle)


def assess(candidate, evaluation):
    from engineering.generator_policy import assess_generator
    return assess_generator(candidate, evaluation)


# --- limit table -------------------------------------------------------------------

def test_the_limit_table_covers_exactly_every_required_check():
    from engineering.capabilities import GLIDER
    for requirements in (dict(), dict(construction='foam_core_wing_v1', process='sheet_foam'),
                         dict(construction='segmented_foam_wing_v1', process='sheet_foam')):
        spec = content('glider_cem', glider_spec(**requirements)).specification
        required = GLIDER.required_checks(spec)
        table = GLIDER.expected_limits(spec)
        assert set(table) == set(required) and len(table) == len(required)
        assert all(rows for rows in table.values())
    assert 'foam_core' in GLIDER.expected_limits(
        content('glider_cem', glider_spec(construction='foam_core_wing_v1', process='sheet_foam')).specification)
    assert 'segmented_structure' in GLIDER.expected_limits(
        content('glider_cem', glider_spec(construction='segmented_foam_wing_v1',
                                          process='sheet_foam')).specification)


def test_limit_values_are_the_recorded_cem_evidence_floors():
    from engineering.capabilities import GLIDER
    spec = content('glider_cem', glider_spec(constraints=dict(max_mass_kg=0.4))).specification
    table = {c: {l.name: (l.comparison, l.value) for l in rows}
             for c, rows in GLIDER.expected_limits(spec).items()}
    assert table['candidate_selection'] == dict(candidates=('ge', 1.0))
    assert table['mass_closure'] == dict(mass_relative_error=('le', 0.05))
    assert table['validity_envelope'] == dict(min_station_reynolds=('ge', 30000.0), min_confidence=('ge', 0.8))
    assert table['uncertainty_bands']['uncertainty_samples'] == ('eq', 200.0)
    assert table['measured_mass_aero']['measured_mass_kg'] == ('le', 0.4)
    assert table['spar_structure']['spar_stress_to_allowable'] == ('le', 1.0 + 1e-5)
    assert table['spar_structure']['measured_spar_root_inertia_m4'] == ('ge', 1e-16)
    assert table['spar_enclosure']['spar_enclosure_tolerance_m'] == ('le', 2e-7)
    assert table['artifact_binding']['bundle_bytes'] == ('le', float(64 * 1024 * 1024))
    assert table['part_accounting']['bundle_parts'] == ('ge', 3.0)


def test_the_table_is_built_from_the_spec_only_and_no_marker_is_left():
    source = (Path(__file__).resolve().parents[1] / 'engineering' / 'capabilities.py').read_text()
    assert 'TODO U6.2' not in source


# --- report -> evidence mapping ----------------------------------------------------

def test_a_screened_report_maps_to_one_passed_check_per_required_id():
    candidate, evaluation = assembled()
    assert [c.check_id for c in evaluation['checks']] == list(candidate.content.required_checks)
    assert all(c.state.value == 'passed' for c in evaluation['checks'])
    assert evaluation['outcome'] == 'accepted' and evaluation['passed'] is True
    decision = assess(candidate, evaluation)
    assert decision.available and decision.outcome.value == 'accepted' and not decision.reasons


def test_values_are_copied_verbatim_from_the_report():
    candidate, evaluation = assembled()
    values = {c.check_id: {m.name: m.value for m in c.measurements} for c in evaluation['checks']}
    assert values['mass_closure'] == dict(mass_relative_error=0.012)
    assert values['validity_envelope'] == dict(min_station_reynolds=45000.5, min_confidence=0.91)
    assert values['candidate_selection']['candidates'] == 2.0
    spar = values['spar_structure']
    assert spar['measured_spar_stress_pa'] == 4.0e7 and spar['allowable_with_sf_pa'] == 6.0e7
    assert spar['measured_spar_root_inertia_m4'] == 2.5e-9
    bands = values['uncertainty_bands']
    assert (bands['duration_p10_s'], bands['duration_p50_s'], bands['duration_p90_s']) == (8.5, 10.25, 12.0)
    assert (bands['distance_p10_m'], bands['distance_p50_m'], bands['distance_p90_m']) == (80.5, 100.25, 120.75)
    assert values['measured_mass_aero']['measured_mass_kg'] == 0.31
    assert values['part_accounting']['bundle_parts'] == 3.0
    assert values['artifact_binding']['artifact_hash_mismatches'] == 0.0


def test_every_check_carries_the_bundle_identity_and_the_pinned_limits():
    candidate, evaluation = assembled()
    table = candidate.content.descriptor.expected_limits(candidate.content.specification)
    zip_bytes = evaluation['artifacts']['generator-bundle.zip']
    for check in evaluation['checks']:
        assert check.limits == table[check.check_id]
        assert [(a.path, a.sha256, a.bytes) for a in check.artifacts] == [
            ('generator-bundle.zip', sha(zip_bytes), len(zip_bytes))]


def test_measurements_parse_the_report_bytes_to_the_same_values():
    candidate, evaluation = assembled()
    parsed = adapter().measurements(evaluation['report_bytes'])
    assert parsed == {c.check_id: {m.name: m.value for m in c.measurements} for c in evaluation['checks']}


def test_the_stated_digests_cover_the_bundle_and_every_part_inside_it():
    candidate, evaluation = assembled()
    assert set(evaluation['artifacts']) == {'generator-bundle.zip', 'generator-result.json'}
    assert evaluation['stated_digests']['generator-bundle.zip'] == design_report(
        candidate, evaluation['artifacts']['generator-bundle.zip'])['bundle_sha256']
    assert sorted(d for _, d in evaluation['extra_digests']) == sorted(sha(part_bytes(n)) for n in PARTS)
    assert evaluation['spec_sha256'] == candidate.candidate_sha256


def test_a_failed_closure_is_a_rejected_report_with_diagnostics_only():
    candidate, evaluation = assembled(design_changes=dict(status='rejected', closure=dict(
        design_report(candidate := make_candidate(), b'')['closure'], state='failed',
        failures=['measured spar fails launch bending'])))
    states = {c.check_id: c.state.value for c in evaluation['checks']}
    assert states['closure'] == 'failed'
    decision = assess(candidate, evaluation)
    assert decision.available and decision.outcome.value == 'rejected' and decision.reasons
    assert not evaluation['artifacts'] and set(evaluation['diagnostics']) == {'generator-result.json'}


def test_a_hard_minimum_miss_fails_only_the_minimum_performance_check():
    candidate, evaluation = assembled(design_changes=dict(status='rejected', minimum_performance_passed=False))
    failed = [c.check_id for c in evaluation['checks'] if c.state.value == 'failed']
    assert failed == ['minimum_performance']
    assert assess(candidate, evaluation).outcome.value == 'rejected'


def test_no_discovery_candidate_fails_selection_with_count_zero_against_one():
    candidate, evaluation = assembled(candidates=(), no_design=True)
    by_id = {c.check_id: c for c in evaluation['checks']}
    selection = by_id['candidate_selection']
    assert selection.state.value == 'failed'
    assert [(m.name, m.value) for m in selection.measurements] == [('candidates', 0.0)]
    assert [(l.comparison, l.value) for l in selection.limits] == [('ge', 1.0)]
    assert all(c.state.value == 'failed' for c in evaluation['checks'])
    decision = assess(candidate, evaluation)
    assert decision.available and decision.outcome.value == 'rejected'
    assert not evaluation['artifacts'] and set(evaluation['diagnostics']) == {'generator-result.json'}


def test_a_screened_report_that_fails_a_floor_is_unavailable_not_rejected():
    from engineering.glider_adapter import GliderInconsistent
    with pytest.raises(GliderInconsistent):
        assembled(design_changes=dict(closure=dict(design_report(make_candidate(), b'')['closure'],
                                                   mass_relative_error=0.2)))


def test_a_rejected_report_with_no_failed_check_is_unavailable():
    from engineering.glider_adapter import GliderInconsistent
    with pytest.raises(GliderInconsistent):
        assembled(design_changes=dict(status='rejected'))


# --- fault injection through assess_generator -----------------------------------------

def test_a_dropped_check_is_unavailable():
    candidate, evaluation = assembled()
    for index in range(len(evaluation['checks'])):
        broken = dict(evaluation, checks=evaluation['checks'][:index] + evaluation['checks'][index + 1:])
        decision = assess(candidate, broken)
        assert not decision.available and decision.outcome.value == 'not_evaluated' and not decision.checks


def test_a_flipped_check_is_rejected_with_diagnostics_only():
    candidate, evaluation = assembled(design_changes=dict(status='rejected', minimum_performance_passed=False))
    decision = assess(candidate, evaluation)
    assert decision.available and decision.outcome.value == 'rejected'
    assert not evaluation['artifacts'] and set(evaluation['diagnostics']) <= set(candidate.content.descriptor.payloads)
    # the same report cannot be presented as accepted
    forged = dict(evaluation, outcome='accepted', passed=True)
    assert not assess(candidate, forged).available


def test_a_changed_byte_in_the_zip_is_unavailable():
    candidate, evaluation = assembled()
    zipped = bytearray(evaluation['artifacts']['generator-bundle.zip'])
    zipped[10] ^= 1
    broken = dict(evaluation, artifacts=dict(evaluation['artifacts'], **{'generator-bundle.zip': bytes(zipped)}))
    assert not assess(candidate, broken).available


def test_a_part_that_differs_from_the_stated_part_digest_is_unavailable():
    candidate, evaluation = assembled(tamper='spar')
    decision = assess(candidate, evaluation)
    assert not decision.available and decision.outcome.value == 'not_evaluated'


def test_a_report_for_another_specification_is_unavailable():
    from engineering.glider_adapter import GliderInconsistent
    with pytest.raises(GliderInconsistent):   # the binding tally fails a screened report
        assembled(design_changes=dict(requirements_sha256='b' * 64))
    candidate, evaluation = assembled()
    assert not assess(candidate, dict(evaluation, spec_sha256='b' * 64)).available


def test_changed_result_bytes_are_unavailable():
    candidate, evaluation = assembled()
    broken = dict(evaluation, artifacts=dict(evaluation['artifacts'], **{'generator-result.json': b'{}'}))
    assert not assess(candidate, broken).available


def test_a_tampered_measurement_is_unavailable_because_it_differs_from_the_report():
    candidate, evaluation = assembled()
    from engineering.contracts import GeneratorCheckResult, Measurement
    check = next(c for c in evaluation['checks'] if c.check_id == 'mass_closure')
    moved = GeneratorCheckResult(**dict(check.model_dump(), measurements=(
        Measurement(name='mass_relative_error', unit='ratio', value=0.001),)))
    checks = tuple(moved if c.check_id == 'mass_closure' else c for c in evaluation['checks'])
    assert not assess(candidate, dict(evaluation, checks=checks)).available


# --- refusal -----------------------------------------------------------------------

@pytest.mark.parametrize('requirements', [
    dict(environment=dict(kind='thermal', thermal=dict(core_uplift_m_s=1.0, radius_m=10.0, distance_m=5.0))),
    dict(environment=dict(kind='ridge', ridge=dict(slope_deg=10.0, wind_m_s=5.0))),
    dict(discovery=dict(families=['conventional', 'canard'])),
    dict(discovery=dict(families='all')),
])
def test_unsupported_requests_are_refused_never_approximated(requirements):
    with pytest.raises(ValueError):
        content('glider_cem', glider_spec(**requirements))


@pytest.mark.parametrize('requirements', [
    dict(environment=dict(kind='thermal', thermal=dict(core_uplift_m_s=1.0, radius_m=10.0, distance_m=5.0))),
    dict(environment=dict(kind='ridge', ridge=dict(slope_deg=10.0, wind_m_s=5.0))),
    dict(discovery=dict(families=['conventional', 'canard'])),
    dict(discovery=dict(families='all')),
])
def test_the_adapter_refuses_again_at_job_time(requirements):
    from engineering.capabilities import UnsupportedCapability
    from engineering.contracts import GeneratorBriefContent
    good = content('glider_cem', glider_spec())
    refused = GeneratorBriefContent.model_construct(
        **dict(good.__dict__, specification=glider_spec(**requirements)))
    fake_brief = types.SimpleNamespace(content=refused)
    with pytest.raises(UnsupportedCapability):
        adapter().candidates(fake_brief)


def test_the_adapter_refuses_a_brief_of_another_capability():
    from engineering.capabilities import UnsupportedCapability
    from tests.test_engineering_generator_contracts import regen_spec
    other = brief_for(content('regen_chamber', regen_spec()))
    with pytest.raises(UnsupportedCapability):
        adapter().candidates(other)


def test_the_candidate_digest_is_the_cem_requirements_digest():
    from cem.glider.requirements import GliderMissionRequirements
    brief = brief_for(content('glider_cem', glider_spec()))
    (candidate,) = adapter().candidates(brief)
    req = GliderMissionRequirements.model_validate(brief.content.specification['requirements'])
    assert candidate.candidate_sha256 == digest(req.model_dump(mode='json'))
    assert candidate.brief_sha256 == brief.brief_sha256


# --- orchestration with a fake job manager -----------------------------------------

class Busy(Exception):
    status_code = 429


class FakeJobs:
    """Same surface as ``cem.api.Jobs`` (``start``, ``completed``, ``task``) over canned results."""
    def __init__(self, root, candidate, *, candidates=(ELITE_LOW, ELITE_HIGH), busy_on=None, hang=False,
                 fail_design=False):
        self.root, self.candidate, self.candidates = root, candidate, candidates
        self.busy_on, self.hang, self.fail_design = busy_on, hang, fail_design
        self.jobs, self.task, self.payloads = {}, None, []
        self.bundle = bundle_zip()

    def start(self, payload):
        self.payloads.append(payload)
        if self.busy_on == len(self.payloads):
            raise Busy('busy')
        key = f'{len(self.payloads):032x}'
        directory = self.root / key
        directory.mkdir()
        kind = payload['kind']
        if kind == 'discovery':
            (directory / 'result.json').write_bytes(enc(discovery_report(self.candidate, self.candidates)))
        else:
            (directory / 'result.json').write_bytes(enc(design_report(self.candidate, self.bundle)))
            (directory / 'bundle.zip').write_bytes(self.bundle)
        status = 'failed' if (kind == 'design' and self.fail_design) else 'completed'
        self.jobs[key] = dict(status=status, directory=directory, kind=kind, identity=dict(output_id='x'))
        self.task = asyncio.ensure_future(asyncio.sleep(3600 if self.hang else 0))
        return dict(job_id=key, status='running')

    def completed(self, key, kind):
        job = self.jobs[key]
        if job['kind'] != kind or job['status'] != 'completed':
            raise Busy('not completed')
        return dict(job, result=json.loads((job['directory'] / 'result.json').read_text()))


@pytest.fixture
def fake_cem(monkeypatch, tmp_path):
    import cem.evidence
    import cem.glider.calibration as calibration
    candidate = make_candidate()
    calls = []
    monkeypatch.setattr(calibration, 'load', lambda version=None: dict(state='fake', version=version))
    monkeypatch.setattr(cem.evidence, 'validate',
                        lambda report, payload, expected, directory: calls.append((payload['kind'], expected, directory)))

    def install(**options):
        jobs = FakeJobs(tmp_path, candidate, **options)
        monkeypatch.setitem(sys.modules, 'cem.api', types.SimpleNamespace(jobs=jobs))
        return jobs
    return types.SimpleNamespace(candidate=candidate, install=install, validated=calls)


def run(coro):
    return asyncio.run(coro)


def test_evaluate_selects_the_highest_objective_elite_and_runs_discovery_then_design(fake_cem):
    jobs = fake_cem.install()
    evaluation = run(adapter().evaluate(fake_cem.candidate))
    discovery, design = jobs.payloads
    assert discovery['kind'] == 'discovery' and design['kind'] == 'design'
    assert design['elite']['elite_id'] == ELITE_HIGH
    assert discovery['calibration'] == design['calibration']
    assert design['requirements'] == discovery['requirements']
    assert [kind for kind, _, _ in fake_cem.validated] == ['discovery', 'design']
    decision = assess(fake_cem.candidate, evaluation)
    assert decision.available and decision.outcome.value == 'accepted'
    assert evaluation['artifacts']['generator-bundle.zip'] == jobs.bundle
    assert jobs.jobs == {}   # the adapter removes its own jobs from the shared manager


def test_the_selection_rule_is_first_maximum_like_glider_cem_size(fake_cem):
    jobs = fake_cem.install(candidates=(ELITE_HIGH, ELITE_LOW))
    run(adapter().evaluate(fake_cem.candidate))
    assert jobs.payloads[1]['elite']['elite_id'] == ELITE_HIGH


def test_no_candidates_starts_no_design_job_and_is_rejected(fake_cem):
    jobs = fake_cem.install(candidates=())
    evaluation = run(adapter().evaluate(fake_cem.candidate))
    assert [p['kind'] for p in jobs.payloads] == ['discovery']
    decision = assess(fake_cem.candidate, evaluation)
    assert decision.available and decision.outcome.value == 'rejected'


@pytest.mark.parametrize('busy_on', [1, 2])
def test_a_busy_manager_is_unavailable(fake_cem, busy_on):
    from engineering.service import EngineeringUnavailable
    fake_cem.install(busy_on=busy_on)
    with pytest.raises(EngineeringUnavailable):
        run(adapter().evaluate(fake_cem.candidate))


def test_a_failed_design_job_is_unavailable(fake_cem):
    from engineering.service import EngineeringUnavailable
    fake_cem.install(fail_design=True)
    with pytest.raises(EngineeringUnavailable):
        run(adapter().evaluate(fake_cem.candidate))


def test_evidence_validation_failure_is_unavailable(fake_cem, monkeypatch):
    import cem.evidence
    from engineering.service import EngineeringUnavailable
    fake_cem.install()

    def refuse(*args):
        raise ValueError('Stale CEM evidence')
    monkeypatch.setattr(cem.evidence, 'validate', refuse)
    with pytest.raises(EngineeringUnavailable) as raised:
        run(adapter().evaluate(fake_cem.candidate))
    assert 'Stale' not in str(raised.value)   # no generator text escapes


def test_cancellation_cancels_the_job_task_and_awaits_it(fake_cem):
    jobs = fake_cem.install(hang=True)

    async def scenario():
        task = asyncio.ensure_future(adapter().evaluate(fake_cem.candidate))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if jobs.task is not None:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return jobs.task
    owned = run(scenario())
    assert owned.cancelled()


def test_a_result_over_the_run_byte_budget_is_unavailable(fake_cem, monkeypatch):
    from engineering import glider_adapter
    from engineering.service import EngineeringUnavailable
    fake_cem.install()
    monkeypatch.setattr(glider_adapter, 'RUN_BYTES', 100)
    with pytest.raises(EngineeringUnavailable):
        run(adapter().evaluate(fake_cem.candidate))


def test_the_adapter_changes_no_budget():
    from engineering import glider_adapter
    from engineering.capabilities import CAPABILITY
    assert glider_adapter.RUN_BYTES == CAPABILITY.resources.run_bytes == 32 * 1024**2


def test_importing_the_adapter_loads_no_native_or_web_modules():
    import subprocess
    code = ('import sys\nbefore = set(sys.modules)\nimport engineering.glider_adapter\n'
            'bad = sorted(m for m in set(sys.modules) - before if m.split(".")[0] in '
            '("fastapi", "cadquery", "OCP", "aerosandbox", "trimesh", "google") or m == "cem.api")\n'
            'assert not bad, bad\n')
    backend = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([backend, str(Path(backend).parent)]))
    subprocess.run([sys.executable, '-c', code], check=True, env=env, cwd=backend)


def test_get_adapter_resolves_the_glider_adapter():
    from engineering.capabilities import GLIDER, get_adapter
    from engineering.glider_adapter import GliderAdapter
    found = get_adapter(GLIDER.id, GLIDER.version, numerical_policy_version=GLIDER.numerical_version)
    assert isinstance(found, GliderAdapter) and found.descriptor is GLIDER


# --- identity: old CEM endpoints versus the v1 adapter (CI native gate, Linux only) ---

@pytest.mark.skipif(os.name != 'posix', reason='CEM worker runs only on Linux')
def test_v1_adapter_matches_the_old_cem_endpoints_byte_for_byte(monkeypatch, tmp_path):
    import time
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from cem import api
    from cem.glider.requirements import GliderMissionRequirements
    from tests.test_cem_pipeline import example
    from engineering.contracts import Outcome
    from engineering.glider_adapter import GliderAdapter
    from engineering.search import search

    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path))
    mission, _, _ = example()
    requirements = mission.model_dump(mode='json')
    requirements['discovery'].update(families=['conventional'], budget_evals=8, top_k=1)
    requirements = GliderMissionRequirements.model_validate(requirements).model_dump(mode='json')
    calibration_version = api.calibration.version(api.calibration.initial())

    # Old path: discover -> elite by the GliderCEM.size rule -> design -> bundle.
    app = FastAPI()
    api.attach_cem_routes(app, Limiter(key_func=get_remote_address, enabled=False))
    manager = api.jobs

    def wait(client, job_id):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            body = client.get(f'/cem/glider/jobs/{job_id}').json()
            if body['status'] != 'running':
                return body
            time.sleep(1)
        raise AssertionError('old CEM job did not finish')

    with TestClient(app) as client:
        started = client.post('/cem/glider/discover', json=dict(requirements=requirements,
                                                                calibration_version=calibration_version))
        assert started.status_code == 202
        discovery_id = started.json()['job_id']
        discovery_body = wait(client, discovery_id)
        assert discovery_body['status'] == 'completed'
        discovery_bytes = (manager.jobs[discovery_id]['directory'] / 'result.json').read_bytes()
        elite = max(discovery_body['result']['candidates'], key=lambda e: e['result']['objective_value'])
        started = client.post('/cem/glider/design', json=dict(discovery_job_id=discovery_id,
                                                              elite_id=elite['elite_id']))
        assert started.status_code == 202
        design_id = started.json()['job_id']
        design_body = wait(client, design_id)
        assert design_body['status'] == 'completed'
        design_directory = manager.jobs[design_id]['directory']
        old_result = (design_directory / 'result.json').read_bytes()
        old_zip = (design_directory / 'bundle.zip').read_bytes()
        bundle_response = client.get(f'/cem/glider/jobs/{design_id}/bundle')
        old_status = design_body['result']['status']
        assert bundle_response.status_code == (200 if old_status == 'screened' else 409)
        if old_status == 'screened':
            assert bundle_response.content == old_zip

    # v1 path in the same process: finalized brief -> search -> adapter.
    from tests.test_engineering_generator_contracts import brief_for, content
    spec = dict(requirements=requirements, calibration_version=calibration_version)
    brief = brief_for(content('glider_cem', spec))
    result = asyncio.run(search(brief, adapter=GliderAdapter()))
    assert result.state in ('selected', 'rejected'), result.reasons
    (attempt,) = result.attempts
    expected_outcome = Outcome.ACCEPTED if old_status == 'screened' else Outcome.REJECTED
    assert attempt.outcome == expected_outcome

    (evaluation,) = result.evaluations
    stated = json.loads(evaluation['report_bytes'])
    assert stated['discovery']['result_sha256'] == sha(discovery_bytes)
    captured = {**evaluation['artifacts'], **evaluation['diagnostics']}
    assert captured['generator-result.json'] == old_result
    identities = {(a.sha256, a.bytes) for check in attempt.checks for a in check.artifacts}
    assert identities == {(sha(old_zip), len(old_zip))}
    if old_status == 'screened':
        assert captured['generator-bundle.zip'] == old_zip
        for name, data in (('old', old_zip), ('v1', captured['generator-bundle.zip'])):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                parts = {n: sha(z.read(n)) for n in sorted(z.namelist()) if n.endswith('.stl')}
            if name == 'old':
                old_parts = parts
        assert parts == old_parts and old_parts
