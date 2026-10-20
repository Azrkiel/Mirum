"""
Secure CadQuery Script Execution Engine.

Executes AI-generated CadQuery Python scripts in an isolated subprocess
with strict timeout enforcement. This is the runtime behind the QA Inspector
(Critic Loop) — failed executions return tracebacks that get fed back to the
Machinist subagent for correction.
"""

import asyncio
import logging
import os
import pathlib
import signal
import subprocess
import sys

try:
    import resource

    _HAS_RESOURCE = True
except ImportError:          # Windows — no resource module
    _HAS_RESOURCE = False
    logging.getLogger("mirum.compiler").warning(
        "Running on Windows: subprocess memory limits are NOT enforced. "
        "Production deployments MUST use Linux/Docker."
    )

# Directory containing cad_utils.py and other backend modules.
# Added to PYTHONPATH so subprocess-executed scripts can import them.
_BACKEND_DIR = str(pathlib.Path(__file__).parent.resolve())

# Writable working directory for subprocess-executed scripts. The host
# `/app` bind mount is read-only (see docker-compose.yml), and CadQuery /
# OCCT will happily drop temp files like `.stepcode.log` or
# `SaveToFile.tmp` into cwd when saving, which would otherwise raise
# PermissionError before the script's own save call even runs. Routing
# cwd to the tmpfs sidesteps all of that.
_WORK_DIR = pathlib.Path(os.environ.get("MIRUM_WORK_DIR", "/tmp/mirum/work"))
try:
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Fall back to the parent's cwd if even /tmp is unwritable — better
    # to let the subprocess fail loudly than to crash module import.
    pass

# ---------------------------------------------------------------------------
# AST Security Scanner (Allowlist Architecture)
# ---------------------------------------------------------------------------
# Package and historical backend-on-PYTHONPATH entrypoints share one policy.
if __package__:
    from .script_policy import _ALLOWED_MODULES, _BANNED_BUILTINS, _check_ast_security
else:
    from script_policy import _ALLOWED_MODULES, _BANNED_BUILTINS, _check_ast_security

_MEM_LIMIT_BYTES = int(1.5 * 1024 * 1024 * 1024)

_SANDBOX_RUNTIME_PREAMBLE = (
    "import sys as _mirum_sys; _mirum_sys.addaudithook("
    "lambda e, a: (_ for _ in ()).throw(PermissionError("
    "'mirum sandbox: blocked operation: ' + e)) if e in ("
    "'os.system', 'os.exec', 'os.spawn', 'os.posix_spawn', 'os.fork', "
    "'os.forkpty', 'pty.spawn', 'subprocess.Popen') else None)\n"
)


def _preexec_sandbox() -> None:
    """preexec_fn — new process group + memory cap (POSIX only)."""
    os.setpgrp()
    resource.setrlimit(resource.RLIMIT_AS, (_MEM_LIMIT_BYTES, _MEM_LIMIT_BYTES))


async def execute_cad_script(script_string: str) -> dict:
    """Execute a CadQuery Python script in a sandboxed subprocess.

    The script is piped to `python -` via stdin rather than written to a
    temp file. This avoids an entire class of Windows permission errors:
    on-access AV scanners (Avast, Defender) briefly lock freshly-written
    .py files, causing `PermissionError: [Errno 13]` when either the
    parent closes the temp file or the child tries to open it. With
    stdin delivery there is no file for AV to touch.

    Args:
        script_string: Complete, self-contained Python/CadQuery source code.

    Returns:
        On success: {"status": "success", "output": <stdout>}
        On failure: {"status": "error", "traceback": <stderr>}
    """
    # --- AST security gate (always enforced) ---
    # Checked on the USER script only; the trusted runtime preamble is
    # prepended afterwards and is intentionally exempt from the allowlist.
    violation = _check_ast_security(script_string)
    if violation:
        return {"status": "error", "traceback": violation}

    # Runtime backstop: install the audit-hook guard ahead of user code.
    sandboxed_script = _SANDBOX_RUNTIME_PREAMBLE + script_string

    try:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": _BACKEND_DIR,
            "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", "/tmp")),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        # Windows-specific env vars the Python runtime and its deps need.
        # ezdxf (a CadQuery dep) calls pathlib.Path("~").expanduser() at import
        # time; on Windows that checks USERPROFILE / HOMEDRIVE+HOMEPATH (NOT
        # HOME), so without these the subprocess crashes before running user
        # code. Inherit conditionally — only if set in the parent env.
        for _key in (
            "SYSTEMROOT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
            "TEMP", "TMP", "APPDATA", "LOCALAPPDATA",
        ):
            _val = os.environ.get(_key)
            if _val:
                env[_key] = _val

        if _HAS_RESOURCE:
            # Own and drain the child on cancellation instead of abandoning a
            # background communicate() thread. Keep the audited stdin script.
            if __package__:
                from .process_runner import ProcessRunner
            else:
                from process_runner import ProcessRunner
            env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
            runner = ProcessRunner(timeout=30, output_limit=1048576, memory_mb=1536)
            try:
                outcome = await runner.run(
                    [sys.executable, "-"], input=sandboxed_script.encode("utf-8"),
                    cwd=str(_WORK_DIR) if _WORK_DIR.exists() else None, env=env,
                )
            finally:
                await runner.close()
            if outcome.get("code"):
                return {"status": "error", "traceback": outcome["code"]}
            if outcome["returncode"] != 0:
                return {"status": "error", "traceback": outcome["stderr"]}
            return {"status": "success", "output": outcome["stdout"]}

        # Native Windows is compatibility-only: no POSIX resource/process cap.
        popen_kwargs: dict = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            # Run inside a writable tmpfs location so any implicit
            # relative-path writes (intermediate STEP logs, OCCT temp
            # files, scripts that still use 'output.glb' etc.) land
            # somewhere the unprivileged caduser can actually write.
            cwd=str(_WORK_DIR) if _WORK_DIR.exists() else None,
        )
        if _HAS_RESOURCE:
            popen_kwargs["preexec_fn"] = _preexec_sandbox
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        def _run() -> tuple[int, str, str]:
            # `python -` reads the program from stdin. Tracebacks show the
            # pseudo-filename "<stdin>", which main.py's _safe_error already
            # collapses via its `File "..."` regex.
            proc = subprocess.Popen([sys.executable, "-"], **popen_kwargs)
            try:
                stdout, stderr = proc.communicate(
                    input=sandboxed_script, timeout=30
                )
            except subprocess.TimeoutExpired:
                # Kill the entire process group, not just the parent
                if _HAS_RESOURCE:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except OSError:
                        proc.kill()
                else:
                    proc.kill()
                proc.wait()
                raise
            return proc.returncode, stdout, stderr

        returncode, stdout, stderr = await asyncio.to_thread(_run)

        if returncode != 0:
            return {"status": "error", "traceback": stderr}

        return {"status": "success", "output": stdout}

    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "traceback": "TIMEOUT: Script exceeded 30-second execution limit.",
        }
    except Exception as e:
        return {
            "status": "error",
            "traceback": f"Execution error: {e}",
        }
