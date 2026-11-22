"""Readiness probe script for the OCCT adapter. Run as a script in an owned subprocess.

Stdlib plus cadquery/OCP only. Prints exactly one JSON line. Never imported by the server process.
Exit 0 with {"cadquery", "ocp", "volume"}; exit 3 with {"error": "import"} when the runtime is missing.
"""
import json
import sys


def _version(module, dist):
    value = getattr(module, '__version__', None)
    if isinstance(value, str) and value:
        return value
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return 'unknown'


def main():
    try:
        import cadquery as cq
        import OCP
    except ImportError:
        print(json.dumps({'error': 'import'}))
        return 3
    volume = float(cq.Workplane('XY').box(1, 2, 3).val().Volume())
    print(json.dumps({'cadquery': _version(cq, 'cadquery'), 'ocp': _version(OCP, 'cadquery-ocp'),
                      'volume': volume}, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
