"""E03b plate adapter: registry descriptor, evidence mapping, policy, out-of-process stage.

Host-runnable (pydantic/numpy): ``evaluate`` runs with a fake stage that calls the real
``plate_verify.verify_plate_step`` physics with a faked STEP measurement. The native tests (real
subprocess, cadquery) and the app wiring test are skipped without cadquery / fastapi.
"""
import asyncio
import json
import re
import time

import pytest

from backend.tests.test_engineering_generator_contracts import brief_for, content
from backend.tests.test_engineering_plate_native import plate_data

PLATE_ID = 'rectangular_plate'


def brief_spec(thicknesses=(10.0,), mode=None, **overrides):
    requirements = plate_data(**overrides)
    del requirements['thickness_mm']
    mode = mode or ('fixed_design' if len(thicknesses) == 1 else 'bounded_thickness')
    return dict(mode=mode, requirements=requirements, candidate_thicknesses_mm=list(thicknesses))


def candidate_for(thicknesses=(10.0,), **overrides):
    from engineering.plate_adapter import PlateAdapter
    brief = brief_for(content(PLATE_ID, brief_spec(thicknesses, **overrides)))
    candidates = PlateAdapter().candidates(brief)
    assert len(candidates) == 1
    return candidates[0]


@pytest.fixture
def fake_stage(monkeypatch, tmp_path):
    """Real verify_plate_step physics; only the OCP STEP measurement is faked."""
    from engineering import plate_verify as pv
    state = dict(calls=[], measure=None)

    def measure(path):
        return dict(state['measure'] or {})

    monkeypatch.setattr(pv, 'measure_step', measure)

    async def stage(spec):
        a, b, h = spec.length_a_mm, spec.width_b_mm, spec.thickness_mm
        state['measure'] = dict(readable=True, solids=1, faces=6, planar_faces=6, axis_aligned_faces=6,
                                bbox_min=[0.0, 0.0, 0.0], length_a_mm=a, width_b_mm=b, thickness_mm=h,
                                volume_mm3=a * b * h)
        state['calls'].append(spec.thickness_mm)
        path = tmp_path / f'plate_{len(state["calls"])}.step'
        path.write_bytes(b'ISO-10303-21; fake plate ' + repr(h).encode())
        return pv.verify_plate_step(path, spec), path.read_bytes()

    state['stage'] = stage
    return state


def run(adapter, candidate):
    return asyncio.run(adapter.evaluate(candidate))


def adapter_with(stage):
    from engineering.plate_adapter import PlateAdapter
    return PlateAdapter(stage=stage)


# ----------------------------------------------------------------- descriptor

def test_descriptor_registered_last_with_checked_design_claim():
    from engineering.capabilities import CAPABILITY, GENERATORS, PLATE, list_capabilities
    assert GENERATORS[-1] is PLATE and [g.id for g in GENERATORS][:3] == ['co2_dragster', 'regen_chamber', 'glider_cem']
    assert list_capabilities()[0] is CAPABILITY and list_capabilities()[-1] is PLATE
    assert (PLATE.id, PLATE.version, PLATE.claim) == (PLATE_ID, '1.0.0', 'checked_design')
    assert PLATE.numerical_version == 'plate_numerical_v1' and PLATE.engineering_certified is False
    assert PLATE.standards == 'not_evaluated' and PLATE.proposers == {} and 'ai' not in PLATE.proposers
    assert PLATE.selection_rule == 'smallest passing thickness in the reviewed set'
    assert PLATE.spec_schema == 'engineering.plate_spec:PlateBrief'
    assert PLATE.diagnostic_payloads == ('generator-report.json', 'generator-spec.canonical.json')
    assert PLATE.primary_artifact in PLATE.payloads and PLATE.unsupported_features and PLATE.limitations


def test_required_checks_are_the_guards_and_checks_in_execution_order():
    from engineering.capabilities import PLATE
    from engineering.plate_spec import CHECK_IDS, GUARD_IDS
    assert len(PLATE.base_checks) == 13 and set(PLATE.base_checks) == set(GUARD_IDS) | set(CHECK_IDS)
    assert PLATE.base_checks[:4] == ('thin_ratio', 'aspect_ratio', 'material', 'geometry')


