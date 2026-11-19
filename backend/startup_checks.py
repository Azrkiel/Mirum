"""Release startup guards (RC1 R3). Stdlib only.

``enforce()`` is a no-op when ``MIRUM_DEPLOYMENT`` is unset, '' or 'development', and refuses
any value other than 'release' (code ``unknown_deployment``). In release mode it
evaluates every guard, logs ``MIRUM_STARTUP_REFUSED code=<c>: <msg>`` per failure and raises
``StartupRefused``. It adds coded, earlier failures on top of the checks that already live in
``engineering/store.py`` (worker count, uid, flock owner) and ``engineering/api_v1.py``
(mounted volume, scratch/data separation); those stay in place and the store's flock stays
authoritative (the probe here is only an early, coded hint). Messages never include the
access token or any other secret.
"""
from __future__ import annotations

import logging
import os
import posixpath
import re
import shlex
import sys
from dataclasses import dataclass

logger = logging.getLogger('mirum.startup')

_TOKEN_MIN_LEN = 32
_TOKEN_MIN_DISTINCT = 10
_VOLUME_PARENT = '/tmp/mirum'
_WORKER_ENV = ('WEB_CONCURRENCY', 'UVICORN_WORKERS', 'MIRUM_API_REPLICAS')


@dataclass(frozen=True)
class GuardFailure:
    code: str
    message: str


class StartupRefused(RuntimeError):
    def __init__(self, failures):
        self.failures = tuple(failures)
        super().__init__('startup refused: ' + '; '.join(f'{f.code}: {f.message}' for f in self.failures))


def _unescape(field: str) -> str:
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), field)


