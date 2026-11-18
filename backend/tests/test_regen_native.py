"""Real-builder regen tests. Skipped unless REGEN_NATIVE=1 and REGEN_BUILDER exists.

Run inside the picogk-regen image by .github/workflows/picogk-runtime.yml (--network none).
Everywhere else these skip, because they need the native PicoGK runtime.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from engineering import regen_cli

BUILDER = Path(os.environ.get('REGEN_BUILDER', '/opt/regen/app/builder'))
EXAMPLES = Path(__file__).resolve().parents[2] / 'picogk-builder' / 'examples'

pytestmark = pytest.mark.skipif(
    os.environ.get('REGEN_NATIVE') != '1' or not BUILDER.exists(),
    reason='needs the native PicoGK builder (REGEN_NATIVE=1, REGEN_BUILDER)')


def _run(spec_name, out):
    return regen_cli.main([str(EXAMPLES / spec_name), str(out)])


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_reference_spec_is_accepted(tmp_path):
    out = tmp_path / 'o'
    assert _run('reference.json', out) == regen_cli.EXIT_ACCEPTED
    report = json.loads((out / 'report.json').read_text())
    assert report['passed'] is True
    assert report['geometry_source'] == 'picogk_builder'
    assert (out / 'cleaned.stl').stat().st_size > 0


def test_small_spec_is_accepted_and_deterministic(tmp_path):
    hashes = []
    for i in range(2):
        out = tmp_path / f'o{i}'
        assert _run('small.json', out) == regen_cli.EXIT_ACCEPTED
        hashes.append(_sha(out / 'cleaned.stl'))
    assert hashes[0] == hashes[1]


def test_over_limit_spec_is_rejected_before_building(tmp_path):
    out = tmp_path / 'o'
    assert _run('too_many_channels.json', out) == regen_cli.EXIT_REJECTED
    assert not (out / 'report.json').exists()


def test_missing_runtime_so_is_unavailable_and_emits_nothing(tmp_path, monkeypatch):
    builder = tmp_path / 'app' / 'builder'
    builder.parent.mkdir()
    builder.write_bytes(BUILDER.read_bytes())  # no picogk.26.2.so beside it
    monkeypatch.setenv('REGEN_BUILDER', str(builder))
    out = tmp_path / 'o'
    assert _run('small.json', out) == regen_cli.EXIT_UNAVAILABLE
    assert not (out / 'report.json').exists() and not (out / 'cleaned.stl').exists()