def test_numerical_references_come_from_plate_numerical_v1():
    from engineering.capabilities import PLATE
    from engineering.plate_spec import PLATE_NUMERICAL_V1
    scalars = {k: v for k, v in PLATE_NUMERICAL_V1.items() if isinstance(v, (int, float, str))}
    assert {r.name: r.value for r in PLATE.numerical} == scalars
    assert {r.source for r in PLATE.numerical} == {'backend/engineering/plate_spec.py:PLATE_NUMERICAL_V1'}
    runtime = {r.name: r.value for r in PLATE.runtime_limits}
    assert runtime == {'ProcessRunner.timeout_s': 30, 'ProcessRunner.memory_mb': 1536,
                       'ProcessRunner.output_limit_bytes': 1048576}


def test_expected_limits_cover_all_checks_and_follow_the_brief():
    from engineering.capabilities import PLATE
    from engineering.plate_spec import PLATE_NUMERICAL_V1 as P
    table = PLATE.expected_limits(brief_spec(allowable_stress_mpa=123.0, max_deflection_mm=1.5))
    assert set(table) == set(PLATE.base_checks)
    assert all(rows[0].name == 'evaluated' and rows[0].comparison == 'eq' and rows[0].value == 1 for rows in table.values())
    by = {c: {l.name: l for l in rows} for c, rows in table.items()}
    assert by['stress_limit']['von_mises_max_mpa'].value == 123.0
    assert by['deflection_limit']['deflection_with_shear_allowance_mm'].value == 1.5
    assert by['thin_ratio']['width_to_thickness'].value == P['thin_ratio_min']
    assert by['aspect_ratio']['aspect_ratio_upper'].value == P['aspect_ratio_max']
    assert by['equilibrium']['equilibrium_relative_error'].value == P['equilibrium']
    assert by['geometry']['dimension_error_mm'].value == 0.01


def test_candidates_return_one_candidate_over_the_whole_brief():
    from engineering.plate_adapter import canonical_brief, PlateAdapter
    from engineering.plate_spec import PlateBrief
    from engineering.capabilities import PLATE
    import hashlib
    spec = brief_spec((12.0, 8.0, 10.0))
    brief = brief_for(content(PLATE_ID, spec))
    (candidate,) = PlateAdapter().candidates(brief)
    parsed = PLATE.parse_spec(candidate.content.specification)
    assert isinstance(parsed, PlateBrief) and parsed.candidate_thicknesses_mm == (8.0, 10.0, 12.0)
    assert candidate.candidate_sha256 == hashlib.sha256(canonical_brief(parsed)).hexdigest()


def test_adapter_resolves_through_the_registry():
    from engineering.capabilities import PLATE, get_adapter
    adapter = get_adapter(PLATE.id, PLATE.version, numerical_policy_version=PLATE.numerical_version,
                          resource_policy_version=PLATE.resources.version)
    assert adapter.descriptor is PLATE


# ------------------------------------------------------------------ evaluate

@pytest.mark.parametrize('edge', ['simply_supported', 'clamped'])
def test_accepted_plate_passes_policy(fake_stage, edge):
    from engineering.policy import assess
    candidate = candidate_for((10.0,), edge_condition=edge)
    result = run(adapter_with(fake_stage['stage']), candidate)
    assert result['outcome'] == 'accepted' and result['passed'] is True
    assert set(result['artifacts']) == {'generator-body.step', 'generator-report.json', 'generator-spec.canonical.json'}
    assert all(c.state.value == 'passed' for c in result['checks']) and len(result['checks']) == 13
    decision = assess(candidate, result)
    assert decision.available and decision.outcome.value == 'accepted', decision.reasons
    report = result['report']
    assert (report['corner_forces'] is not None) == (edge == 'simply_supported')
    attempt = report['attempts'][0]['verify']
    assert attempt['mesh_history'] and attempt['references']['method'] and report['numerical_policy_version'] == 'plate_numerical_v1'
    assert report['implementation_sha256'] and report['selected_thickness_mm'] == 10.0


