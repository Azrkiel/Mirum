"""Real native CLI plus injected I/O faults to verify result preservation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import cadquery as cq
import pytest

from backend.dragsters import cli

EXAMPLE = Path(__file__).parents[1] / 'dragsters/examples/wedge.json'


def args(root, spec=EXAMPLE):
    return ['--spec', str(spec), '--output-root', str(root)]


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_success_reload_and_changed_dimension(tmp_path, capsys):
    root = tmp_path/'results'
    assert cli.main(args(root)) == 0
    first = Path(capsys.readouterr().out.strip())
    assert first.is_absolute() and first.parent == root
    assert {p.name for p in first.iterdir()} == {'body.step','spec.json','report.json'}
    report = json.loads((first/'report.json').read_text())
    assert report['passed'] is True
    assert report['step_sha256'] == hashlib.sha256((first/'body.step').read_bytes()).hexdigest()
    assert report['spec_sha256'] == hashlib.sha256((first/'spec.json').read_bytes()).hexdigest()
    assert cli.main(args(root, first/'spec.json')) == 0
    second = Path(capsys.readouterr().out.strip())
    assert second != first
    rerun = json.loads((second/'report.json').read_text())
    assert rerun['bounds_mm'] == pytest.approx(report['bounds_mm'],abs=0.01)
    assert rerun['volume_mm3'] == pytest.approx(report['volume_mm3'],abs=0.1)
    edited = tmp_path/'edited.json'
    edited.write_text(json.dumps(json.loads((first/'spec.json').read_text()) | {'width_mm': 46.0}))
    assert cli.main(args(root, edited)) == 0
    third = Path(capsys.readouterr().out.strip())
    changed = json.loads((third/'report.json').read_text())
    assert changed['bounds_mm']['ymax'] == pytest.approx(23, abs=0.01)
    assert changed['volume_mm3'] > report['volume_mm3']


@pytest.mark.parametrize('failure', ['json','spec','missing','build','export','check','check_reject','report','publish','collision','mkdir'])
def test_failure_preserves_previous(tmp_path, monkeypatch, capsys, failure):
    root = tmp_path/'results'
    assert cli.main(args(root)) == 0
    capsys.readouterr()
    before = snapshot(root)
    directories = set(root.iterdir())
    spec = EXAMPLE

    def fail(*a, **kw):
        raise OSError('injected failure')

    if failure in ('json','spec','missing'):
        spec = tmp_path/'bad.json'
        if failure == 'json':
            spec.write_text('{')
        elif failure == 'spec':
            spec.write_text(json.dumps(json.loads(EXAMPLE.read_text()) | {'pocket_center_z_mm':40.0}))
    elif failure == 'build':
        from backend.dragsters import geometry
        monkeypatch.setattr(geometry, 'build_body', fail)
    elif failure == 'export':
        monkeypatch.setattr(cq.exporters, 'export', fail)
    elif failure == 'check':
        from backend.dragsters import validation
        monkeypatch.setattr(validation, 'verify_step', fail)
    elif failure == 'check_reject':
        from backend.dragsters import validation
        monkeypatch.setattr(validation, 'verify_step', lambda *a: {'passed': False})
    elif failure == 'collision':
        from types import SimpleNamespace
        previous = next(iter(directories))
        monkeypatch.setattr(cli.uuid, 'uuid4', lambda: SimpleNamespace(hex=previous.name.removeprefix('dragster-')))
    elif failure == 'report':
        original = Path.write_text
        def write(path, *a, **kw):
            if path.name == 'report.json':
                return fail()
            return original(path, *a, **kw)
        monkeypatch.setattr(Path, 'write_text', write)
    elif failure == 'publish':
        monkeypatch.setattr(Path, 'rename', fail)
    elif failure == 'mkdir':
        monkeypatch.setattr(Path, 'mkdir', fail)
    assert cli.main(args(root, spec)) == (2 if failure in ('json','spec') else 1)
    captured = capsys.readouterr()
    assert not captured.out and captured.err
    assert snapshot(root) == before
    assert set(root.iterdir()) == directories


@pytest.mark.parametrize('argv', [[], ['--unknown'], ['--spec','x']])
def test_argument_errors(argv, capsys):
    assert cli.main(argv) == 2
    assert not capsys.readouterr().out


def test_invalid_input_does_not_import_cad(tmp_path):
    bad = tmp_path/'bad.json'
    bad.write_text('{')
    code = '''import sys
from backend.dragsters.cli import main
assert 'cadquery' not in sys.modules
assert main(sys.argv[1:]) == 2
assert 'cadquery' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code, *args(tmp_path/'out', bad)], capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path/'out').exists()
