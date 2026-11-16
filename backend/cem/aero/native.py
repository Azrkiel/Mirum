"""Bounded native runs with retained input, output, failure and binary identities."""
import os
from pathlib import Path
import shutil
import signal
import subprocess

from cem.evidence import file_digest

MAX_FILE = 1024 * 1024
TIMEOUT_S = 20


class NativeFailure(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def resolve(name):
    value = shutil.which(os.environ.get(f'MIRUM_{name.upper()}_BIN', name))
    if not value:
        raise NativeFailure(f'{name} executable unavailable', dict(state='unavailable', solver=name))
    path = Path(value).resolve()
    return path, file_digest(path)


def artifact(path, root):
    return dict(file=path.relative_to(root).as_posix(), sha256=file_digest(path, MAX_FILE))


def _limits():
    # Native tools run in the already-isolated single-threaded CEM worker.
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE, MAX_FILE))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(name, script, folder, stem, outputs=()):
    """No shell, no provider credentials, no graphics and no success fallback."""
    path, binary = resolve(name)
    folder.mkdir(parents=True, exist_ok=True)
    inp, log = folder/f'{stem}.in', folder/f'{stem}.log'
    inp.write_text(script)
    for output in outputs:
        (folder/output).unlink(missing_ok=True)
    env = {k: os.environ[k] for k in ('PATH','LD_LIBRARY_PATH','LANG','LC_ALL') if k in os.environ}
    env.update(OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
    state, code = 'completed', None
    with log.open('w') as stream:
        try:
            proc = subprocess.run([str(path)], input=script, text=True, cwd=folder, env=env,
                stdout=stream, stderr=subprocess.STDOUT, timeout=TIMEOUT_S,
                preexec_fn=_limits if os.name == 'posix' else None)
            code = proc.returncode
            if code:
                state = 'runtime_sigfpe' if code == -signal.SIGFPE else 'runtime_failure'
        except subprocess.TimeoutExpired:
            state = 'timeout'
    evidence = dict(solver=name, executable_sha256=binary, state=state, returncode=code,
        artifacts=[artifact(p, folder) for p in (inp, log, *(folder/n for n in outputs)) if p.is_file()])
    if file_digest(path) != binary:
        raise NativeFailure('Native executable changed during invocation', evidence)
    if state != 'completed':
        raise NativeFailure(f'{name}: {state}', evidence)
    if any(not (folder/n).is_file() for n in outputs):
        evidence['state'] = 'missing_output'
        raise NativeFailure(f'{name}: missing output', evidence)
    return evidence
