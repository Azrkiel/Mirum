"""Deterministic CadQuery geometry for the rectangular plate at the contract datum.

The plate occupies [0, a] x [0, b] x [0, h]. cadquery is imported lazily so this
module imports on hosts without it.
"""
from __future__ import annotations

from pathlib import Path

from .plate_spec import PlateSpec


def build_source(spec: PlateSpec) -> str:
    """CadQuery source for the plate; only validated floats are interpolated."""
    a, b, h = (float(spec.length_a_mm), float(spec.width_b_mm), float(spec.thickness_mm))
    return (
        'import cadquery as cq\n'
        f'result = cq.Workplane("XY").box({a!r}, {b!r}, {h!r}, centered=(False, False, False))\n'
    )


def build_plate(spec: PlateSpec):
    import cadquery as cq

    return cq.Workplane('XY').box(
        float(spec.length_a_mm), float(spec.width_b_mm), float(spec.thickness_mm),
        centered=(False, False, False),
    )


def export_step(spec: PlateSpec, path: str | Path) -> str:
    import cadquery as cq

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(build_plate(spec), str(target), exportType='STEP')
    return str(target)
