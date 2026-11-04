"""Source/runtime binding without reading credentials or mutable job data."""
from functools import lru_cache
import hashlib
from importlib.metadata import version
from importlib.util import find_spec
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
    # The compose layout mounts analysis at /opt/analysis; source checkouts
    # keep it alongside backend. Bind the imported adapters in either layout.
    analysis = find_spec('analysis')
    if analysis is None or analysis.origin is None:
        raise ValueError('Independent analysis adapter package is unavailable')
    for name in ('airfoil.py', 'avl.py', 'lifting_line.py'):
        path = Path(analysis.origin).parent/name
        hashes['analysis/'+name] = hashlib.sha256(path.read_bytes()).hexdigest()
    runtime = {key: version(key) for key in ('aerosandbox', 'neuralfoil', 'numpy', 'scipy', 'trimesh', 'casadi')}
    runtime['python'] = platform.python_version()
    fingerprint = digest(dict(sources=hashes, runtime=runtime))
    return dict(cem_version=VERSION + '+' + fingerprint[:12], implementation_sha256=fingerprint,
                source_hashes=hashes, runtime=runtime)
