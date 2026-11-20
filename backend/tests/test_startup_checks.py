"""Release startup guards: backend/startup_checks.py (RC1 R3).

Unit tests use injected env, mountinfo text, fake stat and a fake lock probe, so they run on
any host. The Linux-only tests use the real /proc/self/mountinfo and flock. The TestClient
test needs fastapi and runs in the native CI gate.
"""
from __future__ import annotations

import logging
import os
import stat as stat_mod
import sys
from types import SimpleNamespace

import pytest

import startup_checks
from startup_checks import GuardFailure, StartupRefused, enforce, evaluate

TOKEN = 'k3Jx9QmZ7wLp2VnB8sTd4RfY6hGc1AeU'
DATA = '/tmp/mirum/engineering'
WORK = '/tmp/mirum/work'

GOOD_MOUNTINFO = '\n'.join([
    '20 1 0:18 / / ro,relatime - overlay overlay rw,lowerdir=/a,upperdir=/b',
    '21 20 0:19 / /tmp rw,nosuid - tmpfs tmpfs rw,size=65536k',
    '22 21 0:20 / /tmp/mirum rw,nosuid - tmpfs tmpfs rw,mode=700,uid=10001,gid=10001',
    '23 22 8:1 /vol /tmp/mirum/engineering rw,relatime - ext4 /dev/sda1 rw',
]) + '\n'


def good_env():
    return {'MIRUM_DEPLOYMENT': 'release', 'MIRUM_ACCESS_TOKEN': TOKEN,
            'MIRUM_ENGINEERING_DATA_DIR': DATA, 'MIRUM_WORK_DIR': WORK}


def fake_stat(uid=10001, gid=10001, mode=0o700):
    return lambda path: SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=stat_mod.S_IFDIR | mode)


def run(env=None, **over):
    kw = dict(mountinfo=GOOD_MOUNTINFO, stat=fake_stat(), euid=10001, egid=10001,
              argv=['uvicorn', 'main:app'], try_lock=lambda path: True)
    kw.update(over)
    return evaluate(good_env() if env is None else env, **kw)


def codes(failures):
    return [f.code for f in failures]


def test_all_good_has_no_failures():
    assert run() == []


def test_engineering_volume_missing_when_not_a_mountpoint():
    info = '\n'.join(GOOD_MOUNTINFO.splitlines()[:3]) + '\n'
    assert 'engineering_volume_missing' in codes(run(mountinfo=info))


def test_engineering_volume_missing_when_outside_tmp_mirum():
    env = good_env()
    env['MIRUM_ENGINEERING_DATA_DIR'] = '/data/engineering'
    info = GOOD_MOUNTINFO + '24 20 8:2 / /data/engineering rw - ext4 /dev/sdb rw\n'
    assert 'engineering_volume_missing' in codes(run(env, mountinfo=info))


@pytest.mark.parametrize('kw', [
    dict(stat=fake_stat(uid=0)),
    dict(stat=fake_stat(gid=0)),
    dict(stat=fake_stat(mode=0o770)),
    dict(stat=fake_stat(mode=0o707)),
])
def test_engineering_volume_owner(kw):
    assert 'engineering_volume_owner' in codes(run(**kw))


def test_engineering_volume_owner_when_stat_fails():
    def boom(path):
        raise OSError('nope')
    assert 'engineering_volume_owner' in codes(run(stat=boom))


def test_engineering_volume_in_use():
    seen = []

    def busy(path):
        seen.append(path)
        return False
    assert 'engineering_volume_in_use' in codes(run(try_lock=busy))
    assert seen == [DATA]


def test_lock_not_probed_without_a_volume():
    seen = []
    info = '\n'.join(GOOD_MOUNTINFO.splitlines()[:3]) + '\n'
    run(mountinfo=info, try_lock=lambda p: seen.append(p) or True)
    assert seen == []


@pytest.mark.parametrize('name', ['WEB_CONCURRENCY', 'UVICORN_WORKERS', 'MIRUM_API_REPLICAS'])
def test_multiple_workers_env(name):
    env = good_env()
    env[name] = '2'
    assert 'multiple_workers' in codes(run(env))


@pytest.mark.parametrize('argv', [
    ['uvicorn', 'main:app', '--workers', '2'],
    ['uvicorn', 'main:app', '--workers=3'],
    ['gunicorn', '-w', '4', 'main:app'],
    ['uvicorn', '--workers', 'many'],
    ['gunicorn', '-w2', 'main:app'],
    ['gunicorn', '-w=2', 'main:app'],
])
def test_multiple_workers_argv(argv):
    assert 'multiple_workers' in codes(run(argv=argv))


@pytest.mark.parametrize('args', ['-w 2', '-w2', '--workers=3', '--bind 0.0.0.0:8000 --workers 4'])
def test_multiple_workers_gunicorn_cmd_args(args):
    env = good_env()
    env['GUNICORN_CMD_ARGS'] = args
    assert 'multiple_workers' in codes(run(env))


def test_gunicorn_cmd_args_one_worker_is_fine():
    env = good_env()
    env['GUNICORN_CMD_ARGS'] = '--workers 1 --bind 0.0.0.0:8000'
    assert run(env) == []


