"""Bounded POSIX CAD subprocess ownership, ported from Text-2-CAD.

Lifecycle/resource controls, not an OS sandbox. Keep secrets out of env.
"""
import asyncio
import os
import pathlib
import secrets
import signal
import sys

_KILL_ROUNDS = 5      # bounded rescans for token-bearing processes after the first kill
_KILL_PAUSE = 0.01    # seconds between rescans; yields to the event loop


def _token_pids(token: str) -> list:
    """Linux only: live pids whose initial environment still carries this call's token."""
    needle = b"MIRUM_PROCESS_TOKEN=" + token.encode("ascii")
    try:
        entries = list(pathlib.Path("/proc").iterdir())
    except OSError:
        return []     # no /proc: only the owned process group can be reaped
    own = os.getpid()
    found = []
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name) == own:
            continue
        try:
            environment = (entry / "environ").read_bytes()
        except OSError:
            continue  # exited, zombie (empty environ), or not readable by us
        if needle in environment.split(b"\0"):
            found.append(int(entry.name))
    return found


class ProcessRunner:
    """Own a bounded set of POSIX sessions. This is lifecycle control, not isolation.

    Each call gets a fresh MIRUM_PROCESS_TOKEN in its child environment. After a
    timeout, an output overflow, or cancellation, the runner SIGKILLs its own
    session and then any Linux process still carrying that token. Only this
    call's descendants carry it, so other jobs are never signalled. Pipe drain
    after the kill is bounded by drain_timeout, and transports are closed if EOF
    cannot arrive. A descendant that scrubs its environment escapes the token scan.
    """

    def __init__(self, max_processes=3, timeout=30, output_limit=1048576, drain_timeout=1.0, memory_mb=1536):
        if type(memory_mb) is not int or memory_mb<=0:raise ValueError("memory_mb must be a positive integer")
        self.memory_mb=memory_mb
        self.timeout = timeout
        self.output_limit = output_limit
        self.drain_timeout = drain_timeout
        self._slots = asyncio.Semaphore(max_processes)
        self._tasks = set()
        self._closed = False

    @staticmethod
    def _kill_group(process):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _kill_token_process(pid):
        """SIGKILL a token-bearing process, and its group only when it leads one and is not ours."""
        try:
            group = os.getpgid(pid)
            if group == pid and group != os.getpgrp():
                os.killpg(group, signal.SIGKILL)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    @staticmethod
    def _close_transports(process):
        """Close the pipes and subprocess transport (asyncio's `_transport` handle on CPython)."""
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()

    @staticmethod
    async def _finish(task):
        """Wait until an owned cleanup or spawn settles, then replay any cancellation.

        asyncio.wait yields, so a task that is already done or cancelled returns or
        raises at once without spinning. Repeated cancellation of the caller is
        absorbed until the task settles, then re-raised.
        """
        cancelled = False
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                cancelled = True
        if cancelled or task.cancelled():
            raise asyncio.CancelledError
        return task.result()

    async def _drain(self, process, communication):
        """Bounded wait for pipe EOF. If EOF cannot arrive, close the transports and stop waiting."""
        done, _ = await asyncio.wait({communication}, timeout=self.drain_timeout)
        if not done:
            self._close_transports(process)
            done, _ = await asyncio.wait({communication}, timeout=self.drain_timeout)
        if not done:
            communication.cancel()
            await asyncio.wait({communication}, timeout=self.drain_timeout)

    async def _reap(self, process, token, communication=None):
        """Kill this call's processes, bound the pipe drain, then reap the direct child."""
        if process.returncode is None:
            # Unreaped, so its pid cannot be reused and killpg cannot hit another job.
            self._kill_group(process)
        for _ in range(_KILL_ROUNDS):
            pids = _token_pids(token)
            if not pids:
                break
            for pid in pids:
                self._kill_token_process(pid)
            await asyncio.sleep(_KILL_PAUSE)
        process.stdin.close()
        if communication is None:
            self._close_transports(process)
        else:
            await self._drain(process, communication)
        try:
            await asyncio.wait_for(process.wait(), self.drain_timeout)
        except asyncio.TimeoutError:
            pass

    async def run(self, command, *, input=b"", cwd=None, env=None):
        if self._closed:
            return dict(code="WORKER_CLOSED", message="Worker is closed", returncode=None,
                        stdout="", stderr="")
        if os.name != "posix":
            return dict(code="PROCESS_TREE_UNAVAILABLE", returncode=None, stdout="", stderr="",
                        message="POSIX process groups required; run the worker in Linux/Docker")
        task = asyncio.current_task()
        self._tasks.add(task)
        token = secrets.token_hex(16)
        child_env = dict(os.environ if env is None else env, MIRUM_PROCESS_TOKEN=token)
        try:
            async with self._slots:
                limited_command=[sys.executable,'-I',os.path.join(os.path.dirname(__file__),'_memory_entry.py'),
                                 str(self.memory_mb*1024*1024),*command]
                spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                    *limited_command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, start_new_session=True, cwd=cwd, env=child_env))
                try:
                    process = await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    async def abort_spawn():
                        try:
                            started = await spawn
                        except Exception:
                            return  # the spawn failed, so there is nothing to reap
                        await self._reap(started, token)
                    await self._finish(asyncio.create_task(abort_spawn()))
                    raise
                overflow = asyncio.Event()
                buffers = [bytearray(), bytearray()]
                retained = 0

                async def drain(stream, buffer):
                    nonlocal retained
                    while True:
                        block = await stream.read(16384)
                        if not block:
                            break
                        room = max(0, self.output_limit - retained)
                        buffer.extend(block[:room])
                        retained += min(room, len(block))
                        if len(block) > room:
                            overflow.set()

                async def feed():
                    try:
                        process.stdin.write(input)
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        process.stdin.close()

                async def exchange():
                    await asyncio.gather(feed(), drain(process.stdout, buffers[0]),
                                         drain(process.stderr, buffers[1]), process.wait())

                communication = asyncio.create_task(exchange())
                exceeded = asyncio.create_task(overflow.wait())
                code = None
                try:
                    done, _ = await asyncio.wait([communication, exceeded], timeout=self.timeout,
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if overflow.is_set():
                        code = "OUTPUT_LIMIT"
                    elif not done:
                        code = "TIMEOUT"
                finally:
                    exceeded.cancel()
                    async def cleanup():
                        await self._reap(process, token, communication)
                        await asyncio.gather(exceeded, return_exceptions=True)
                    await self._finish(asyncio.create_task(cleanup()))
                return dict(returncode=process.returncode, stdout=buffers[0].decode(errors="ignore"),
                            stderr=buffers[1].decode(errors="ignore"), code=code)
        finally:
            self._tasks.discard(task)

    async def close(self):
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

