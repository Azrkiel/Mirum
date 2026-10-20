"""Real process containment regressions ported from Text-2-CAD."""

import asyncio, json, os, signal, subprocess, sys, textwrap
from pathlib import Path
import pytest
from backend.process_runner import ProcessRunner

ROOT = Path(__file__).resolve().parents[2]

PROCESS_TREE = pytest.mark.skipif(
    os.name != "posix" or not Path("/proc/self").is_dir(),
    reason="needs POSIX process groups and Linux /proc",
)

DRIVER = """
import asyncio, json, os, pathlib, sys, time
from backend.process_runner import ProcessRunner

def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True

async def main(job, bystander, bystander_pid_file):
    pid_file = pathlib.Path(bystander_pid_file)
    background = ProcessRunner(max_processes=1, timeout=60)
    unrelated = asyncio.create_task(background.run([sys.executable, bystander]))
    try:
        for _ in range(500):
            if pid_file.exists():
                break
            await asyncio.sleep(0.01)
        runner = ProcessRunner(max_processes=2, timeout=1.0, output_limit=4096, drain_timeout=0.2)
        started = time.monotonic()
        try:
            result = await runner.run([sys.executable, job])
        finally:
            await runner.close()
        elapsed = time.monotonic() - started
        print(json.dumps({"code": result["code"], "elapsed": elapsed,
                          "bystander_alive": alive(int(pid_file.read_text()))}))
    finally:
        await background.close()
        await asyncio.gather(unrelated, return_exceptions=True)

asyncio.run(main(*sys.argv[1:4]))
"""

CANCELLED_CLEANUP = """
import asyncio
from backend.process_runner import ProcessRunner

async def main():
    async def cleanup():
        await asyncio.sleep(60)
    task = asyncio.create_task(cleanup())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait({task})
    try:
        await ProcessRunner._finish(task)
    except asyncio.CancelledError:
        print("settled")

asyncio.run(main())
"""

def run_python(code, *args, timeout=45):
    """Run code in a child interpreter; a stall is killed and reported as a failure."""
    process = subprocess.Popen(
        [sys.executable, "-c", code, *map(str, args)], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise AssertionError("child interpreter stalled; a cleanup path did not settle") from None
    return process.returncode, out, err

def _alive(pid):
    try:
        # A zombie has exited; it is not executing.
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return False

def _kill_recorded(pid_file):
    try:
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        pass

def _write_bystander(tmp_path):
    script, pid_file = tmp_path / "bystander.py", tmp_path / "bystander.pid"
    script.write_text(textwrap.dedent(f"""
        import os, pathlib, time
        pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))
        time.sleep(60)
    """))
    return script, pid_file

def test_finish_returns_when_owned_cleanup_is_already_cancelled():
    completed = run_python(CANCELLED_CLEANUP, timeout=20)
    returncode, out, err = completed
    assert returncode == 0, err
    assert out.strip() == "settled"

@PROCESS_TREE
def test_timeout_reaps_setsid_descendant_that_holds_the_pipes(tmp_path):
    escapee_pid = tmp_path / "escapee.pid"
    job = tmp_path / "job.py"
    job.write_text(textwrap.dedent(f"""
        import pathlib, subprocess, sys, time
        escaped = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        pathlib.Path({str(escapee_pid)!r}).write_text(str(escaped.pid))
        time.sleep(60)
    """))
    bystander, bystander_pid = _write_bystander(tmp_path)
    try:
        returncode, out, err = run_python(DRIVER, job, bystander, bystander_pid)
        assert returncode == 0, err
        report = json.loads(out)
        assert report["code"] == "TIMEOUT"
        assert report["elapsed"] < 10
        assert report["bystander_alive"] is True
        assert not _alive(int(escapee_pid.read_text()))
    finally:
        _kill_recorded(escapee_pid)
        _kill_recorded(bystander_pid)

@PROCESS_TREE
def test_escapee_without_the_token_is_abandoned_after_the_bounded_drain(tmp_path):
    escapee_pid = tmp_path / "escapee.pid"
    job = tmp_path / "job.py"
    job.write_text(textwrap.dedent(f"""
        import pathlib, subprocess, sys, time
        escaped = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True, env={{}})
        pathlib.Path({str(escapee_pid)!r}).write_text(str(escaped.pid))
        time.sleep(60)
    """))
    bystander, bystander_pid = _write_bystander(tmp_path)
    try:
        returncode, out, err = run_python(DRIVER, job, bystander, bystander_pid)
        assert returncode == 0, err
        report = json.loads(out)
        assert report["code"] == "TIMEOUT"
        assert report["elapsed"] < 10
        # Documented limit: a scrubbed descendant carries no token and is not signalled.
        assert _alive(int(escapee_pid.read_text()))
    finally:
        _kill_recorded(escapee_pid)
        _kill_recorded(bystander_pid)

@PROCESS_TREE
def test_each_call_gets_its_own_process_token_and_keeps_workspace_env():
    source = (
        "import os\n"
        "print(os.environ['MIRUM_PROCESS_TOKEN'])\n"
        "print(os.environ['MIRUM_RUN_ID'], os.environ['MIRUM_WORKSPACE_TOKEN'])\n"
    )
    env = {**os.environ, "MIRUM_RUN_ID": "run-7", "MIRUM_WORKSPACE_TOKEN": "ws-7"}

    async def main():
        runner = ProcessRunner(max_processes=1, timeout=10)
        try:
            first = await runner.run([sys.executable, "-c", source], env=env)
            second = await runner.run([sys.executable, "-c", source], env=env)
        finally:
            await runner.close()
        return first, second

    first, second = asyncio.run(main())
    assert first["code"] is None and second["code"] is None
    lines_a, lines_b = first["stdout"].splitlines(), second["stdout"].splitlines()
    assert lines_a[1:] == ["run-7 ws-7"] == lines_b[1:]
    assert len(lines_a[0]) >= 32 and lines_a[0] != lines_b[0]
