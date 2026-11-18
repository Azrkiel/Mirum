"""Real-builder regen tests. Skipped unless REGEN_NATIVE=1 and REGEN_BUILDER exists.

Run inside the picogk-regen image by .github/workflows/picogk-runtime.yml (--network none).
Everywhere else these skip, because they need the native PicoGK runtime.
"""
import hashlib
import json
import os
import shutil
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


def _app_copy(tmp_path, name, with_so):
    """Copy the whole builder dir (mode bits preserved), optionally without the runtime .so."""
    dst = tmp_path / name
    shutil.copytree(BUILDER.parent, dst)
    if not with_so:
        (dst / regen_cli.RUNTIME_SO).unlink()
    assert os.access(dst / 'builder', os.X_OK), 'copied builder must stay executable'
    return dst / 'builder'


def test_missing_runtime_so_is_unavailable_for_the_right_reason(tmp_path, monkeypatch, capsys):
    spec = EXAMPLES / 'small.json'
    # Positive control: same copy with the .so present starts, runs and builds an STL.
    good = _app_copy(tmp_path, 'with_so', True)
    stats, err = regen_cli.run_builder(good, spec, tmp_path / 'ctl.stl')
    assert err is None and isinstance(stats, dict)
    assert (tmp_path / 'ctl.stl').stat().st_size > 84

    # Missing .so: the exec bit is intact, so the builder starts and then fails loading the library.
    bad = _app_copy(tmp_path, 'no_so', False)
    monkeypatch.setenv('REGEN_BUILDER', str(bad))
    out = tmp_path / 'o'
    assert _run('small.json', out) == regen_cli.EXIT_UNAVAILABLE
    stderr = capsys.readouterr().err
    assert stderr.startswith('UNAVAILABLE: builder exited '), stderr
    assert 'Permission denied' not in stderr and 'could not start' not in stderr
    assert 'picogk' in stderr.lower(), stderr
    assert not (out / 'report.json').exists() and not (out / 'cleaned.stl').exists()
    assert [p for p in out.iterdir() if not p.name.startswith('.work-')] == []