def test_bounded_set_selects_the_smallest_passing_thickness(fake_stage):
    from engineering.policy import assess
    candidate = candidate_for((10.0, 5.0, 8.0, 6.0), max_deflection_mm=0.3)
    result = run(adapter_with(fake_stage['stage']), candidate)
    assert fake_stage['calls'] == [5.0, 6.0, 8.0]          # ascending, stops at the first pass, never runs 10
    assert result['outcome'] == 'accepted' and result['report']['selected_thickness_mm'] == 8.0
    assert [a['passed'] for a in result['report']['attempts']] == [False, False, True]
    assert assess(candidate, result).outcome.value == 'accepted'


def test_all_candidates_failing_is_a_rejection_naming_the_check(fake_stage):
    from engineering.policy import assess
    candidate = candidate_for((8.0, 10.0), max_deflection_mm=0.01)
    result = run(adapter_with(fake_stage['stage']), candidate)
    assert fake_stage['calls'] == [8.0, 10.0]
    assert result['outcome'] == 'rejected' and not result['artifacts']
    assert set(result['diagnostics']) == {'generator-report.json', 'generator-spec.canonical.json'}
    decision = assess(candidate, result)
    assert decision.available and decision.outcome.value == 'rejected'
    assert decision.reasons[0].startswith('deflection_limit:')
    assert len(result['report']['attempts']) == 2 and result['report']['selected_thickness_mm'] is None


def test_guard_rejection_names_the_guard_and_later_checks_are_not_evaluated(fake_stage):
    from engineering.policy import assess
    candidate = candidate_for((25.0,))                     # b/h = 16 < 20
    result = run(adapter_with(fake_stage['stage']), candidate)
    decision = assess(candidate, result)
    assert decision.available and decision.outcome.value == 'rejected'
    assert decision.reasons[0] == 'thin_ratio: b/h = 16 < 20'
    by = {c.check_id: c for c in result['checks']}
    assert by['thin_ratio'].state.value == 'failed'
    for later in ('geometry', 'small_deflection', 'stress_limit'):
        assert by[later].state.value == 'failed' and by[later].reason == 'not evaluated: rejected earlier by thin_ratio'
        assert dict((m.name, m.value) for m in by[later].measurements)['evaluated'] == 0
    assert fake_stage['calls'] == [25.0]


def test_small_deflection_guard_rejection(fake_stage):
    from engineering.policy import assess
    candidate = candidate_for((50.0,), length_a_mm=1000.0, width_b_mm=1000.0, pressure_mpa=10.0,
                              max_deflection_mm=20.0, allowable_stress_mpa=100000.0)
    result = run(adapter_with(fake_stage['stage']), candidate)
    decision = assess(candidate, result)
    assert decision.available and decision.outcome.value == 'rejected'
    assert decision.reasons[0].startswith('small_deflection:')


def test_policy_rejects_a_tampered_check(fake_stage):
    from engineering.policy import assess
    candidate = candidate_for((10.0,))
    result = run(adapter_with(fake_stage['stage']), candidate)
    report = json.loads(result['report_bytes'])
    report['attempts'][-1]['verify']['checks']['stress_limit']['quantity'] *= 2
    result = dict(result, report_bytes=json.dumps(report).encode(), report=report)
    assert not assess(candidate, result).available


def test_report_has_no_filesystem_paths(fake_stage, tmp_path):
    candidate = candidate_for((10.0,))
    result = run(adapter_with(fake_stage['stage']), candidate)
    text = result['report_bytes'].decode()
    assert str(tmp_path) not in text and not re.search(r'[A-Za-z]:[\\/]|/tmp/|/home/|\\\\', text)


@pytest.mark.parametrize('mutate', [
    lambda r, s: ({**r, 'spec_sha256': '0' * 64}, s),
    lambda r, s: ({k: v for k, v in r.items() if k != 'guards'}, s),
    lambda r, s: ({**r, 'passed': 1}, s),
    lambda r, s: (r, b''),
    lambda r, s: (r, s + b'x'),
    lambda r, s: ('not a report', s),
])
def test_malformed_stage_output_is_unavailable(fake_stage, mutate):
    from engineering.service import EngineeringUnavailable

    async def bad(spec):
        return mutate(*await fake_stage['stage'](spec))
    with pytest.raises(EngineeringUnavailable):
        run(adapter_with(bad), candidate_for((10.0,)))


