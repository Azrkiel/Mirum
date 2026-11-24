"""Geometry and analysis for Mirum’s engineering fixtures."""

from .design import Design, KernelError, load_design, parse_design
from .search import search_layout

__all__ = [
    "Design", "KernelError", "load_design", "parse_design", "search_layout",
    "build_delivery", "inspect_delivery", "inspect_step",
]


def __getattr__(name):
    """Load the native CAD stack only when an exchange or inspection API is used."""
    if name == "build_delivery":
        from .exchange import build_delivery
        return build_delivery
    if name in ("inspect_delivery", "inspect_step"):
        from . import inspection
        return getattr(inspection, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
