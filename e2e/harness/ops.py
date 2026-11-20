"""Release-container and engineering-volume operations for the R6 tests."""
from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import TypeVar

from harness.stack import Stack, StackError


_PROC = Path("/proc")
_MAX_TIMEOUT = 289
_ROOT = "/tmp/mirum/engineering"
_Collection = TypeVar("_Collection", list, dict)

_PROCESSES = r"""
import json
from pathlib import Path

rows = []
for proc in Path("/proc").iterdir():
    if not proc.name.isdecimal():
        continue
    try:
        stat = (proc / "stat").read_bytes().rsplit(b")", 1)[1].split()
        argv = (proc / "cmdline").read_bytes().removesuffix(b"\0")
        env = (proc / "environ").read_bytes().split(b"\0")
    except (FileNotFoundError, ProcessLookupError):
        continue
    owner = next((item.split(b"=", 1)[1].decode(errors="replace")
                  for item in env if item.startswith(b"MIRUM_ENGINEERING_OWNER=")), None)
    rows.append(dict(pid=int(proc.name), ppid=int(stat[1]),
                     argv=[arg.decode(errors="replace") for arg in argv.split(b"\0")] if argv else [],
                     has_token=any(item.startswith(b"MIRUM_PROCESS_TOKEN=") for item in env),
                     owner=owner))
print(json.dumps(rows))
"""

_SIDECAR_INPUT = """
import json
import sys
from pathlib import Path

args = json.loads(sys.argv[1])
root = Path("/tmp/mirum/engineering")
"""

_DB_ROWS = _SIDECAR_INPUT + """
import sqlite3

db = sqlite3.connect((root / "engineering.sqlite3").as_uri() + "?mode=ro", uri=True)
db.execute("PRAGMA query_only=1")
sql, params = args
print(json.dumps(db.execute(sql, params).fetchall()))
"""

_SET_EXPIRY = _SIDECAR_INPUT + """
import sqlite3

job, expiry = args
db = sqlite3.connect((root / "engineering.sqlite3").as_uri() + "?mode=rw", uri=True)
db.execute("PRAGMA busy_timeout=5000")
with db:
    db.execute("BEGIN IMMEDIATE")
    for statement in ("UPDATE jobs SET expires=? WHERE id=?",
                      "UPDATE publications SET expires=? WHERE job=?"):
        if db.execute(statement, (expiry, job)).rowcount != 1:
            raise ValueError("Missing job or publication for expiry acceleration")
"""

_BUNDLE = _SIDECAR_INPUT + """
bundle = root / "bundles" / args[0]
if bundle.is_symlink() or bundle.parent.is_symlink():
    raise ValueError("Unexpected symlink in bundle path")
"""

_DIGESTS = _BUNDLE + """
import hashlib

digests = {}
for path in sorted(bundle.rglob("*")):
    if path.is_symlink():
        raise ValueError("Unexpected symlink in bundle")
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        digests[path.relative_to(bundle).as_posix()] = digest.hexdigest()
print(json.dumps(digests))
"""

_TAMPER = _BUNDLE + """
path = bundle / args[1]
if path.is_symlink() or not path.is_file():
    raise ValueError("Expected a regular bundle file")
with path.open("r+b") as stream:
    first = stream.read(1)
    if not first:
        raise ValueError("Cannot corrupt an empty artifact")
    if args[2] == "flip_byte":
        stream.seek(0)
        stream.write(bytes([first[0] ^ 1]))
    else:
        stream.truncate(stream.seek(0, 2) - 1)
"""

_SPARSE = _SIDECAR_INPUT + """
with (root / args[0]).open("xb") as stream:
    stream.truncate(args[1])
"""

_REMOVE = _SIDECAR_INPUT + """
(root / args[0]).unlink(missing_ok=True)
"""

