"""Style registry: style name -> StyleDefinition (stdlib + pydantic only).

Builders are referenced by string and imported lazily by ``resolve_build`` so that importing
the registry never pulls in CadQuery. Pure profile-law functions live in ``profiles`` so that
validation can use them without touching any builder (master plan § 6.3).
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Callable

_PACKAGE_DIR = Path(__file__).resolve().parents[1]
_DRAGSTERS_PACKAGE = __name__.rsplit('.', 1)[0]   # 'backend.dragsters' or 'dragsters'


@dataclass(frozen=True)
class StyleDefinition:
    name: str                 # 'wedge' | 'teardrop' | 'narrow_waist'
    label: str                # UI label
    generator_version: str    # e.g. 'dragster_teardrop_v2'
    build: str                # 'dragsters.styles.<module>:build', resolved lazily (cadquery inside)
    preset_path: str          # relative to the dragsters package, e.g. 'presets/teardrop.json'


REGISTRY: dict[str, StyleDefinition] = {
    'wedge': StyleDefinition('wedge', 'Low wedge', 'dragster_wedge_v2',
                             'dragsters.styles.wedge:build', 'presets/wedge.json'),
    'teardrop': StyleDefinition('teardrop', 'Smooth teardrop', 'dragster_teardrop_v2',
                                'dragsters.styles.teardrop:build', 'presets/teardrop.json'),
    'narrow_waist': StyleDefinition('narrow_waist', 'Narrow waist + raised rear housing',
                                    'dragster_narrow_waist_v2', 'dragsters.styles.narrow_waist:build',
                                    'presets/narrow_waist.json'),
}


def resolve_build(style: str) -> Callable:
    """Import and return the style's ``build(spec)``; ``KeyError`` for an unknown style."""
    module_path, attr = REGISTRY[style].build.split(':')
    # 'dragsters.<rest>' is resolved inside whichever package this registry was imported from,
    # so 'backend.dragsters' and 'dragsters' never load two copies of the same module.
    root, _, rest = module_path.partition('.')
    if root != 'dragsters':
        raise ValueError(f'build path {module_path!r} must live in the dragsters package')
    module = importlib.import_module(f'{_DRAGSTERS_PACKAGE}.{rest}')
    return getattr(module, attr)


def load_preset(style: str):
    """Load and strictly validate the style's v2 preset; ``KeyError`` for an unknown style."""
    from ..spec_v2 import parse_v2

    spec = parse_v2((_PACKAGE_DIR / REGISTRY[style].preset_path).read_text(encoding='utf-8'))
    if spec.style != style:
        raise ValueError(f'preset for {style!r} declares style {spec.style!r}')
    return spec
