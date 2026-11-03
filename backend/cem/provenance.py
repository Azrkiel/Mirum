"""Source/runtime binding without reading credentials or mutable job data."""
from functools import lru_cache
import hashlib
from importlib.metadata import version
from pathlib import Path
import platform

from cem import VERSION
from cem.core import digest


@lru_cache(maxsize=1)
def provenance():
    root = Path(__file__).resolve().parents[1]
    paths = list((root / 'cem').rglob('*.py'))
    paths += list((root / 'cem/data').glob('*.json'))
    paths += list((root / 'gliders/model').glob('*.py'))
    paths += [root / 'gliders' / name for name in ('spec.py', 'search.py', 'design_spec.py')]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    runtime = {key: version(key) for key in ('aerosandbox', 'neuralfoil', 'numpy', 'scipy', 'trimesh', 'casadi')}
    runtime['python'] = platform.python_version()
    fingerprint = digest(dict(sources=hashes, runtime=runtime))
    return dict(cem_version=VERSION + '+' + fingerprint[:12], implementation_sha256=fingerprint,
                source_hashes=hashes, runtime=runtime)
