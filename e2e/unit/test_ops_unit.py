"""Offline command and payload checks for release storage operations."""
import ast
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import ops as module
from harness.ops import Ops
from harness.stack import Stack, StackConfig, StackError


def result(out: str = "", err: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.fixture
def rig(tmp_path: Path) -> tuple:
    config = StackConfig.from_env(tmp_path, {"MIRUM_E2E_TOKEN": "x" * 48})
    stack = Stack(config)
    runner = Mock()
    ticks = iter((2.0, 5.25))
    ops = Ops(stack, runner, lambda: next(ticks), Mock(side_effect=AssertionError))
    yield ops, stack, runner
    for call in runner.call_args_list:
        (argv,), options = call
        assert isinstance(argv, list) and all(isinstance(arg, str) for arg in argv)
        assert options["capture_output"] is True and options["text"] is True
        assert 0 < options["timeout"] <= 290 and not options.get("shell")
        assert "prune" not in argv and ["volume", "rm"] not in [
            argv[i:i + 2] for i in range(len(argv))
        ]
        assert not ("down" in argv and any(v in argv for v in ("--volumes", "-v")))


def calls(runner: Mock) -> list:
    return [call.args[0] for call in runner.call_args_list]


def test_lifecycle(rig: tuple) -> None:
    ops, stack, runner = rig
    ps = stack.compose_argv("ps", "-q", "backend")
    up = stack.compose_argv("up", "-d", "--wait", "--wait-timeout", "240", "--no-deps", "backend")
    runner.side_effect = map(result, ["old\n", "", "", "", "old", "", "", "new", "new", "OK\n", "new", ""])
    ops.kill("backend")
    assert ops.stop("backend", 60) == 3.25
    ops.start("backend", 45)
    assert ops.replace("backend") == ("old", "new")
    assert ops.exec_py("backend", "print('OK')") == "OK\n"
    ops.signal("backend", 17, "STOP")
    assert calls(runner) == [
        ps, ["docker", "kill", "-s", "KILL", "old"],
        stack.compose_argv("stop", "-t", "60", "backend"),
        stack.compose_argv("up", "-d", "--wait", "--wait-timeout", "45", "--no-deps", "backend"),
        ps, stack.compose_argv("rm", "-s", "-f", "backend"), up, ps,
        ps, ["docker", "exec", "-u", "10001:10001", "new", "python", "-c", "print('OK')"],
        ps, ["docker", "exec", "-u", "10001:10001", "new", "python", "-c",
         "import os, signal; os.kill(17, signal.SIGSTOP)"],
    ]
    assert runner.call_args_list[0].kwargs["env"]["MIRUM_ACCESS_TOKEN"] == stack.config.token


def test_exited_container(rig: tuple) -> None:
    ops, stack, runner = rig
    runner.side_effect = map(result, ["", "old\n", "137\n"])
    assert ops.exit_code("backend") == 137
    assert calls(runner) == [
        stack.compose_argv("ps", "-q", "backend"), stack.compose_argv("ps", "--all", "-q", "backend"),
        ["docker", "inspect", "-f", "{{.State.ExitCode}}", "old"],
    ]


def test_process_inventory_uses_only_selected_environment_fields(
    rig: tuple, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    ops, stack, runner = rig
    for pid, env in [("41", b"MIRUM_PROCESS_TOKEN=private\0MIRUM_ENGINEERING_OWNER=owner\0OTHER=hidden\0"),
                     ("42", b"MIRUM_PROCESS_TOKEN_EXTRA=hidden\0")]:
        proc = tmp_path / pid
        proc.mkdir()
        (proc / "stat").write_bytes(pid.encode() + b" (test task)) S 7 8")
        (proc / "cmdline").write_bytes(b"python\0worker.py\0")
        (proc / "environ").write_bytes(env)

    def run(argv: list, **kw: object) -> subprocess.CompletedProcess:
        if argv == stack.compose_argv("ps", "-q", "backend"):
            return result("cid")
        assert argv[:-1] == ["docker", "exec", "-u", "10001:10001", "cid", "python", "-c"]
        exec(argv[-1].replace('"/proc"', repr(tmp_path.as_posix())), {})
        raw = capsys.readouterr().out
        assert "hidden" not in raw and "private" not in raw
        return result(raw)

    runner.side_effect = run
    rows = sorted(ops.processes("backend"), key=lambda row: row["pid"])
    assert rows == [
        {"pid": 41, "ppid": 7, "argv": ["python", "worker.py"], "has_token": True, "owner": "owner"},
        {"pid": 42, "ppid": 7, "argv": ["python", "worker.py"], "has_token": False, "owner": None},
    ]


def test_host_pid_selects_uvicorn_across_threads(rig: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ops, _, runner = rig
    monkeypatch.setattr(module, "_PROC", tmp_path)
    for tid, children in (("40", "55 56"), ("41", "61")):
        task = tmp_path / "40" / "task" / tid
        task.mkdir(parents=True)
        (task / "children").write_text(children)
    for pid, command in (("55", b"python\0-c\0print('uvicorn')\0"), ("61", b"python\0-m\0uvicorn\0main:app\0")):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "cmdline").write_bytes(command)
    runner.side_effect = map(result, ["cid", "40"])
    assert ops.api_host_pid("backend") == 61
    assert calls(runner)[-1] == ["docker", "inspect", "-f", "{{.State.Pid}}", "cid"]


def config_output(name: str = "test_volume") -> str:
    return json.dumps({"volumes": {"mirum_engineering": {"name": name}}})


def sidecar_argv(write: bool, code: str) -> list[str]:
    mount = "test_volume:/tmp/mirum/engineering" + ("" if write else ":ro")
    return ["docker", "run", "--rm", "--network", "none", "--read-only", "--user", "10001:10001",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "1g", "--pids-limit", "256",
            "--tmpfs", "/tmp:rw,nosuid,noexec,size=256m", "-v",
            mount, "--entrypoint", "python", "mirum-backend:release", "-c", code]


@pytest.mark.parametrize("write", [False, True])
def test_sidecar(rig: tuple, write: bool) -> None:
    ops, stack, runner = rig
    runner.side_effect = map(result, [config_output(), "42"])
    assert (ops.sidecar("print(42)", True) if write else ops.sidecar("print(42)")) == "42"
    assert calls(runner) == [stack.compose_argv("config", "--format", "json"), sidecar_argv(write, "print(42)")]


def test_default_volume_and_mountpoint(rig: tuple) -> None:
    ops, stack, runner = rig
    runner.side_effect = map(result, ['{"volumes":{"mirum_engineering":{}}}', "/srv/volume\n"])
    assert ops.mountpoint() == "/srv/volume"
    assert calls(runner) == [stack.compose_argv("config", "--format", "json"), [
        "docker", "volume", "inspect", "-f", "{{.Mountpoint}}", stack.config.project + "_mirum_engineering",
    ]]


def test_sql_and_bundle_payloads(rig: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Execute the exact Python/JSON pair sent to the sidecar against a temporary volume."""
    ops, _, runner = rig
    db = tmp_path / "engineering.sqlite3"
    with sqlite3.connect(db) as conn:
        for table, key in (("jobs", "id"), ("publications", "job")):
            conn.execute(f"CREATE TABLE {table} ({key} TEXT PRIMARY KEY, expires REAL)")
            conn.executemany(f"INSERT INTO {table} VALUES (?, 99)", [("first",), ("second",)])

    def run(argv: list, **kw: object) -> subprocess.CompletedProcess:
        if argv[-3:] == ["config", "--format", "json"]:
            return result(config_output())
        code, payload = argv[-2:]
        write = code in (module._SET_EXPIRY, module._TAMPER, module._SPARSE, module._REMOVE)
        assert argv[:-1] == sidecar_argv(write, code)
        assert all(value not in code for value in ("second", "model.step", "x'\n?", "ballast"))
        monkeypatch.setattr(sys, "argv", ["-c", payload])
        try:
            exec(code.replace('"/tmp/mirum/engineering"', repr(tmp_path.as_posix())), {})
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            return result(err=str(exc), rc=1)
        return result(capsys.readouterr().out)

    runner.side_effect = run
    sql = "SELECT ? AS sample, expires FROM jobs WHERE id=?"
    assert ops.db_rows(sql, ("x'\n?", "second")) == [["x'\n?", 99]]
    argv = calls(runner)[-1]
    assert argv == sidecar_argv(False, argv[-2]) + [argv[-1]]
    assert json.loads(argv[-1]) == [sql, ["x'\n?", "second"]]
    assert sql not in argv[-2] and "PRAGMA query_only=1" in argv[-2]
    with pytest.raises(StackError):
        ops.db_rows("DELETE FROM jobs")
    ops.set_expiry("second", 7.5)
    assert "BEGIN IMMEDIATE" in calls(runner)[-1][-2] and "busy_timeout=5000" in calls(runner)[-1][-2]
    for table, key in (("jobs", "id"), ("publications", "job")):
        assert ops.db_rows(f"SELECT * FROM {table} ORDER BY {key}") == [["first", 99], ["second", 7.5]]
    assert ops.notes == [{"kind": "A", "what": "expiry accelerated", "job_id": "second"}]
    with conn:
        conn.execute("DELETE FROM publications WHERE job='first'")
    with pytest.raises(StackError):
        ops.set_expiry("first", -1)
    assert ops.db_rows("SELECT expires FROM jobs WHERE id=?", ("first",)) == [[99]]
    assert len(ops.notes) == 1
    conn.close()

    folder = tmp_path / "bundles" / "second"
    (folder / "sub").mkdir(parents=True)
    target = folder / "model.step"
    target.write_bytes(b"abc")
    (folder / "sub" / "empty").touch()
    assert ops.volume_digests("second") == {
        "model.step": hashlib.sha256(b"abc").hexdigest(), "sub/empty": hashlib.sha256(b"").hexdigest(),
    }
    ops.tamper("second", "model.step", "flip_byte")
    assert target.stat().st_size == 3 and target.read_bytes() != b"abc"
    ops.tamper("second", "model.step", "truncate")
    assert target.stat().st_size < 3
    assert [(note["kind"], note["job_id"], note["mode"]) for note in ops.notes[1:]] == [
        ("F", "second", "flip_byte"), ("F", "second", "truncate"),
    ]
    ops.write_sparse("ballast", 131072)
    assert (tmp_path / "ballast").stat().st_size == 131072
    ops.remove("ballast")
    assert not (tmp_path / "ballast").exists()


@pytest.mark.parametrize("path", ["..", "/etc/passwd", ".hidden", "../target", "one/two", r"C:\root"])
def test_root_names_confined(rig: tuple, path: str) -> None:
    ops, _, runner = rig
    for method, args in [(ops.write_sparse, (path, 3)), (ops.remove, (path,)),
                         (ops.tamper, (path, "file", "truncate")), (ops.tamper, ("job", path, "flip_byte")),
                         (ops.volume_digests, (path,))]:
        with pytest.raises((ValueError, StackError)):
            method(*args)
    runner.assert_not_called()


@pytest.mark.parametrize("ci", [None, "false", "TRUE"])
def test_host_ci_gate(rig: tuple, ci: str, monkeypatch: pytest.MonkeyPatch) -> None:
    ops, _, runner = rig
    if ci is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", ci)
    for method, args in [(ops.host_chattr, ("bundles", "+i")), (ops.host_remount, ("ro",)),
                         (ops.host_fallocate, ("fill", 100)), (ops.host_poll_then_stop, ("bundles/job", 25, 10))]:
        with pytest.raises(StackError, match="CI"):
            method(*args)
    runner.assert_not_called()


def test_host_validation(rig: tuple, monkeypatch: pytest.MonkeyPatch) -> None:
    ops, _, runner = rig
    monkeypatch.setenv("CI", "true")
    for method, args in [
        (ops.host_chattr, ("../bundles", "+i")), (ops.host_chattr, ("bundles", "-R")),
        (ops.host_remount, ("bind",)), (ops.host_fallocate, ("/fill", 100)),
        (ops.host_fallocate, ("fill", -1)), (ops.host_fallocate, ("fill", True)),
        (ops.host_poll_then_stop, ("bundles/../job", 2, 1)), (ops.host_poll_then_stop, ("job", 0, 1)),
        (ops.host_poll_then_stop, ("job", 2, "30")), (ops.tamper, ("job", "file", "delete")),
    ]:
        with pytest.raises((ValueError, StackError)):
            method(*args)
    runner.assert_not_called()


@pytest.mark.parametrize(("method", "args", "command", "output", "expected"), [
    ("host_chattr", ("bundles", "+i"), ["python3", "-I", "-c", module._CHATTR, "/mnt/vol", "bundles", "+i"], "", None),
    ("host_chattr", ("bundles", "-i"), ["python3", "-I", "-c", module._CHATTR, "/mnt/vol", "bundles", "-i"], "", None),
    ("host_fallocate", ("fill", 2048), ["python3", "-I", "-c", module._FALLOCATE, "/mnt/vol", "fill", "2048"], "", None),
    ("host_remount", ("ro",), ["mount", "-o", "remount,ro", "/mnt/vol"], "", None),
    ("host_remount", ("rw",), ["mount", "-o", "remount,rw", "/mnt/vol"], "", None),
    ("host_poll_then_stop", ("bundles/job", 52, 10),
     ["python3", "-I", "-c", module._POLL, "/mnt/vol", "bundles/job", "52", "10"], "true\n", True),
    ("host_poll_then_stop", ("bundles/job", 52, 0),
     ["python3", "-I", "-c", module._POLL, "/mnt/vol", "bundles/job", "52", "0"], "false\n", False),
])
def test_host_commands(
    rig: tuple, monkeypatch: pytest.MonkeyPatch, method: str, args: tuple,
    command: list[str], output: str, expected: bool | None,
) -> None:
    ops, stack, runner = rig
    monkeypatch.setenv("CI", "true")
    runner.side_effect = map(result, [config_output(), "/mnt/vol", output])
    assert getattr(ops, method)(*args) is expected
    assert calls(runner) == [
        stack.compose_argv("config", "--format", "json"),
        ["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", "test_volume"],
        ["sudo", "-n", *command],
    ]
    if "-c" in command:
        compile(command[command.index("-c") + 1], "<host helper>", "exec")


def test_no_destructive_commands_or_unguarded_sudo() -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        strings = [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert "prune" not in strings and not ("volume" in strings and "rm" in strings)
        assert not ("down" in strings and ("-v" in strings or "--volumes" in strings))
        if "sudo" in strings:
            assert fn.name.startswith("host_") and "-n" in strings


@pytest.mark.parametrize("failure", ["exit", "timeout", "oserror"])
def test_failure_redaction(rig: tuple, failure: str) -> None:
    ops, stack, runner = rig
    token = stack.config.token
    if failure == "timeout":
        runner.side_effect = subprocess.TimeoutExpired(["docker"], 60, output=token.encode(), stderr=b"timeout")
    elif failure == "exit":
        runner.return_value = result(token, "failed " + token, 1)
    else:
        runner.side_effect = OSError("failed " + token)
    with pytest.raises(StackError) as exc:
        ops.container_id("backend")
    assert token not in str(exc.value) and "***" in str(exc.value)