# Privileged file operations walk relative to the mounted directory descriptor.
# O_NOFOLLOW on each component prevents a volume symlink redirecting a host write.
_HOST_PATH = """
import os
import sys

mount, relative = sys.argv[1:3]
dirs = os.O_DIRECTORY | os.O_NOFOLLOW
parts = relative.split("/")
def parent_fd():
    fd = os.open(mount, dirs)
    try:
        for component in parts[:-1]:
            child = os.open(component, dirs, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise
"""

_POLL = _HOST_PATH + """
import signal
import stat
import time

process = os.pidfd_open(int(sys.argv[3]))
deadline = time.monotonic() + int(sys.argv[4])
while True:
    try:
        directory = parent_fd()
        try:
            mode = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("Unexpected symlink at poll target")
            signal.pidfd_send_signal(process, signal.SIGSTOP)
            print("true")
            break
        finally:
            os.close(directory)
    except FileNotFoundError:
        pass
    if time.monotonic() >= deadline:
        print("false")
        break
    time.sleep(0.01)
"""

_FALLOCATE = _HOST_PATH + """
import stat

fd = os.open(parts[-1], os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
             0o600, dir_fd=parent_fd())
if not stat.S_ISREG(os.fstat(fd).st_mode):
    raise ValueError("Expected a regular file")
os.posix_fallocate(fd, 0, int(sys.argv[3]))
"""

_CHATTR = _HOST_PATH + """
import array
import fcntl
import stat

fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd())
mode = os.fstat(fd).st_mode
if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
    raise ValueError("Expected a regular file or directory")
# Linux FS_IOC_GETFLAGS / FS_IOC_SETFLAGS encode sizeof(long) in the ioctl number.
flags = array.array("L", [0])
size = flags.itemsize << 16
fcntl.ioctl(fd, 0x80006601 | size, flags, True)
immutable = 0x10
if sys.argv[3] == "+i":
    flags[0] |= immutable
else:
    flags[0] &= ~immutable
fcntl.ioctl(fd, 0x40006602 | size, flags, True)
"""


def _relative(value: str, *, leaf: bool = False) -> str:
    if not isinstance(value, str) or any(c in value for c in ("..", "\\", ":", "\x00")):
        raise ValueError("Invalid volume path")
    if (leaf and "/" in value) or any(not p or p.startswith(".") for p in value.split("/")):
        raise ValueError("Expected a relative volume path")
    return value


