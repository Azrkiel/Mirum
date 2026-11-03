"""Immutable content-addressed calibration snapshots and identifiable updates."""
import copy
import json
import math
import os
from pathlib import Path
import re

from cem.core import canonical_json, digest

DEFAULT = Path(__file__).parents[1] / 'calibration/glider.json'


def initial():
    return json.loads(DEFAULT.read_text())


def version(calibration):
    return digest(calibration)


def root():
    return Path(os.environ.get('MIRUM_WORK_DIR', '/tmp/mirum/work')) / 'cem/calibration'


def load(identifier=None):
    base = initial()
    if identifier in (None, version(base)):
        return base
    if not re.fullmatch(r'[0-9a-f]{64}', identifier):
        raise ValueError('Invalid calibration version')
    path = root() / f'{identifier}.json'
    if path.stat().st_size > 65536:
        raise ValueError('Calibration evidence exceeds budget')
    value = json.loads(path.read_text())
    if version(value) != identifier:
        raise ValueError('Calibration content does not match its version')
    return value


def update_drag(calibration, observed_ratios):
    """Log-normal update of identifiable *profile* drag observations only.

    Each ratio comes from a fully specified flight condition with known measured
    launch height. Launch efficiency, mass, CLmax and e are NOT simultaneously
    fitted from duration/range: those coefficients are not identifiable there.
    """
    if not observed_ratios or len(observed_ratios) > 200:
        raise ValueError('A bounded set of observations is required')
    if any(not math.isfinite(x) or not .25 <= x <= 4 for x in observed_ratios):
        raise ValueError('Observation outside supported calibration envelope')
    updated = copy.deepcopy(calibration)
    p = calibration['cd0_factor']
    prior_precision = 1 / p['log_sigma'] ** 2
    noise_sigma = .10
    precision = prior_precision + len(observed_ratios) / noise_sigma**2
    mean = (math.log(p['median']) * prior_precision +
            sum(math.log(x) for x in observed_ratios) / noise_sigma**2) / precision
    updated['cd0_factor'] = dict(median=math.exp(mean), log_sigma=math.sqrt(1 / precision))
    updated.update(version='flight-posterior-1', parent=version(calibration),
                   state='flight_updated_screening_model', observation_count=len(observed_ratios))
    return updated


def save(calibration):
    identifier = version(calibration)
    root().mkdir(parents=True, exist_ok=True)
    path = root() / f'{identifier}.json'
    # Content-addressed immutable writes; concurrent updates produce distinct
    # versions and cannot silently overwrite each other's "latest" calibration.
    try:
        with path.open('x') as f:
            f.write(canonical_json(calibration))
    except FileExistsError:
        if load(identifier) != calibration:
            raise ValueError('Calibration collision')
    return identifier