def test_one_worker_argv_is_fine():
    assert run(argv=['uvicorn', 'main:app', '--workers', '1']) == []


def test_scratch_not_tmpfs():
    info = GOOD_MOUNTINFO.replace('- tmpfs tmpfs rw,mode=700', '- ext4 /dev/sdc rw,mode=700')
    assert 'scratch_not_tmpfs' in codes(run(mountinfo=info))


def test_scratch_overlap_equal_and_nested():
    for work in (DATA, DATA + '/work', '/tmp/mirum'):
        env = good_env()
        env['MIRUM_WORK_DIR'] = work
        assert 'scratch_overlap' in codes(run(env)), work


def test_scratch_overlap_ignores_prefix_siblings():
    env = good_env()
    env['MIRUM_WORK_DIR'] = '/tmp/mirum/engineering2'
    assert 'scratch_overlap' not in codes(run(env))


@pytest.mark.parametrize('token', [None, '', 'short', 'y' * 31])
def test_access_token_missing(token):
    env = good_env()
    env.pop('MIRUM_ACCESS_TOKEN')
    if token is not None:
        env['MIRUM_ACCESS_TOKEN'] = token
    failures = run(env)
    assert 'access_token_missing' in codes(failures)
    if token:
        assert all(token not in f.message for f in failures)


@pytest.mark.parametrize('token', ['a' * 40, 'ab' * 20, 'abcdefghi' * 5,
                                   'abcdefghij klmnopqrstuvwxyz0123456789',
                                   'abcdefghijklmnopqrstuvwxyz012345\n'])
def test_access_token_weak(token):
    env = good_env()
    env['MIRUM_ACCESS_TOKEN'] = token
    failures = run(env)
    assert 'access_token_weak' in codes(failures)
    assert 'access_token_missing' not in codes(failures)
    assert all(token not in f.message for f in failures)


def test_access_token_with_ten_distinct_chars_is_fine():
    env = good_env()
    env['MIRUM_ACCESS_TOKEN'] = 'abcdefghij' * 4
    assert run(env) == []


def open_env(optin='1', token=None):
    env = good_env()
    env.pop('MIRUM_ACCESS_TOKEN')
    if optin is not None:
        env['MIRUM_OPEN_ACCESS'] = optin
    if token is not None:
        env['MIRUM_ACCESS_TOKEN'] = token
    return env


@pytest.mark.parametrize('token', [None, ''])
def test_open_access_optin_without_token_passes(token):
    assert run(open_env('1', token)) == []


@pytest.mark.parametrize('token', ['short', 'a' * 40, 'ab' * 20])
def test_open_access_optin_with_weak_token_still_fails(token):
    failures = run(open_env('1', token))
    assert {'access_token_missing', 'access_token_weak'} & set(codes(failures))
    assert all(token not in f.message for f in failures)


def test_open_access_optin_with_strong_token_passes():
    assert run(open_env('1', TOKEN)) == []


@pytest.mark.parametrize('optin', ['0', 'true', 'yes', 'True', ' 1', '1 ', '', '11'])
def test_open_access_other_values_do_not_count(optin):
    assert 'access_token_missing' in codes(run(open_env(optin)))


def test_no_optin_and_no_token_still_fails():
    assert 'access_token_missing' in codes(run(open_env(None)))


def test_open_access_does_not_relax_other_guards():
    info = GOOD_MOUNTINFO.replace('/ / ro,relatime', '/ / rw,relatime')
    assert codes(run(open_env('1'), mountinfo=info)) == ['root_writable']
    assert 'running_as_root' in codes(run(open_env('1'), euid=0, egid=0, stat=fake_stat(uid=0, gid=0)))


def test_enforce_logs_open_access_line_once(monkeypatch, caplog):
    monkeypatch.setattr(startup_checks, '_read_mountinfo', lambda: GOOD_MOUNTINFO)
    monkeypatch.setattr(startup_checks, 'evaluate', lambda *a, **k: [])
    with caplog.at_level(logging.WARNING):
        enforce(open_env('1'))
    assert caplog.text.count('MIRUM_STARTUP_OPEN_ACCESS') == 1
    assert 'MIRUM_STARTUP_REFUSED' not in caplog.text


def test_enforce_no_open_access_line_without_optin(monkeypatch, caplog):
    monkeypatch.setattr(startup_checks, 'evaluate', lambda *a, **k: [])
    with caplog.at_level(logging.WARNING):
        enforce(good_env())
        enforce(open_env('1', TOKEN))
        enforce(open_env('true'))
    assert 'MIRUM_STARTUP_OPEN_ACCESS' not in caplog.text


def test_root_writable():
    info = GOOD_MOUNTINFO.replace('/ / ro,relatime', '/ / rw,relatime')
    assert 'root_writable' in codes(run(mountinfo=info))


def test_root_writable_when_root_mount_absent():
    info = '\n'.join(GOOD_MOUNTINFO.splitlines()[1:]) + '\n'
    assert 'root_writable' in codes(run(mountinfo=info))


def test_running_as_root():
    assert 'running_as_root' in codes(run(euid=0, egid=0, stat=fake_stat(uid=0, gid=0)))


