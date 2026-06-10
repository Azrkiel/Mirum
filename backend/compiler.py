"""
Secure CadQuery Script Execution Engine.

Executes AI-generated CadQuery Python scripts in an isolated subprocess
with strict timeout enforcement. This is the runtime behind the QA Inspector
(Critic Loop) — failed executions return tracebacks that get fed back to the
Machinist subagent for correction.
"""

import ast
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
_ALLOWED_MODULES = {"cadquery", "cq", "math", "cad_utils", "cad_aero"}
_BANNED_BUILTINS = {
    "eval", "exec", "open", "compile", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "breakpoint", "input",
    "memoryview", "type",
}
# Dunder access is the classic sandbox-escape highway:
#   ().__class__.__bases__[0].__subclasses__()        → reach arbitrary types,
#   obj.__getattribute__('__subclasses__')()          → same, smuggled via a
#                                                        string the old attr
#                                                        denylist never saw.
# Denylisting a handful of dunders is futile — __getattribute__ walks around
# it. So we ban ALL dunder *attribute* access and all dunder *name* loads,
# keeping only a tiny allowlist of harmless module dunders that legitimate
# scripts occasionally read (e.g. `if __name__ == "__main__":`).
_ALLOWED_DUNDER_NAMES = {"__name__", "__file__", "__doc__"}
# Importlib gap fix: block bare use of dangerous stdlib module names as
# ast.Name nodes (e.g. `importlib.import_module(...)` without a prior
# import statement, exploiting a pre-loaded reference in __builtins__).
# The import-statement path is already caught by the allowlist above, but
# belt-and-suspenders coverage prevents any residual name-binding tricks.
_BANNED_BARE_NAMES = {
    "importlib", "ctypes", "subprocess", "socket", "shutil",
    "pickle", "pathlib", "tempfile", "pty", "fcntl", "mmap",
    "multiprocessing", "threading",
}
_MEM_LIMIT_BYTES = int(1.5 * 1024 * 1024 * 1024)  # 1.5 GB

# Runtime backstop (defence in depth behind the AST gate). Prepended to every
# script before it is piped to the subprocess. Installs a PEP-578 audit hook
# that hard-blocks process-spawn / shell-exec primitives — a geometry script
# has no legitimate reason to spawn a process, and these are the events an
# attacker would reach for AFTER any future AST bypass. Kept to a SINGLE line
# so it does not shift user-script traceback line numbers (the Machinist reads
# those tracebacks to self-correct). It imports `sys` directly; that is fine —
# the AST allowlist is only applied to the user portion, never to this trusted
# preamble.
_SANDBOX_RUNTIME_PREAMBLE = (
    "import sys as _mirum_sys; _mirum_sys.addaudithook("
    "lambda e, a: (_ for _ in ()).throw(PermissionError("
    "'mirum sandbox: blocked operation: ' + e)) if e in ("
    "'os.system', 'os.exec', 'os.spawn', 'os.posix_spawn', 'os.fork', "
    "'os.forkpty', 'pty.spawn', 'subprocess.Popen') else None)\n"
)


def _is_dunder(name: str) -> bool:
    """True for double-underscore names like __class__, __getattribute__."""
    return len(name) >= 5 and name.startswith("__") and name.endswith("__")


def _check_ast_security(script_string: str) -> str | None:
    """Return an error message if the script imports or calls banned targets.

    Uses an ALLOWLIST for imports: only modules in _ALLOWED_MODULES may be
    imported.  All other modules (os, sys, subprocess, importlib, ctypes,
    pickle, socket, shutil, pathlib, etc.) are rejected automatically.

    Additionally, bare use of any name in _BANNED_BARE_NAMES is rejected
    even without an import statement (importlib gap fix).
    """
    try:
        tree = ast.parse(script_string)
    except SyntaxError as exc:
        return f"SyntaxError in script: {exc}"

    for node in ast.walk(tree):
        # --- Import allowlist ---
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in _ALLOWED_MODULES:
                    return (
                        f"Security violation: import of '{alias.name}' is not "
                        f"allowed. Permitted modules: {sorted(_ALLOWED_MODULES)}."
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top not in _ALLOWED_MODULES:
                    return (
                        f"Security violation: import from '{node.module}' is "
                        f"not allowed. Permitted modules: {sorted(_ALLOWED_MODULES)}."
                    )

        # --- Banned bare names (importlib gap fix) ---
        # Check ast.Name nodes that are NOT part of an import statement;
        # the import-statement branch above handles those separately.
        if isinstance(node, ast.Name) and node.id in _BANNED_BARE_NAMES:
            return (
                f"Security violation: use of '{node.id}' is not allowed "
                f"(forbidden stdlib module name)."
            )

        # --- Banned builtins, by reference not just by call ---
        # A bare `ast.Name` load of a banned builtin is rejected wherever it
        # appears, not only at a call site. This closes the indirect-call
        # escape `fn = exec; fn(code)` (and any other aliasing), where the
        # call site only ever sees the harmless-looking alias.
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in _BANNED_BUILTINS
        ):
            return (
                f"Security violation: reference to builtin '{node.id}' is "
                f"banned (forbidden builtins may not be called or aliased)."
            )

        # --- Dunder name loads (e.g. __builtins__, __loader__) ---
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and _is_dunder(node.id)
            and node.id not in _ALLOWED_DUNDER_NAMES
        ):
            return (
                f"Security violation: access to dunder name '{node.id}' is "
                f"banned."
            )

        # --- Dunder attribute access (ALL of them) ---
        # Banning every `obj.__x__` closes the attribute-chain escape
        # `().__class__.__bases__[0].__subclasses__()` AND its string-smuggled
        # cousin `obj.__getattribute__('__subclasses__')`, where only the final
        # `.__subclasses__` link used to be on a narrow denylist.
        if isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            return (
                f"Security violation: access to dunder attribute "
                f"'{node.attr}' is banned."
            )

    return None


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