def test_stage_failure_is_unavailable(fake_stage):
    from engineering.service import EngineeringUnavailable

    async def boom(spec):
        raise RuntimeError('native crash with /secret/path')
    with pytest.raises(EngineeringUnavailable) as caught:
        run(adapter_with(boom), candidate_for((10.0,)))
    assert 'secret' not in str(caught.value)


def test_read_bounded_refuses_oversize_and_missing(tmp_path):
    from engineering.plate_adapter import read_bounded
    from engineering.service import EngineeringUnavailable
    big = tmp_path / 'big'
    big.write_bytes(b'x' * 11)
    assert read_bounded(big, 11) == b'x' * 11
    with pytest.raises(EngineeringUnavailable):
        read_bounded(big, 10)
    with pytest.raises(EngineeringUnavailable):
        read_bounded(tmp_path / 'missing', 10)


def test_nonzero_exit_and_malformed_report_are_unavailable(monkeypatch, tmp_path):
    """The native stage with a stub runner (no subprocess, no cadquery)."""
    import types
    import sys
    from engineering.plate_adapter import PlateAdapter
    from engineering.plate_spec import PlateBrief
    from engineering.service import EngineeringUnavailable
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.setitem(sys.modules, 'compiler', types.SimpleNamespace(_WORK_DIR=work))
    spec = PlateBrief.model_validate_json(json.dumps(brief_spec((10.0,)))).candidates()[0]

    class Runner:
        def __init__(self, outcome, write=None):
            self.outcome, self.write = outcome, write

        async def run(self, argv, cwd, env):
            assert 'PYTHONPATH' not in env and argv[1].endswith('plate_adapter.py')
            if self.write:
                from pathlib import Path
                Path(argv[3]).write_bytes(b'STEP')
                Path(argv[4]).write_bytes(self.write)
            return self.outcome

    for outcome, write in (({'returncode': 1}, None), ({'code': 'timeout', 'returncode': None}, None),
                           ({'returncode': 0}, None), ({'returncode': 0}, b'{not json'),
                           ({'returncode': 0}, b'{"x": NaN}'), ({'returncode': 0}, b'x' * 1048577)):
        adapter = PlateAdapter(runner=lambda o=outcome, w=write: Runner(o, w))
        with pytest.raises(EngineeringUnavailable):
            asyncio.run(adapter._native_stage(spec))


def test_api_runtime_registers_the_plate_adapter(tmp_path, monkeypatch):
    pytest.importorskip('fastapi')
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path / 'work'))
    from engineering.api_v1 import create_runtime, generator_entry
    from engineering.capabilities import PLATE
    runtime = create_runtime()
    try:
        assert PLATE.id in runtime.jobs.generators
        entry = generator_entry(PLATE)
        assert entry['claim'] == 'checked_design' and entry['available'] is True
    finally:
        runtime.store.close()


# -------------------------------------------------------------------- native

def test_native_ss_plate_runs_through_the_real_subprocess(tmp_path, monkeypatch):
    import os
    try:
        import cadquery, OCP  # noqa: F401
    except ImportError:
        pytest.skip('cadquery/OCP unavailable')
    if os.name != 'posix':
        pytest.skip('Linux process groups and rlimits')
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path))
    from engineering.plate_adapter import PlateAdapter
    from engineering.policy import assess
    candidate = candidate_for((10.0,))
    started = time.monotonic()
    result = asyncio.run(PlateAdapter().evaluate(candidate))
    seconds = time.monotonic() - started
    print(f'NATIVE_PLATE_WALL_SECONDS={seconds:.2f}')
    assert result['outcome'] == 'accepted'
    decision = assess(candidate, result)
    assert decision.available and decision.outcome.value == 'accepted', decision.reasons
    assert result['report']['corner_forces'] is not None and seconds < 30
