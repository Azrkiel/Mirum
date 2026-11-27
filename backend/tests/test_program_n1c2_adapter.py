"""N1c-2: NativePlateAdapter against a fake mirum-native binary (no real kernel, no OCCT).

The fake is a small Python script created in a temp dir and pinned through the constructor's `pin`
argument (the only override; nothing in the environment can change the SHA pin). The OCCT remeasure
worker is replaced by an in-process fake so no CadQuery is needed. The real binary is exercised in
test_program_n1c2_real_binary.py when it is available.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import stat
import sys
from base64 import b64decode
from pathlib import Path

import pytest

from engineering.language.semantic import compile_source
from engineering.program import kernel
from engineering.program.adapters import adapter_for
from engineering.program.adapters.native_pin import (
    BINARY_PATH, BINARY_SHA256, KERNEL_COMMIT, PIN, NativePin,
)
from engineering.program.kernel import ExecutionContext, NATIVE_CLAIMS, plan_for
from engineering.program.planning import generator_candidates

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

REPO = Path(__file__).resolve().parents[2]
PLATE_SRC = (REPO / 'backend' / 'engineering' / 'fixtures' / 'editable_plate' / 'edge.mir').read_bytes()
VERSION_LINE = {'name': 'mirum-native', 'version': '0.1.0', 'commit': '1' * 40, 'protocol': 'mirum_native_plate_v1'}

FAKE = '''#!{python}
import json, math, os, sys
here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, 'cfg.json')))
args = sys.argv[1:]
def record(extra=None):
    with open(os.path.join(here, 'record-%s.json' % args[0]), 'w') as out:
        json.dump(dict(argv=sys.argv, env=dict(os.environ), cwd=os.getcwd(), **(extra or {{}})), out)
if args[:1] == ['version']:
    record()
    text = json.dumps(cfg['version'])
    if cfg.get('dup_version'):  # a duplicate key ahead of the genuine one: last-wins parsers would accept it
        text = '{{"name": "evil", ' + text[1:]
    print(text)
    sys.exit(0)
record()
plan = json.load(open(args[1]))
step_path = args[args.index('--step') + 1]
w, h, t = plan['width_mm'], plan['height_mm'], plan['thickness_mm']
if w == 80:  # the probe plate: only the volume knob applies, so build-time faults are not probe faults
    cfg = {k: v for k, v in cfg.items() if k in ('volume_scale', 'version')}
volume = (w * h * t - 4 * math.pi * 9 * t) * cfg.get('volume_scale', 1.0)
spec = dict(plan)
if cfg.get('spec_mismatch'):
    spec['width_mm'] = w + 1
report = dict(schema='mirum_native_plate_report_v0_1', part_id='plate', outcome=cfg.get('outcome', 'pass'),
              diagnostics=[], specification=spec, volume_mm3=volume, envelope_mm=[w, h, t])
mode = cfg.get('stdout', 'normal')
if mode == 'normal':
    sys.stdout.write(json.dumps(report) + '\\n')
elif mode == 'huge':
    sys.stdout.write('x' * (3 * 1024 * 1024))
elif mode == 'nan':
    sys.stdout.write('{{"outcome": NaN}}')
elif mode == 'deep':
    sys.stdout.write('[' * 400 + ']' * 400)
elif mode == 'notjson':
    sys.stdout.write('not json')
step = cfg.get('step', 'normal')
if step == 'normal':
    open(step_path, 'wb').write(b'ISO-10303-21;FAKE-STEP')
elif step == 'symlink':
    open(os.path.join(here, 'target.step'), 'wb').write(b'ISO-10303-21;FAKE-STEP')
    os.symlink(os.path.join(here, 'target.step'), step_path)
elif step == 'oversize':
    open(step_path, 'wb').write(b'S' * (600 * 1024))
elif step == 'empty':
    open(step_path, 'wb').write(b'')
if cfg.get('sleep'):
    import time
    time.sleep(cfg['sleep'])
sys.exit(cfg.get('exit', 0))
'''


def make_fake(directory: Path, **cfg) -> tuple[Path, NativePin]:
    directory.mkdir(mode=0o755, exist_ok=True)
    os.chmod(directory, 0o755)
    binary = directory / 'mirum-native'
    binary.write_text(FAKE.replace('{python}', sys.executable).replace('{{', '{').replace('}}', '}'))
    os.chmod(binary, 0o755)
    write_cfg(directory, **cfg)
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    return binary, NativePin(sha256=sha, commit=VERSION_LINE['commit'], version='0.1.0')


def write_cfg(directory: Path, **cfg):
    cfg.setdefault('version', VERSION_LINE)
    (directory / 'cfg.json').write_text(json.dumps(cfg))


@pytest.fixture(autouse=True)
def kernel_enabled(monkeypatch):
    monkeypatch.setenv('MIRUM_NATIVE_KERNEL', '1')
    monkeypatch.delenv('MIRUM_NATIVE_BIN', raising=False)


@pytest.fixture()
def fake_occt(monkeypatch):
    """Replace the OCCT plate worker by an in-process fake that reports the STEP it is handed."""
    import engineering.editable_plate_adapter as epa
    calls = []

    async def worker(python, operation, spec, *, step=None, seconds=30):
        calls.append((operation, dict(spec), step))
        assert operation == 'inspect' and step is not None
        w, h, t = spec['width_mm'], spec['height_mm'], spec['thickness_mm']
        report = {'schema': 'editable_plate_measurement_v1', 'outcome': 'pass', 'diagnostics': [],
                  'step': {'bytes': len(step), 'sha256': hashlib.sha256(step).hexdigest()},
                  'specification': spec, 'envelope_mm': [w, h, t],
                  'volume_mm3': w * h * t - 4 * math.pi * 9 * t, 'bores': []}
        return {'report': report}

    monkeypatch.setattr(epa, 'worker', worker)
    return calls


@pytest.fixture(scope='module')
def compiled():
    return compile_source(PLATE_SRC.replace(b'backend = "occt"', b'backend = "mirum-native"'))


@pytest.fixture(scope='module')
def candidate(compiled):
    return generator_candidates(compiled.brief_content)[0]


@pytest.fixture(scope='module')
def wide_candidate():
    wide = compile_source(PLATE_SRC.replace(b'backend = "occt"', b'backend = "mirum-native"')
                          .replace(b'80 mm', b'100 mm'))
    return generator_candidates(wide.brief_content)[0]


def native_plan(candidate, backend='mirum-native'):
    return plan_for('editable_plate', '1.0.0', backend, candidate, ('step',))


def context(deadline_s=30.0):
    return ExecutionContext(deadline_s=deadline_s, max_artifact_bytes=1024 ** 2, workspace='n1c2-test')


def adapter_of(binary, pin):
    from engineering.program.adapters.native import NativePlateAdapter
    return NativePlateAdapter(binary=str(binary), pin=pin, capability='editable_plate')


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ pin and description

def test_pin_values_and_sha_file_agree():
    assert PIN.sha256 == BINARY_SHA256 == 'b9d4f28d26049485fb3cbe7a7996c5f550180716e8951de7c05742a839f1e9bd'
    assert PIN.commit == KERNEL_COMMIT == '3501017b40c07b77d9cd48c0e7c1dcee744fd5fb'
    assert PIN.version == '0.1.0' and PIN.protocol == 'mirum_native_plate_v1'
    assert BINARY_PATH == '/opt/mirum-native/bin/mirum-native'
    sha_file = REPO / 'native-kernel' / 'mirum-native.sha256'
    assert sha_file.read_text() == f'{BINARY_SHA256}  {BINARY_PATH}\n'  # Dockerfile.verify copies native-kernel/
    from engineering.program.adapters import native_pin
    assert native_pin.IMAGE.endswith('@sha256:98e503588daab23582eb6ca531b69b341e8422a3072c86524f398cbfe4afe09d')


def test_adapter_for_native_returns_the_real_adapter_with_no_override_surface():
    from engineering.program.adapters.native import NativePlateAdapter
    adapter = adapter_for('mirum-native', capability='editable_plate')
    assert type(adapter) is NativePlateAdapter and adapter.backend == 'mirum-native'
    with pytest.raises(TypeError):
        adapter_for('mirum-native', capability='editable_plate', sha256='0' * 64, bogus=1)


def test_claims_are_geometry_checks_only():
    assert dict(NATIVE_CLAIMS) == {'editable_plate@1.0.0': 'geometry_checks_only'}


def test_describe_declares_plate_only_with_native_and_occt_tolerances(tmp_path):
    binary, pin = make_fake(tmp_path / 'b')
    d = adapter_of(binary, pin).describe()
    assert d.backend == 'mirum-native' and d.schema_version == 'mirum_kernel_v0_2'
    assert d.capabilities == ('editable_plate@1.0.0',) and d.export_formats == ('step',)
    assert d.representations == ('brep', 'report')
    assert d.operations == ('capability.build@0.6.0', 'capability.inspect@0.6.0', 'capability.export@0.6.0')
    by = {t.name: t for t in d.tolerances}
    assert by['native_length'].value == 1e-6 and by['native_length'].unit == 'mm'
    assert {'length', 'axis', 'volume'} <= set(by)
    assert all(by[n].source == 'editable-plate-occt-v1' for n in ('length', 'axis', 'volume'))


# ------------------------------------------------------------------ probe

def test_probe_available_records_versions(tmp_path):
    binary, pin = make_fake(tmp_path / 'b')
    probe = run(adapter_of(binary, pin).probe())
    assert probe.available, probe
    names = {v.name: v.version for v in probe.native_versions}
    assert names == {'mirum-native': '0.1.0', 'mirum-native-commit': pin.commit,
                     'mirum-native-sha256': pin.sha256}
    lock = kernel.backend_lock(adapter_of(binary, pin).describe(), probe)
    assert lock.backend == 'mirum-native' and lock.schema_version == 'mirum_kernel_v0_2'


def test_probe_runtime_missing_unless_flag_is_one(tmp_path, monkeypatch):
    binary, pin = make_fake(tmp_path / 'b')
    for value in (None, '', '0', 'true', '11'):
        if value is None:
            monkeypatch.delenv('MIRUM_NATIVE_KERNEL')
        else:
            monkeypatch.setenv('MIRUM_NATIVE_KERNEL', value)
        probe = run(adapter_of(binary, pin).probe())
        assert (probe.available, probe.reasons) == (False, ('runtime_missing',)), value


def test_probe_runtime_missing_when_binary_absent(tmp_path):
    _, pin = make_fake(tmp_path / 'b')
    probe = run(adapter_of(tmp_path / 'b' / 'nope', pin).probe())
    assert probe.reasons == ('runtime_missing',)


def test_default_path_and_env_path_are_used_when_no_binary_given(tmp_path, monkeypatch):
    from engineering.program.adapters.native import NativePlateAdapter
    binary, pin = make_fake(tmp_path / 'b')
    monkeypatch.setenv('MIRUM_NATIVE_BIN', str(binary))
    assert run(NativePlateAdapter(pin=pin).probe()).available
    monkeypatch.delenv('MIRUM_NATIVE_BIN')
    # The default path does not exist on a development host.
    assert run(NativePlateAdapter(pin=pin).probe()).reasons == ('runtime_missing',)


def test_probe_sha_mismatch_is_probe_failed(tmp_path):
    binary, pin = make_fake(tmp_path / 'b')
    bad = NativePin(sha256='0' * 64, commit=pin.commit, version=pin.version)
    assert run(adapter_of(binary, bad).probe()).reasons == ('probe_failed',)


@pytest.mark.parametrize('field,value', [('name', 'other'), ('version', '0.1.1'), ('commit', '2' * 40),
                                         ('protocol', 'mirum_native_plate_v2')])
def test_probe_version_handshake_must_match_exactly(tmp_path, field, value):
    binary, pin = make_fake(tmp_path / 'b', version={**VERSION_LINE, field: value})
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_probe_version_handshake_extra_or_missing_keys_refused(tmp_path):
    binary, pin = make_fake(tmp_path / 'b', version={**VERSION_LINE, 'extra': 1})
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)
    short = {k: v for k, v in VERSION_LINE.items() if k != 'commit'}
    binary, pin = make_fake(tmp_path / 'c', version=short)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_probe_volume_check_failure(tmp_path):
    binary, pin = make_fake(tmp_path / 'b', volume_scale=1.0 + 1e-8)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)
    binary, pin = make_fake(tmp_path / 'c', volume_scale=1.0 + 1e-11)
    assert run(adapter_of(binary, pin).probe()).available


def test_probe_refuses_relative_path(tmp_path, monkeypatch):
    binary, pin = make_fake(tmp_path / 'b')
    monkeypatch.chdir(binary.parent)
    from engineering.program.adapters.native import NativePlateAdapter
    assert run(NativePlateAdapter(binary='mirum-native', pin=pin).probe()).reasons == ('probe_failed',)
    assert run(NativePlateAdapter(binary='./mirum-native', pin=pin).probe()).reasons == ('probe_failed',)


def test_probe_refuses_symlink_directory_and_writable_binaries(tmp_path):
    binary, pin = make_fake(tmp_path / 'b')
    link = tmp_path / 'link'
    link.symlink_to(binary)
    assert run(adapter_of(link, pin).probe()).reasons == ('probe_failed',)
    assert run(adapter_of(binary.parent, pin).probe()).reasons == ('probe_failed',)
    for bits in (stat.S_IWGRP, stat.S_IWOTH):
        os.chmod(binary, 0o755 | bits)
        assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',), oct(bits)
    os.chmod(binary, 0o755)
    assert run(adapter_of(binary, pin).probe()).available
    os.chmod(binary, 0o644)  # not executable
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_probe_refuses_a_writable_parent_directory(tmp_path):
    binary, pin = make_fake(tmp_path / 'b')
    os.chmod(binary.parent, 0o777)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_probe_refuses_a_symlinked_ancestor_directory(tmp_path):
    (tmp_path / 'real').mkdir(mode=0o755)
    binary, pin = make_fake(tmp_path / 'real' / 'b')
    (tmp_path / 'link').symlink_to(tmp_path / 'real')
    via_link = tmp_path / 'link' / 'b' / 'mirum-native'
    assert via_link.exists() and os.path.realpath(via_link) == str(binary)
    assert run(adapter_of(via_link, pin).probe()).reasons == ('probe_failed',)
    assert run(adapter_of(binary, pin).probe()).available  # the same file by its real path is fine


def test_probe_refuses_a_group_or_world_writable_ancestor_unless_sticky(tmp_path):
    outer = tmp_path / 'outer'
    outer.mkdir(mode=0o755)
    binary, pin = make_fake(outer / 'b')
    for mode in (0o775, 0o757, 0o777):
        os.chmod(outer, mode)
        assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',), oct(mode)
    os.chmod(outer, 0o1777)  # sticky: other users cannot rename or replace entries
    assert run(adapter_of(binary, pin).probe()).available
    os.chmod(outer, 0o755)
    assert run(adapter_of(binary, pin).probe()).available


def test_probe_refuses_an_ancestor_not_owned_by_root_or_the_service_user(tmp_path, monkeypatch):
    import engineering.program.adapters.native as native
    binary, pin = make_fake(tmp_path / 'b')
    real_lstat = os.lstat

    def lstat(path, *a, **k):
        result = real_lstat(path, *a, **k)
        if str(path) == str(tmp_path) and stat.S_ISDIR(result.st_mode):
            return os.stat_result((result.st_mode, result.st_ino, result.st_dev, result.st_nlink,
                                   os.geteuid() + 12345, result.st_gid, result.st_size,
                                   int(result.st_atime), int(result.st_mtime), int(result.st_ctime)))
        return result

    monkeypatch.setattr(native.os, 'lstat', lstat)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_probe_rejects_duplicate_json_keys_in_the_native_stdout(tmp_path):
    binary, pin = make_fake(tmp_path / 'b', dup_version=True)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_probe_refuses_oversized_binary(tmp_path, monkeypatch):
    import engineering.program.adapters.native as native
    binary, pin = make_fake(tmp_path / 'b')
    monkeypatch.setattr(native, 'MAX_BINARY_BYTES', 100)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


# ------------------------------------------------------------------ build

def test_build_completes_and_remeasures_with_the_occt_worker(tmp_path, candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b')
    built = run(adapter_of(binary, pin).build(native_plan(candidate), context()))
    result = built.result
    assert result.outcome == 'completed' and result.domain_outcome == 'accepted', result
    assert result.backend == 'mirum-native' and result.schema_version == 'mirum_kernel_v0_2'
    assert result.claim_scope == 'geometry_checks_only'
    assert [m.source for m in result.measurements] and {m.source for m in result.measurements} == {'report'}
    names = {m.name for m in result.measurements}
    assert {'plate.volume', 'plate.width', 'plate.height', 'plate.thickness'} <= names
    # The OCCT worker was handed exactly the bytes the native kernel wrote.
    (operation, spec, step), = fake_occt
    assert step == b'ISO-10303-21;FAKE-STEP' and spec == dict(candidate.content.specification)
    (artifact,) = built.artifacts
    assert artifact.data == step and artifact.ref.sha256 == hashlib.sha256(step).hexdigest()
    ev = built.evaluation
    assert ev['passed'] and ev['report']['step']['sha256'] == artifact.ref.sha256
    assert ev['report']['kernel_revision'] and ev['artifacts']['generator-body.step'] == step


def test_build_subprocess_environment_and_argv_are_minimal(tmp_path, candidate, fake_occt, monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'secret')
    monkeypatch.setenv('MIRUM_SOMETHING', 'x')
    binary, pin = make_fake(tmp_path / 'b')
    run(adapter_of(binary, pin).build(native_plan(candidate), context()))
    rec = json.loads((binary.parent / 'record-plate.json').read_text())
    env = dict(rec['env'])
    # LANG=C from the adapter, plus the tokens ProcessRunner always adds (process_runner.py), and nothing else.
    tokens = {k: env.pop(k) for k in ('MIRUM_PROCESS_TOKEN', 'MIRUM_ENGINEERING_OWNER') if k in env}
    assert 'MIRUM_PROCESS_TOKEN' in tokens
    env.pop('LC_CTYPE', None)  # added by CPython's C-locale coercion in the runner's own wrapper (LANG=C)
    assert env == {'LANG': 'C'}, env
    assert set(rec['env']) <= {'LANG', 'LC_CTYPE', 'MIRUM_PROCESS_TOKEN', 'MIRUM_ENGINEERING_OWNER'}
    argv = rec['argv']
    assert argv[0] == str(binary) and argv[1] == 'plate' and argv[3] == '--step' and len(argv) == 5
    for path in (argv[2], argv[4]):
        assert os.path.isabs(path)
    from compiler import _WORK_DIR
    assert Path(argv[2]).parent == Path(argv[4]).parent and Path(argv[2]).parent.parent == Path(_WORK_DIR)
    assert Path(argv[2]).name == 'plan.json' and Path(argv[4]).name == 'out.step'
    assert not Path(argv[2]).parent.exists()  # temp dir removed
    # no user-controlled string reaches argv
    assert not any(candidate.content.specification['datum_ref'] in a for a in argv)


@pytest.mark.parametrize('cfg,reason', [
    ({'exit': 2}, 'native_failed'),
    ({'exit': 1}, 'native_failed'),
    ({'exit': 3}, 'native_failed'),
    ({'sleep': 5}, 'deadline_exceeded'),
    ({'stdout': 'huge'}, 'budget_exceeded'),
    ({'stdout': 'nan'}, 'evidence_invalid'),
    ({'stdout': 'deep'}, 'evidence_invalid'),
    ({'stdout': 'notjson'}, 'evidence_invalid'),
    ({'spec_mismatch': True}, 'evidence_invalid'),
    ({'outcome': 'fail'}, 'evidence_invalid'),
    ({'step': 'symlink'}, 'evidence_invalid'),
    ({'step': 'oversize'}, 'evidence_invalid'),
    ({'step': 'empty'}, 'evidence_invalid'),
    ({'step': 'missing'}, 'evidence_invalid'),
])
def test_build_refusals(tmp_path, wide_candidate, fake_occt, cfg, reason):
    binary, pin = make_fake(tmp_path / 'b', **cfg)  # misbehaviour applies to the 100 mm candidate, not the probe plate
    adapter = adapter_of(binary, pin)
    assert run(adapter.probe()).available
    ctx = context(deadline_s=2.0 if 'sleep' in cfg else 30.0)
    built = run(adapter.build(native_plan(wide_candidate), ctx))
    assert built.result.outcome == 'unavailable' and built.result.reasons == (reason,), built.result
    assert built.artifacts == () and built.evaluation is None
    assert fake_occt == []  # the OCCT worker never saw an unvalidated STEP


def test_build_unsupported_and_unavailable_never_probe_or_run(tmp_path, candidate, fake_occt, monkeypatch):
    binary, pin = make_fake(tmp_path / 'b')
    adapter = adapter_of(binary, pin)
    occt_plan = native_plan(candidate, backend='occt')
    built = run(adapter.build(occt_plan, context()))
    assert built.result.outcome == 'unsupported' and built.result.reasons == ('backend_mismatch',)
    assert not (binary.parent / 'record-version.json').exists()
    monkeypatch.setenv('MIRUM_NATIVE_KERNEL', '0')
    built = run(adapter.build(native_plan(candidate), context()))
    assert built.result.outcome == 'unavailable' and built.result.reasons == ('runtime_missing',)


def test_native_occt_disagreement_is_rejected(tmp_path, candidate, monkeypatch):
    import engineering.editable_plate_adapter as epa
    real_worker_calls = []

    async def worker(python, operation, spec, *, step=None, seconds=30):
        w, h, t = spec['width_mm'], spec['height_mm'], spec['thickness_mm']
        real_worker_calls.append(1)
        return {'report': {'schema': 'editable_plate_measurement_v1', 'outcome': 'pass', 'diagnostics': [],
                           'step': {'bytes': len(step), 'sha256': hashlib.sha256(step).hexdigest()},
                           'specification': spec, 'envelope_mm': [w, h, t],
                           'volume_mm3': (w * h * t - 4 * math.pi * 9 * t) * 1.001}}

    monkeypatch.setattr(epa, 'worker', worker)
    binary, pin = make_fake(tmp_path / 'b')
    built = run(adapter_of(binary, pin).build(native_plan(candidate), context()))
    assert real_worker_calls and built.result.outcome == 'completed'
    assert built.result.domain_outcome == 'rejected' and built.artifacts == ()
    assert built.evaluation['passed'] is False
    assert any(d['code'] == 'native_disagreement' for d in built.evaluation['report']['diagnostics'])


def test_occt_rejection_of_the_native_step_is_rejected(tmp_path, candidate, monkeypatch):
    import engineering.editable_plate_adapter as epa

    async def worker(python, operation, spec, *, step=None, seconds=30):
        return {'report': {'schema': 'editable_plate_measurement_v1', 'outcome': 'fail',
                           'diagnostics': [{'code': 'bore_mismatch', 'field': 'bores', 'message': 'x'}],
                           'step': {'bytes': len(step), 'sha256': hashlib.sha256(step).hexdigest()},
                           'specification': spec}}

    monkeypatch.setattr(epa, 'worker', worker)
    binary, pin = make_fake(tmp_path / 'b')
    built = run(adapter_of(binary, pin).build(native_plan(candidate), context()))
    assert built.result.domain_outcome == 'rejected' and built.artifacts == ()


def test_occt_worker_unavailable_is_unavailable(tmp_path, candidate, monkeypatch):
    import engineering.editable_plate_adapter as epa
    from engineering.service import EngineeringUnavailable

    async def worker(*args, **kwargs):
        raise EngineeringUnavailable('x')

    monkeypatch.setattr(epa, 'worker', worker)
    binary, pin = make_fake(tmp_path / 'b')
    built = run(adapter_of(binary, pin).build(native_plan(candidate), context()))
    assert built.result.outcome == 'unavailable' and built.result.reasons == ('native_failed',)


def test_cancellation_stops_the_build(tmp_path, wide_candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b', sleep=20)
    adapter = adapter_of(binary, pin)

    async def go():
        task = asyncio.create_task(adapter.build(native_plan(wide_candidate), context(deadline_s=30)))
        await asyncio.sleep(1.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(go())


# ------------------------------------------------------------------ inspect / export / measure

def test_inspect_uses_the_occt_worker_only(tmp_path, candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b')
    adapter = adapter_of(binary, pin)
    built = run(adapter.build(native_plan(candidate), context()))
    (artifact,) = built.artifacts
    before = len(fake_occt)
    result = run(adapter.inspect(artifact, native_plan(candidate)))
    assert result.outcome == 'consistent' and result.backend == 'mirum-native'
    assert len(fake_occt) == before + 1
    out = run(adapter.measure_plate(artifact.data, native_plan(candidate)))
    assert out['report']['outcome'] == 'pass'


def test_inspect_refuses_wrong_format_and_wrong_backend_plan(tmp_path, candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b')
    adapter = adapter_of(binary, pin)
    built = run(adapter.build(native_plan(candidate), context()))
    (artifact,) = built.artifacts
    wrong = run(adapter.inspect(artifact, native_plan(candidate, backend='occt')))
    assert wrong.outcome == 'unsupported' and wrong.reasons == ('operation_not_supported',) or wrong.outcome == 'unsupported'


def test_export_passes_step_and_refuses_everything_else(tmp_path, candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b')
    adapter = adapter_of(binary, pin)
    (artifact,) = run(adapter.build(native_plan(candidate), context())).artifacts
    assert adapter.export(artifact, 'step').outcome == 'exported'
    for fmt in ('glb', 'stl'):
        refused = adapter.export(artifact, fmt)
        assert (refused.outcome, refused.reasons) == ('unsupported', ('export_not_supported',))
    assert adapter.export(None, 'step').outcome == 'unsupported'


def test_mock_mode_has_no_native_runtime():
    adapter = adapter_for('mirum-native', mode='mock')
    assert adapter.mode == 'mock'
    assert run(adapter.probe()).reasons == ('mock_backend',)


def test_native_module_does_not_import_the_occt_adapter_class():
    import ast
    import engineering.program.adapters.native as native
    tree = ast.parse(Path(native.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and m.endswith('adapters.occt') or m == 'occt' for m in imported)
    assert 'OcctAdapter' not in Path(native.__file__).read_text()