def test_failures_are_guard_failures_with_no_secrets():
    env = good_env()
    env['MIRUM_ACCESS_TOKEN'] = 'secret-token'
    failures = run(env, euid=0)
    assert failures and all(isinstance(f, GuardFailure) for f in failures)
    assert all('secret-token' not in f.message for f in failures)


@pytest.mark.parametrize('deployment', [None, '', 'development'])
def test_enforce_is_noop_outside_release(deployment, monkeypatch):
    env = {}
    if deployment is not None:
        env['MIRUM_DEPLOYMENT'] = deployment

    def explode(*a, **k):
        raise AssertionError('evaluate must not run')
    monkeypatch.setattr(startup_checks, 'evaluate', explode)
    enforce(env)


@pytest.mark.parametrize('value', ['prod', 'Release', 'production', 'release ', 'staging'])
def test_enforce_refuses_unknown_deployment(value, monkeypatch, caplog):
    def explode(*a, **k):
        raise AssertionError('guards must not run for an unknown value')
    monkeypatch.setattr(startup_checks, 'evaluate', explode)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(StartupRefused) as err:
            enforce({'MIRUM_DEPLOYMENT': value})
    assert codes(err.value.failures) == ['unknown_deployment']
    message = err.value.failures[0].message
    assert 'release' in message and 'development' in message
    assert 'MIRUM_STARTUP_REFUSED code=unknown_deployment:' in caplog.text
    assert repr(value) not in caplog.text and repr(value) not in str(err.value)


def test_enforce_logs_codes_and_raises(monkeypatch, caplog):
    monkeypatch.setattr(startup_checks, '_read_mountinfo', lambda: GOOD_MOUNTINFO)
    env = good_env()
    env['MIRUM_ACCESS_TOKEN'] = 'secret-token'
    with caplog.at_level(logging.ERROR):
        with pytest.raises(StartupRefused) as err:
            enforce(env)
    assert 'access_token_missing' in codes(err.value.failures)
    assert 'MIRUM_STARTUP_REFUSED code=access_token_missing:' in caplog.text
    assert 'secret-token' not in caplog.text and 'secret-token' not in str(err.value)
    assert 'access_token_missing' in str(err.value)


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='needs /proc/self/mountinfo and flock')
def test_real_mountinfo_and_tmp_dirs(tmp_path):
    info = startup_checks._read_mountinfo()
    assert info.strip()
    data = tmp_path / 'engineering'
    data.mkdir(mode=0o700)
    data.chmod(0o700)
    env = {'MIRUM_DEPLOYMENT': 'release', 'MIRUM_ENGINEERING_DATA_DIR': str(data),
           'MIRUM_WORK_DIR': str(tmp_path / 'work'), 'MIRUM_ACCESS_TOKEN': TOKEN}
    found = codes(evaluate(env, mountinfo=info, stat=os.stat, euid=os.geteuid(), egid=os.getegid(),
                           argv=[], try_lock=startup_checks._default_try_lock))
    # a pytest tmp dir is not a mountpoint under /tmp/mirum
    assert 'engineering_volume_missing' in found
    assert 'engineering_volume_in_use' not in found
    assert 'multiple_workers' not in found and 'access_token_missing' not in found
    assert 'scratch_overlap' not in found


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='needs flock')
def test_default_lock_probe_detects_holder(tmp_path):
    import fcntl
    held = (tmp_path / 'owner.lock').open('a+b')
    try:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert startup_checks._default_try_lock(str(tmp_path)) is False
    finally:
        held.close()
    assert startup_checks._default_try_lock(str(tmp_path)) is True


def test_lifespan_refuses_release_with_bad_env_and_dev_is_unchanged(monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    import main

    monkeypatch.delenv('MIRUM_ACCESS_TOKEN', raising=False)
    monkeypatch.setenv('MIRUM_DEPLOYMENT', 'release')
    with pytest.raises(StartupRefused) as err:
        with TestClient(main.app):
            pass
    assert 'access_token_missing' in codes(err.value.failures)

    called = []
    monkeypatch.setattr(startup_checks, 'enforce', lambda env=os.environ: called.append(1))
    monkeypatch.delenv('MIRUM_DEPLOYMENT', raising=False)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'eng'))
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path / 'work'))
    with TestClient(main.app):
        pass
    assert called == [1]


def test_lifespan_real_noop_when_deployment_unset(monkeypatch, tmp_path):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    import main

    monkeypatch.delenv('MIRUM_DEPLOYMENT', raising=False)
    monkeypatch.setenv('MIRUM_ENGINEERING_DATA_DIR', str(tmp_path / 'eng'))
    monkeypatch.setenv('MIRUM_WORK_DIR', str(tmp_path / 'work'))
    with TestClient(main.app):
        pass


def test_lifespan_refuses_unknown_deployment(monkeypatch):
    pytest.importorskip('fastapi')
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setenv('MIRUM_DEPLOYMENT', 'prod')
    with pytest.raises(StartupRefused) as err:
        with TestClient(main.app):
            pass
    assert codes(err.value.failures) == ['unknown_deployment']
