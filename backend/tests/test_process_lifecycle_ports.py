"""Real subprocess lifecycle tests, independent of OCCT and model SDKs."""
import asyncio
import os
from pathlib import Path
import sys

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")]


def alive(pid):
    try:
        # Container PID 1 may retain orphan zombies; a zombie is not executing.
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False


async def wait_file(path):
    for _ in range(300):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Child did not signal readiness: {path}")


async def test_timeout_kills_group_reaps_direct_child_and_releases_slot(tmp_path):
    from backend.process_runner import ProcessRunner
    child_pid = tmp_path / "child"
    parent_pid = tmp_path / "parent"
    source = (
        'import os, pathlib, subprocess, sys, time\n'
        f'p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
        f'pathlib.Path({str(child_pid)!r}).write_text(str(p.pid))\n'
        f'pathlib.Path({str(parent_pid)!r}).write_text(str(os.getpid()))\n'
        'time.sleep(60)\n'
    )
    runner = ProcessRunner(max_processes=1, timeout=0.4, output_limit=1024)
    try:
        result = await runner.run([sys.executable, "-c", source])
        assert result["code"] == "TIMEOUT"
        assert not alive(int(child_pid.read_text()))
        pid = int(parent_pid.read_text())
        assert not alive(pid)
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)
        second = await runner.run([sys.executable, "-c", 'print("slot released")'])
        assert second["returncode"] == 0
        assert second["stdout"] == "slot released\n"
    finally:
        await runner.close()


async def test_output_limit_bounds_combined_streams_and_kills_writer(tmp_path):
    from backend.process_runner import ProcessRunner
    pid_file = tmp_path / "pid"
    source = (f'import os, pathlib\npathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n'
              'while True:\n os.write(1, b"x" * 4096)\n os.write(2, b"y" * 4096)\n')
    runner = ProcessRunner(timeout=2, output_limit=1000)
    try:
        report = await runner.run([sys.executable, "-c", source])
        assert report["code"] == "OUTPUT_LIMIT"
        assert len(report["stdout"].encode()) + len(report["stderr"].encode()) <= 1000
        assert not alive(int(pid_file.read_text()))
    finally:
        await runner.close()


async def test_cancellation_during_spawn_still_kills_and_reaps_child(monkeypatch, tmp_path):
    from backend.process_runner import ProcessRunner
    original = asyncio.create_subprocess_exec
    spawned, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed_spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        spawned.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    runner = ProcessRunner(timeout=10)
    task = asyncio.create_task(runner.run([sys.executable, "-c", "import time; time.sleep(60)"]))
    try:
        await asyncio.wait_for(spawned.wait(), 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not alive(processes[0].pid)
        with pytest.raises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)
    finally:
        for process in processes:
            if process.returncode is None:
                os.killpg(process.pid, 9)
                await process.communicate()
        await runner.close()


async def test_close_cancels_running_and_queued_jobs_then_refuses_new_work(tmp_path):
    from backend.process_runner import ProcessRunner
    runner = ProcessRunner(max_processes=1, timeout=10)
    pid_file, queued_file = tmp_path / "pid", tmp_path / "queued"
    source = (f'import os, pathlib, time\npathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n'
              'time.sleep(60)')
    first = asyncio.create_task(runner.run([sys.executable, "-c", source]))
    await wait_file(pid_file)
    second = asyncio.create_task(runner.run([
        sys.executable, "-c", f'import pathlib; pathlib.Path({str(queued_file)!r}).touch()']))
    await asyncio.sleep(0)
    await runner.close()
    assert first.cancelled() and second.cancelled()
    assert not alive(int(pid_file.read_text()))
    assert not queued_file.exists()
    result = await runner.run([sys.executable, "-c", 'print("must not run")'])
    assert result["code"] == "WORKER_CLOSED"
    await runner.close()


async def test_windows_without_tree_containment_fails_visibly(monkeypatch):
    import backend.process_runner as compiler
    from types import SimpleNamespace
    monkeypatch.setattr(compiler, "os", SimpleNamespace(name="nt"))
    runner = compiler.ProcessRunner()
    try:
        result = await runner.run([sys.executable, "-c", "pass"])
        assert result["code"] == "PROCESS_TREE_UNAVAILABLE"
    finally:
        await runner.close()


