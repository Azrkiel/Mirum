"""regen_cli exit codes without PicoGK."""
import json

from engineering import regen_cli
from tests.test_regen_spec import spec_data


def test_invalid_spec_exits_2_without_running_builder(tmp_path, monkeypatch):
    monkeypatch.setenv('REGEN_BUILDER', str(tmp_path / 'no-such-builder'))
    sp = tmp_path / 's.json'
    sp.write_text(json.dumps(spec_data(channel_count=48, channel_radius_mm=1.1, throat_radius_mm=15.0,
                                       wall_thickness_mm=5.0, convergent_length_mm=40.0)))
    out = tmp_path / 'out'
    assert regen_cli.main([str(sp), str(out)]) == 2
    assert not out.exists() or list(out.iterdir()) == []


def test_missing_builder_exits_1_and_emits_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv('REGEN_BUILDER', str(tmp_path / 'no-such-builder'))
    sp = tmp_path / 's.json'
    sp.write_text(json.dumps(spec_data()))
    out = tmp_path / 'out'
    assert regen_cli.main([str(sp), str(out)]) == 1
    assert list(out.iterdir()) == []


def _passing_verify(monkeypatch):
    from engineering import regen_mesh

    monkeypatch.setattr(regen_mesh, 'read_stl', lambda path: 'tris')
    monkeypatch.setattr(regen_mesh, 'sha256_file', lambda path: 'a' * 64)
    monkeypatch.setattr(regen_mesh, 'stl_bytes', lambda cleaned: b'stl')
    monkeypatch.setattr(regen_mesh, 'verify', lambda *a, **k: (
        {'passed': True, 'failures': [], 'stl_sha256': 'b' * 64, 'raw_stl_sha256': 'a' * 64}, 'cleaned'))


def test_prebuilt_stl_that_passes_is_never_accepted(tmp_path, monkeypatch):
    _passing_verify(monkeypatch)
    monkeypatch.setenv('REGEN_BUILDER', str(tmp_path / 'no-such-builder'))
    sp = tmp_path / 's.json'
    sp.write_text(json.dumps(spec_data()))
    stl = tmp_path / 'other.stl'
    stl.write_bytes(b'x')
    out = tmp_path / 'out'
    assert regen_cli.main([str(sp), str(out), '--prebuilt-stl', str(stl)]) == 3
    assert not (out / 'report.json').exists()
    assert not (out / 'cleaned.stl').exists()
    assert json.loads((out / 'prebuilt_report.json').read_text())['geometry_source'] == 'prebuilt_test_hook'


def test_missing_runtime_exits_1_and_emits_nothing(tmp_path, monkeypatch):
    _passing_verify(monkeypatch)
    monkeypatch.setenv('REGEN_BUILDER', str(tmp_path / 'builder'))  # no .so beside it
    monkeypatch.setattr(regen_cli, 'run_builder', lambda *a: ({'tris': 1}, None))
    sp = tmp_path / 's.json'
    sp.write_text(json.dumps(spec_data()))
    out = tmp_path / 'out'
    assert regen_cli.main([str(sp), str(out)]) == 1
    assert list(out.iterdir()) == []


def test_builder_run_with_runtime_is_accepted_and_labelled(tmp_path, monkeypatch):
    _passing_verify(monkeypatch)
    (tmp_path / 'picogk.26.2.so').write_bytes(b'so')
    monkeypatch.setenv('REGEN_BUILDER', str(tmp_path / 'builder'))
    monkeypatch.setattr(regen_cli, 'run_builder', lambda *a: ({'tris': 1}, None))
    sp = tmp_path / 's.json'
    sp.write_text(json.dumps(spec_data()))
    out = tmp_path / 'out'
    assert regen_cli.main([str(sp), str(out)]) == 0
    report = json.loads((out / 'report.json').read_text())
    assert report['geometry_source'] == 'picogk_builder'
    assert report['safety_notice'] == regen_cli.SAFETY_NOTICE


def test_malformed_json_exits_2(tmp_path):
    sp = tmp_path / 's.json'
    sp.write_text('{not json')
    assert regen_cli.main([str(sp), str(tmp_path / 'o')]) == 2
