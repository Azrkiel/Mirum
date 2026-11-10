"""Hardware checks: wheel envelopes, stock containment, body mass (stub; D6 replaces the bodies).

Signatures are the D6 contract. ``wheel_envelopes`` and ``summary`` stay pure (frontend imports
them); CadQuery/OCP may only be imported inside ``cad_checks``.
"""
from __future__ import annotations


def wheel_envelopes(spec) -> list[dict]:
    return []


def cad_checks(body, spec) -> dict[str, dict]:
    return {}


def summary(spec, volume_mm3, bounds) -> dict:
    return {'hardware': {}, 'mass': {'value_g': None, 'basis': 'body_only',
                                     'reason': 'hardware checks not implemented'}}