def _parse_mountinfo(text: str):
    """Return [(mountpoint, mount_options, fstype)] in file order."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if '-' not in parts[6:]:
            continue
        sep = parts.index('-', 6)
        if len(parts) < sep + 2:
            continue
        mounts.append((_unescape(parts[4]), parts[5].split(','), parts[sep + 1]))
    return mounts


def _under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip('/') + '/')


def _worker_count_bad(argv) -> bool:
    args = list(argv or [])
    for i, arg in enumerate(args):
        value = None
        if arg in ('--workers', '-w'):
            value = args[i + 1] if i + 1 < len(args) else ''
        elif arg.startswith('--workers='):
            value = arg.split('=', 1)[1]
        elif arg.startswith('-w') and not arg.startswith('--'):
            value = arg[2:].lstrip('=')
        if value is not None:
            try:
                if int(value) != 1:
                    return True
            except ValueError:
                return True
    return False


def evaluate(env, *, mountinfo, stat, euid, egid, argv, try_lock):
    """Pure evaluation; every side effect is injected. Returns a list of GuardFailure."""
    failures = []

    def fail(code, message):
        failures.append(GuardFailure(code, message))

    data = posixpath.normpath(env.get('MIRUM_ENGINEERING_DATA_DIR', _VOLUME_PARENT + '/engineering'))
    scratch = posixpath.normpath(env.get('MIRUM_WORK_DIR', _VOLUME_PARENT + '/work'))
    mounts = _parse_mountinfo(mountinfo)
    by_point = {}
    for point, opts, fstype in mounts:
        by_point[posixpath.normpath(point)] = (opts, fstype)  # later mounts shadow earlier ones

    # engineering volume
    volume_ok = _under(data, _VOLUME_PARENT) and data != _VOLUME_PARENT and data in by_point
    if not volume_ok:
        fail('engineering_volume_missing',
             f'engineering data dir {data} must be an exact mountpoint under {_VOLUME_PARENT}')
    else:
        try:
            st = stat(data)
        except OSError as exc:
            fail('engineering_volume_owner', f'cannot stat engineering volume {data}: {exc.__class__.__name__}')
        else:
            if st.st_uid != euid or st.st_gid != egid:
                fail('engineering_volume_owner',
                     f'engineering volume {data} is owned by {st.st_uid}:{st.st_gid}, '
                     f'process runs as {euid}:{egid}')
            elif st.st_mode & 0o022:
                fail('engineering_volume_owner',
                     f'engineering volume {data} is group/world writable (mode {st.st_mode & 0o7777:o})')
        if not try_lock(data):
            fail('engineering_volume_in_use', f'engineering volume {data} already has an active owner')

    # workers
    bad_env = [name for name in _WORKER_ENV if env.get(name, '1') != '1']
    try:
        gunicorn_args = shlex.split(env.get('GUNICORN_CMD_ARGS', ''))
    except ValueError:
        gunicorn_args = ['--workers', 'unparseable']
    if bad_env or _worker_count_bad(argv) or _worker_count_bad(gunicorn_args):
        fail('multiple_workers',
             'engineering supports a single API process/replica'
             + (f' (set: {", ".join(bad_env)})' if bad_env else ''))

    # scratch
    best = None
    for point in by_point:
        if _under(scratch, point) and (best is None or len(point) > len(best)):
            best = point
    if best is None or by_point[best][1] != 'tmpfs':
        fail('scratch_not_tmpfs', f'scratch dir {scratch} must be on tmpfs')
    if _under(scratch, data) or _under(data, scratch):
        fail('scratch_overlap', f'scratch dir {scratch} and engineering data dir {data} must not overlap')

    # access token (value never echoed)
    token = env.get('MIRUM_ACCESS_TOKEN', '')
    if len(token) < _TOKEN_MIN_LEN:
        fail('access_token_missing',
             f'MIRUM_ACCESS_TOKEN must be set and at least {_TOKEN_MIN_LEN} characters')
    elif len(set(token)) < _TOKEN_MIN_DISTINCT or any(c.isspace() for c in token):
        fail('access_token_weak',
             f'MIRUM_ACCESS_TOKEN must have at least {_TOKEN_MIN_DISTINCT} distinct characters '
             'and no whitespace')

    # read-only root
    root = by_point.get('/')
    if root is None or 'ro' not in root[0]:
        fail('root_writable', 'root filesystem must be mounted read-only')

    if euid == 0:
        fail('running_as_root', 'process must not run as root')
    return failures


def _read_mountinfo() -> str:
    try:
        with open('/proc/self/mountinfo', encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return ''


def _default_try_lock(path: str) -> bool:
    """True when the volume's owner.lock can be taken (and is released again at once)."""
    try:
        import fcntl
    except ImportError:
        return True
    try:
        handle = open(os.path.join(path, 'owner.lock'), 'a+b')
    except OSError:
        return True  # ownership/permission problems are reported by the owner check
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True
    finally:
        handle.close()


def enforce(env=os.environ) -> None:
    deployment = env.get('MIRUM_DEPLOYMENT')
    if deployment in (None, '', 'development'):
        return
    if deployment != 'release':
        failure = GuardFailure('unknown_deployment',
                               "MIRUM_DEPLOYMENT must be unset, '' or 'development' (no guards) "
                               "or 'release' (guards enforced)")
        logger.error('MIRUM_STARTUP_REFUSED code=%s: %s', failure.code, failure.message)
        raise StartupRefused([failure])
    resolved = dict(env)
    for name, default in (('MIRUM_ENGINEERING_DATA_DIR', _VOLUME_PARENT + '/engineering'),
                          ('MIRUM_WORK_DIR', _VOLUME_PARENT + '/work')):
        resolved[name] = os.path.realpath(env.get(name, default))
    geteuid = getattr(os, 'geteuid', lambda: -1)
    getegid = getattr(os, 'getegid', lambda: -1)
    failures = evaluate(resolved, mountinfo=_read_mountinfo(), stat=os.stat, euid=geteuid(),
                        egid=getegid(), argv=sys.argv, try_lock=_default_try_lock)
    if failures:
        for failure in failures:
            logger.error('MIRUM_STARTUP_REFUSED code=%s: %s', failure.code, failure.message)
        raise StartupRefused(failures)
