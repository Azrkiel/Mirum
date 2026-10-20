"""Generated-source AST policy ported from Text-2-CAD architecture hardening.

This is defense in depth, NOT a hostile-code isolation boundary. Mirum retains
its cad_aero helpers, subprocess audit hook, secret-free environment and limits.
"""
import ast
import re

_ALLOWED_MODULES = {"cadquery", "cq", "math", "cad_utils", "cad_aero"}

_BANNED_BUILTINS = {
    "eval", "exec", "open", "compile", "__import__", "getattr", "setattr",
    "delattr", "globals", "locals", "vars", "breakpoint", "input",
    "memoryview", "type",
}

_ALLOWED_DUNDERS = frozenset({"__name__", "__main__"})

_BLOCKED_MODULE_NAMES = frozenset({
    "sys", "os", "posix", "builtins", "importlib", "subprocess", "shutil", "socket",
    "pathlib", "io", "pickle", "marshal", "shelve", "ctypes", "cffi", "inspect", "gc",
    "runpy", "pkgutil", "multiprocessing", "threading", "asyncio", "concurrent",
    "tempfile", "codecs", "linecache", "tokenize", "glob", "operator", "webbrowser",
    "urllib", "http", "requests", "ssl", "pty", "fcntl", "termios", "sysconfig",
    "zipimport", "copyreg",
})

_CTYPES_NAMES = frozenset({
    "ctypeslib", "CDLL", "PyDLL", "WinDLL", "OleDLL", "cdll", "pydll", "windll",
    "oledll", "pythonapi", "CFUNCTYPE", "PYFUNCTYPE", "WINFUNCTYPE", "load_library",
    "string_at", "memmove", "memset", "addressof",
})

_REFLECTIVE_ATTRS = frozenset({
    "f_globals", "f_locals", "f_builtins", "f_back", "f_code", "f_trace",
    "gi_frame", "gi_code", "gi_yieldfrom", "cr_frame", "cr_code", "cr_await",
    "ag_frame", "ag_code", "tb_frame", "tb_next", "co_code", "func_globals",
})

_NUMPY_ROOTS = frozenset({"numpy", "np"})

_NUMPY_FILE_NAMES = frozenset({
    "load", "loadtxt", "genfromtxt", "save", "savez", "savez_compressed", "savetxt",
    "fromfile", "tofile", "memmap", "DataSource",
})

_DUNDER_TEXT = re.compile(r"__[A-Za-z][A-Za-z0-9_]*__")

_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "bool", "callable", "chr", "complex", "dict", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "int", "isinstance", "iter",
    "len", "list", "map", "max", "min", "next", "ord", "pow", "print", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip",
    "ArithmeticError", "AssertionError", "AttributeError", "Exception", "IndexError",
    "KeyError", "NotImplementedError", "RuntimeError", "StopIteration", "TypeError",
    "ValueError", "ZeroDivisionError",
)

_MEM_LIMIT_BYTES = int(1.5 * 1024 * 1024 * 1024)  # 1.5 GB

def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")

def _is_blocked_name(name: str) -> bool:
    """Dunder names outside the allowlist, module objects, and ctypes entrypoints."""
    if _is_dunder(name):
        return name not in _ALLOWED_DUNDERS
    return name in _BLOCKED_MODULE_NAMES or name in _CTYPES_NAMES

def _is_blocked_attr(name: str) -> bool:
    """Private and dunder attributes, frame internals, and blocked names."""
    if name.startswith("_") and name not in _ALLOWED_DUNDERS:
        return True
    return name in _REFLECTIVE_ATTRS or _is_blocked_name(name)

def _is_blocked_import(name: str) -> bool:
    """Import names may not be private either: `from cadquery import _x` is rejected."""
    return (name.startswith("_") and name not in _ALLOWED_DUNDERS) or _is_blocked_name(name)

def _attribute_chain(node: ast.Attribute) -> list:
    """Names along an attribute chain, root first: cq.occ.np.load -> [cq, occ, np, load]."""
    names = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    return names[::-1]

def _node_violation(node) -> str | None:
    """Policy message for one AST node, or None. Each rule closes a reachability path."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] not in _ALLOWED_MODULES or any(_is_blocked_import(p) for p in parts):
                return (
                    f"Security violation: import of '{alias.name}' is not "
                    f"allowed. Permitted modules: {sorted(_ALLOWED_MODULES)}."
                )
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            return "Security violation: relative imports are not allowed."
        parts = node.module.split(".")
        if parts[0] not in _ALLOWED_MODULES or any(_is_blocked_import(p) for p in parts):
            return (
                f"Security violation: import from '{node.module}' is "
                f"not allowed. Permitted modules: {sorted(_ALLOWED_MODULES)}."
            )
        for alias in node.names:
            if _is_blocked_import(alias.name):
                return f"Security violation: import of '{alias.name}' is banned."
    elif isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in _BANNED_BUILTINS:
            return f"Security violation: call to '{func.id}()' is banned."
    elif isinstance(node, ast.Name):
        if _is_blocked_name(node.id) or (isinstance(node.ctx, ast.Load) and node.id in _BANNED_BUILTINS):
            return f"Security violation: use of '{node.id}' is banned."
    elif isinstance(node, ast.Attribute):
        if _is_blocked_attr(node.attr):
            return f"Security violation: access to '{node.attr}' is banned."
        if node.attr in _NUMPY_FILE_NAMES and _NUMPY_ROOTS.intersection(_attribute_chain(node)[:-1]):
            return f"Security violation: numpy file entrypoint '{node.attr}' is banned."
    elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        text = node.value if isinstance(node.value, str) else node.value.decode("latin-1")
        if _DUNDER_TEXT.search(text) and text not in _ALLOWED_DUNDERS:
            return "Security violation: string literal names a dunder attribute and is banned."
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        if _is_blocked_name(node.name):
            return f"Security violation: definition of '{node.name}' is banned."
    elif isinstance(node, ast.arg):
        if _is_blocked_name(node.arg):
            return f"Security violation: parameter '{node.arg}' is banned."
    elif isinstance(node, ast.MatchClass):
        for name in node.kwd_attrs:
            if _is_blocked_attr(name):
                return f"Security violation: pattern attribute '{name}' is banned."
    return None

def _check_ast_security(script_string: str) -> str | None:
    """Return an error message if the script imports or calls banned targets.

    Uses an ALLOWLIST for imports: only modules in _ALLOWED_MODULES may be
    imported. The walk also rejects other paths to banned targets: dunder and
    private names and attributes, frame internals, bare references to banned
    builtins, module objects such as sys or os, ctypes and numpy file
    entrypoints, and dunder text in string literals. Malformed input (NUL bytes,
    non-text, parser limits) is reported as a message and never raised.
    """
    try:
        tree = ast.parse(script_string)
    except SyntaxError as exc:
        return f"SyntaxError in script: {exc}"
    except (ValueError, TypeError, RecursionError) as exc:
        return f"Invalid script: {exc}"

    for node in ast.walk(tree):
        message = _node_violation(node)
        if message:
            return message
    return None
