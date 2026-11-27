"""Kernel adapters (F03).

`adapter_for(backend, mode=...)` returns exactly the adapter for that backend id; it never falls
back to another backend. Mode 'mock' returns the geometry-free MockKernelAdapter. Mode 'native'
imports the native adapter lazily from `.occt`, `.picogk` or `.native` (added by those modules), so importing
this package never loads a native kernel, a web framework or a client library.
"""
from __future__ import annotations

from .mock import MockKernelAdapter

__all__ = ['adapter_for', 'MockKernelAdapter']

_BACKENDS = ('occt', 'picogk', 'mirum-native')


def adapter_for(backend: str, *, mode: str = 'native', **kw):
    if backend not in _BACKENDS:
        raise ValueError(f'unknown backend {backend!r}; expected one of {list(_BACKENDS)}')
    if mode == 'mock':
        return MockKernelAdapter(backend)
    if mode != 'native':
        raise ValueError(f'unknown mode {mode!r}; expected native or mock')
    if backend == 'mirum-native':
        # Never falls back to OCCT: this is the only constructor reachable for 'mirum-native'.
        from .native import NativePlateAdapter
        return NativePlateAdapter(**kw)
    if backend == 'occt':
        from .occt import OcctAdapter
        return OcctAdapter(**kw)
    from .picogk import PicogkAdapter
    return PicogkAdapter(**kw)
