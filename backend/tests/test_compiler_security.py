"""T-02 unit tests — AST security scanner in compiler.py.

Tests:
  - 15 malicious scripts that MUST be blocked by _check_ast_security()
  - 5 valid CadQuery scripts that MUST pass through

Also covers the importlib gap fix: bare use of `importlib` as a name
(without an import statement) is now caught.

Run from repo root:
    python backend/tests/test_compiler_security.py
or via pytest:
    pytest backend/tests/test_compiler_security.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess  # noqa: E402

from compiler import (  # noqa: E402
    _SANDBOX_RUNTIME_PREAMBLE,
    _check_ast_security,
)


def _blocked(script: str) -> bool:
    """Return True if the script is rejected by the AST scanner."""
    return _check_ast_security(script) is not None


def _allowed(script: str) -> bool:
    """Return True if the script passes the AST scanner."""
    return _check_ast_security(script) is None


# ---------------------------------------------------------------------------
# Malicious scripts — all must be BLOCKED
# ---------------------------------------------------------------------------

class TestMaliciousBlocked(unittest.TestCase):
    """15 attack patterns that must be caught by the scanner."""

    def test_block_import_os(self):
        self.assertTrue(_blocked("import os; os.system('rm -rf /')"))

    def test_block_import_subprocess(self):
        self.assertTrue(_blocked("import subprocess; subprocess.run(['ls'])"))

    def test_block_import_sys(self):
        self.assertTrue(_blocked("import sys; sys.exit(0)"))

    def test_block_import_importlib(self):
        self.assertTrue(_blocked("import importlib; importlib.import_module('os')"))

    def test_block_import_ctypes(self):
        self.assertTrue(_blocked("import ctypes; ctypes.cdll.LoadLibrary('libm.so')"))

    def test_block_import_socket(self):
        self.assertTrue(_blocked("import socket; socket.gethostname()"))

    def test_block_eval_call(self):
        self.assertTrue(_blocked("eval('__import__(\"os\").system(\"id\")')"))

    def test_block_exec_call(self):
        self.assertTrue(_blocked("exec('import os')"))

    def test_block_open_call(self):
        self.assertTrue(_blocked("f = open('/etc/passwd', 'r')"))

    def test_block_compile_call(self):
        self.assertTrue(_blocked("compile('import os', '<string>', 'exec')"))

    def test_block_dunder_globals(self):
        self.assertTrue(_blocked("x = (lambda: None).__globals__"))

    def test_block_dunder_builtins(self):
        self.assertTrue(_blocked("b = (lambda: None).__globals__['__builtins__']"))

    def test_block_dunder_subclasses(self):
        self.assertTrue(_blocked("object.__subclasses__()"))

    def test_block_dunder_code(self):
        self.assertTrue(_blocked("f = lambda: None; c = f.__code__"))

    def test_block_importlib_bare_use(self):
        """importlib used as a bare Name (not via import stmt) must be caught.

        This is the gap fix — the scanner now checks ast.Name nodes for
        forbidden stdlib names even when no import statement is present.
        Without this fix, a pre-loaded importlib reference in __builtins__
        could theoretically be exploited.
        """
        script = (
            "import cad_utils\n"
            "# No import statement for importlib — use it as a bare name\n"
            "result = importlib.import_module('os')\n"
        )
        self.assertTrue(_blocked(script))

    # ---------------------------------------------------------------------------
    # Bonus: getattr / setattr / delattr — blocked builtins
    # ---------------------------------------------------------------------------

    def test_block_getattr_call(self):
        self.assertTrue(_blocked("getattr(__builtins__, 'eval')('1+1')"))

    def test_block_setattr_call(self):
        self.assertTrue(_blocked("setattr(object, '__init__', lambda self: None)"))

    def test_block_globals_call(self):
        self.assertTrue(_blocked("g = globals(); g['evil'] = __import__('os')"))

    def test_block_import_pickle(self):
        self.assertTrue(_blocked("import pickle; pickle.loads(b'evil_bytes')"))

    def test_block_import_pathlib(self):
        self.assertTrue(_blocked("import pathlib; p = pathlib.Path('/etc/shadow')"))


# ---------------------------------------------------------------------------
# Valid scripts — all must PASS the scanner
# ---------------------------------------------------------------------------

class TestValidScriptsPass(unittest.TestCase):
    """5 legitimate CadQuery scripts that must not be blocked."""

    def test_valid_simple_box(self):
        script = (
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(10, 20, 5)\n"
        )
        self.assertTrue(_allowed(script))

    def test_valid_cad_utils_import(self):
        script = (
            "import cadquery as cq\n"
            "from cad_utils import make_hex_bolt\n"
            "result = make_hex_bolt('M6', 30)\n"
        )
        self.assertTrue(_allowed(script))

    def test_valid_math_import(self):
        script = (
            "import cadquery as cq\n"
            "import math\n"
            "r = math.sqrt(2) * 10\n"
            "result = cq.Workplane('XY').circle(r).extrude(5)\n"
        )
        self.assertTrue(_allowed(script))

    def test_valid_cq_alias(self):
        script = (
            "import cadquery as cq\n"
            "import cq  # explicit cq alias\n"
            "result = cq.Workplane('XY').cylinder(20, 5)\n"
        )
        self.assertTrue(_allowed(script))

    def test_valid_from_cad_utils(self):
        script = (
            "from cad_utils import make_naca_airfoil, make_involute_spur_gear\n"
            "import cadquery as cq\n"
            "import math\n"
            "gear = make_involute_spur_gear(20, 2.0, 20.0)\n"
            "result = gear\n"
        )
        self.assertTrue(_allowed(script))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_syntax_error_is_blocked(self):
        """Unparseable scripts must also be blocked (not crash)."""
        result = _check_ast_security("def foo(: pass")
        self.assertIsNotNone(result)
        self.assertIn("SyntaxError", result)

    def test_empty_script_passes(self):
        self.assertTrue(_allowed(""))

    def test_comment_only_passes(self):
        self.assertTrue(_allowed("# This is just a comment\n"))

    def test_error_message_mentions_violation(self):
        result = _check_ast_security("import os")
        self.assertIsNotNone(result)
        self.assertIn("os", result)

    def test_from_import_blocked(self):
        """from os import path  must be blocked."""
        self.assertTrue(_blocked("from os import path"))

    def test_from_import_allowed(self):
        """from cad_utils import make_hex_bolt  must pass."""
        self.assertTrue(_allowed("from cad_utils import make_hex_bolt"))


# ---------------------------------------------------------------------------
# Regression: confirmed sandbox escapes (review 2026-06-09)
# ---------------------------------------------------------------------------

class TestConfirmedEscapesBlocked(unittest.TestCase):
    """Bypasses that previously PASSED the gate and are now closed."""

    # --- Indirect builtin reference: alias then call ---
    def test_block_indirect_exec_alias(self):
        self.assertTrue(_blocked("fn = exec\nfn('import os')"))

    def test_block_indirect_eval_alias(self):
        self.assertTrue(_blocked("e = eval\ne('1+1')"))

    def test_block_indirect_open_alias(self):
        self.assertTrue(_blocked("o = open\no('/etc/passwd')"))

    def test_block_builtin_in_list(self):
        self.assertTrue(_blocked("[exec][0]('import os')"))

    # --- Dunder attribute chain (with and without __getattribute__) ---
    def test_block_getattribute_subclasses_smuggle(self):
        script = (
            "o = ().__class__.__bases__[0]\n"
            "subs = o.__getattribute__('__subclasses__')()\n"
        )
        self.assertTrue(_blocked(script))

    def test_block_class_bases_chain(self):
        self.assertTrue(_blocked("x = ().__class__.__bases__[0]"))

    def test_block_mro_access(self):
        self.assertTrue(_blocked("m = type(1).__mro__"))

    def test_block_getattribute_method(self):
        self.assertTrue(_blocked("g = (1).__getattribute__('__class__')"))

    # --- Dunder bare name ---
    def test_block_builtins_dunder_name(self):
        self.assertTrue(_blocked("e = __builtins__['eval']"))

    # --- Valid dunder usage still allowed ---
    def test_allow_name_main_guard(self):
        script = (
            "import cadquery as cq\n"
            "if __name__ == '__main__':\n"
            "    result = cq.Workplane('XY').box(1, 1, 1)\n"
        )
        self.assertTrue(_allowed(script))


class TestRuntimeAuditHook(unittest.TestCase):
    """Defence-in-depth: the prepended audit hook blocks process spawning
    even if a script reaches the interpreter (e.g. a future AST bypass)."""

    def _run(self, body: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-"],
            input=_SANDBOX_RUNTIME_PREAMBLE + body,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_os_system_is_blocked(self):
        proc = self._run("import os\nos.system('echo pwned')")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("blocked operation", proc.stderr)

    def test_subprocess_popen_is_blocked(self):
        proc = self._run(
            "import subprocess\nsubprocess.Popen(['echo', 'pwned'])"
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("blocked operation", proc.stderr)

    def test_benign_script_still_runs(self):
        proc = self._run("print(6 * 7)")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("42", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