def _integer(value: int, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError("Integer outside supported range")
    return value


def _service(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
        raise ValueError("Invalid service name")
    return value


def _signal_name(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch("[A-Z][A-Z0-9]*", value):
        raise ValueError("Invalid signal name")
    # Linux container signals may be unavailable on the harness host (Windows).
    if value not in {"KILL", "STOP", "CONT", "TERM"} and "SIG" + value not in signal.Signals.__members__:
        raise ValueError("Unknown signal")
    return value


def _require_ci() -> None:
    if os.environ.get("CI") != "true":
        raise StackError("Host operations require CI=true")


def _json_output(raw: str, expected: type[_Collection]) -> _Collection:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise StackError("Invalid JSON in command output") from None
    if not isinstance(value, expected):
        raise StackError("Unexpected JSON type in command output")
    return value


class Ops:
    """Inspect and exercise one stack and its engineering volume."""

    def __init__(
        self, stack: Stack, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.stack = stack
        self._runner = runner
        self._clock = clock
        self._sleep = sleep
        self.notes: list[dict] = []

    def _run(self, argv: list[str], timeout: int = 60) -> str:
        env = os.environ.copy()
        if argv[:2] == ["docker", "compose"]:
            env["MIRUM_ACCESS_TOKEN"] = self.stack.config.token
        try:
            result = self._runner(argv, capture_output=True, text=True, timeout=min(timeout, _MAX_TIMEOUT),
                                  cwd=self.stack.config.repo_root, env=env)
        except subprocess.TimeoutExpired as error:
            outputs = [v.decode(errors="replace") if isinstance(v, bytes) else v
                       for v in (error.stdout, error.stderr) if v]
            raise StackError(self.stack.redact("\n".join(["Command timed out", *outputs]))) from None
        except Exception as error:
            raise StackError(self.stack.redact(str(error))) from None
        if result.returncode:
            message = f"Command exited {result.returncode}\n{result.stdout}\n{result.stderr}"
            raise StackError(self.stack.redact(message))
        return self.stack.redact(result.stdout)

    def container_id(self, svc: str) -> str:
        ids = self._run(self.stack.compose_argv("ps", "-q", _service(svc))).split()
        if not ids:
            ids = self._run(self.stack.compose_argv("ps", "--all", "-q", svc)).split()
        if len(ids) != 1:
            raise StackError("Expected exactly one service container")
        return ids[0]

    def exit_code(self, svc: str) -> int:
        raw = self._run(["docker", "inspect", "-f", "{{.State.ExitCode}}", self.container_id(svc)])
        try:
            return int(raw)
        except ValueError:
            raise StackError("Invalid container exit code") from None

    def kill(self, svc: str, sig: str = "KILL") -> None:
        self._run(["docker", "kill", "-s", _signal_name(sig), self.container_id(svc)])

    def stop(self, svc: str, timeout_s: int) -> float:
        _integer(timeout_s, 0, _MAX_TIMEOUT)
        start = self._clock()
        self._run(self.stack.compose_argv("stop", "-t", str(timeout_s), _service(svc)), timeout_s + 30)
        return self._clock() - start

    def start(self, svc: str, timeout_s: int = 240) -> None:
        _integer(timeout_s, 1, _MAX_TIMEOUT)
        self._run(self.stack.compose_argv("up", "-d", "--wait", "--wait-timeout", str(timeout_s),
                                          "--no-deps", _service(svc)), timeout_s + 30)

    def replace(self, svc: str) -> tuple[str, str]:
        old = self.container_id(svc)
        self._run(self.stack.compose_argv("rm", "-s", "-f", svc))
        self.start(svc)
        return old, self.container_id(svc)

    def exec_py(self, svc: str, code: str) -> str:
        return self._run(["docker", "exec", "-u", "10001:10001", self.container_id(svc), "python", "-c", code])

    def processes(self, svc: str) -> list[dict]:
        return _json_output(self.exec_py(svc, _PROCESSES), list)

    def signal(self, svc: str, pid: int, sig: str) -> None:
        _integer(pid, 1)
        name = _signal_name(sig)
        self._run(["docker", "exec", "-u", "10001:10001", self.container_id(svc), "kill", "-" + name, str(pid)])

    def api_host_pid(self, svc: str) -> int:
        init = self._run(["docker", "inspect", "-f", "{{.State.Pid}}", self.container_id(svc)]).strip()
        if not init.isdecimal() or int(init) <= 1:
            raise StackError("Missing container init PID")
        candidates = set()
        try:
            for children in (_PROC / init / "task").glob("*/children"):
                for child in children.read_text().split():
                    try:
                        argv = (_PROC / child / "cmdline").read_bytes().split(b"\0")
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    if argv[1:3] == [b"-m", b"uvicorn"] or argv[0].rsplit(b"/", 1)[-1] == b"uvicorn":
                        candidates.add(int(child))
        except OSError as error:
            raise StackError(self.stack.redact(str(error))) from None
        if len(candidates) != 1:
            raise StackError("Expected one uvicorn child of container init")
        return candidates.pop()

    def volume_name(self) -> str:
        config = _json_output(self._run(self.stack.compose_argv("config", "--format", "json")), dict)
        volume = config.get("volumes", {}).get("mirum_engineering") or {}
        name = volume.get("name") or self.stack.config.project + "_mirum_engineering"
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name):
            raise StackError("Invalid engineering volume name")
        return name

    def mountpoint(self) -> str:
        path = self._run(["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", self.volume_name()]).strip()
        parsed = PurePosixPath(path)
        if parsed.root != "/" or len(parsed.parts) < 2 or ".." in parsed.parts or parsed.as_posix() != path:
            raise StackError("Invalid volume mountpoint")
        return path

    def _sidecar(self, code: str, write: bool, *args: str) -> str:
        mount = f"{self.volume_name()}:{_ROOT}" + ("" if write else ":ro")
        return self._run(["docker", "run", "--rm", "--network", "none", "--read-only", "--user", "10001:10001",
                          "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--tmpfs", "/tmp",
                          "-v", mount, "--entrypoint", "python", "mirum-backend:release", "-c", code, *args],
                         _MAX_TIMEOUT)

    def sidecar(self, code: str, write: bool = False) -> str:
        return self._sidecar(code, write)

    def db_rows(self, sql: str, params: Sequence = ()) -> list:
        return _json_output(self._sidecar(_DB_ROWS, False, json.dumps([sql, params])), list)

    def set_expiry(self, job_id: str, when_epoch: float) -> None:
        _relative(job_id, leaf=True)
        if type(when_epoch) not in (int, float) or not math.isfinite(when_epoch):
            raise ValueError("Expected finite expiry timestamp")
        self._sidecar(_SET_EXPIRY, True, json.dumps([job_id, when_epoch]))
        self.notes.append({"kind": "A", "what": "expiry accelerated", "job_id": self.stack.redact(job_id)})

    def volume_digests(self, job_id: str) -> dict[str, str]:
        payload = json.dumps([_relative(job_id, leaf=True)])
        return _json_output(self._sidecar(_DIGESTS, False, payload), dict)

    def write_sparse(self, name: str, size: int) -> None:
        self._sidecar(_SPARSE, True, json.dumps([_relative(name, leaf=True), _integer(size, 0)]))

    def remove(self, name: str) -> None:
        self._sidecar(_REMOVE, True, json.dumps([_relative(name, leaf=True)]))

    def tamper(self, job_id: str, name: str, mode: str) -> None:
        if mode not in ("flip_byte", "truncate"):
            raise ValueError("Unknown tamper mode")
        self._sidecar(_TAMPER, True, json.dumps([_relative(job_id, leaf=True), _relative(name, leaf=True), mode]))
        self.notes.append({"kind": "F", "what": "artifact tampered", "job_id": self.stack.redact(job_id),
                           "name": self.stack.redact(name), "mode": mode})

    def host_poll_then_stop(self, rel_path: str, pid: int, timeout_s: int) -> bool:
        _require_ci()
        _relative(rel_path)
        _integer(pid, 2)
        _integer(timeout_s, 0, _MAX_TIMEOUT - 1)
        value = self._run(["sudo", "-n", "python3", "-I", "-c", _POLL, self.mountpoint(),
                           rel_path, str(pid), str(timeout_s)], timeout_s + 5).strip()
        if value not in ("true", "false"):
            raise StackError("Unexpected poll result")
        return value == "true"

    def host_chattr(self, rel: str, flag: str) -> None:
        _require_ci()
        _relative(rel)
        if flag not in ("+i", "-i"):
            raise ValueError("Expected +i or -i")
        self._run(["sudo", "-n", "python3", "-I", "-c", _CHATTR, self.mountpoint(), rel, flag])

    def host_fallocate(self, rel: str, bytes: int) -> None:
        _require_ci()
        _relative(rel)
        _integer(bytes, 1)
        self._run(["sudo", "-n", "python3", "-I", "-c", _FALLOCATE, self.mountpoint(), rel, str(bytes)])

    def host_remount(self, mode: str) -> None:
        _require_ci()
        if mode not in ("ro", "rw"):
            raise ValueError("Expected ro or rw")
        self._run(["sudo", "-n", "mount", "-o", "remount," + mode, self.mountpoint()])
