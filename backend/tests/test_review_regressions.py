"""Review regressions: real Linux CadQuery/OCP exports, offline model seams only."""
import base64
import json
import struct

import pytest
from fastapi.testclient import TestClient

# Reuse setup/helpers, not test functions (which pytest would collect twice).
from backend.tests.test_native_api_hardening_ports import agents, events, main, offline_models


ASSEMBLY_SOURCE = """import cadquery as cq
result = cq.Assembly(name='wing')
result.add(cq.Workplane('XY').box(10, 20, 3), name='skin')
result.add(cq.Workplane('XY').box(2, 20, 2).translate((20, 0, 0)), name='spar')
"""


def assert_valid_glb(encoded):
    blob = base64.b64decode(encoded)
    magic, version, length = struct.unpack_from('<4sII', blob)
    assert (magic, version, length) == (b'glTF', 2, len(blob))
    json_length, chunk_type = struct.unpack_from('<I4s', blob, 12)
    assert chunk_type == b'JSON'
    document = json.loads(blob[20:20 + json_length])
    assert document['meshes']
    assert document['scenes']


def test_generate_assembly_passes_required_certificate(offline_models, monkeypatch):
    agents.run_machinist.return_value = ASSEMBLY_SOURCE
    with TestClient(main.app) as client:
        result = events(client.post('/generate', json={'prompt': 'fixture wing assembly'}))[-1]
    assert result['status'] == 'done', result
    assert result['geometry_certificate']['passed'] is True
    assert result['geometry_certificate']['volume_mm3'] == pytest.approx(680)
    assert_valid_glb(result['glb'])


def test_generate_assembly_exports_native_step_for_aero(offline_models, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import cadquery as cq
    import analysis.airfoil as airfoil

    agents.run_machinist.return_value = ASSEMBLY_SOURCE
    monkeypatch.setattr(main, 'run_rea', AsyncMock(return_value=SimpleNamespace(
        inferred_domain='D', airfoil_spec=SimpleNamespace(naca_code='0012', chord_mm=10, span_mm=20),
        flight_env=None, propulsion_spec=None, material_class=None,
    )))

    def analyze_exported_step(step_path, naca_code, span_mm, chord_mm, flight_env):
        # The XFOIL boundary is deterministic; STEP creation/import is native OCP.
        shape = cq.importers.importStep(step_path).val()
        assert shape.isValid()
        assert shape.Volume() == pytest.approx(680)
        return dict(naca_code=naca_code, chord_mm=chord_mm, reynolds=500000,
                    mach=0.0, n_crit=9.0, alpha=[0.0, 2.0], Cl=[0.0, 0.2],
                    Cd=[0.01, 0.02], Cm=[0.0, 0.0], L_D=[0.0, 10.0],
                    converged_count=2, stall_alpha=None, Cl_max=0.2)

    analysis = Mock(side_effect=analyze_exported_step)
    monkeypatch.setattr(airfoil, 'analyze_wing_step', analysis)
    with TestClient(main.app) as client:
        stream = events(client.post('/generate', json={
            'prompt': 'fixture wing assembly', 'run_aero': True,
        }))
    result = stream[-1]
    assert result['status'] == 'done', result
    assert result['aero_report'] is not None, stream
    analysis.assert_called_once()
    assert result['aero_report']['converged_count'] == 2
    assert result['geometry_certificate']['passed'] is True
    assert_valid_glb(result['glb'])


def test_changed_refinement_returns_actual_runnable_script_without_old_certificate(offline_models, monkeypatch):
    from unittest.mock import AsyncMock
    from schemas import ManifestDiff

    revised_source = "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 20, 3)\n"
    monkeypatch.setattr(main, 'run_refiner', AsyncMock(return_value=ManifestDiff(
        modified_parts=['box'], updated_descriptions={'box': '20 x 20 x 3 mm box'},
    )))
    with TestClient(main.app) as client:
        original = events(client.post('/generate', json={'prompt': 'fixture box'}))[-1]
        assert original['geometry_certificate']['passed'] is True
        agents.run_machinist.return_value = revised_source
        revised = client.post('/refine', json={
            'original_manifest': original['manifest'], 'original_scripts': original['scripts'],
            'refinement_prompt': 'double the width',
        }).json()
        assert revised['status'] == 'ok', revised
        assert revised.get('script'), 'The editor needs the actual executed assembly script'
        assert 'output.glb' in revised['script']
        assert revised['updated_scripts'] == {'box': revised_source}
        assert revised['regenerated_parts'] == ['box']
        assert revised['geometry_certificate']['passed'] is True
        assert revised['geometry_certificate']['volume_mm3'] == pytest.approx(1200)
        assert revised['geometry_certificate'] != original['geometry_certificate']
        import hashlib
        assert revised['geometry_certificate']['artifacts']['glb_sha256'] == hashlib.sha256(
            base64.b64decode(revised['glb'])).hexdigest()
        assert 'aero_report' not in revised
        assert 'struct_report' not in revised
        assert_valid_glb(revised['glb'])
        rerun = client.post('/run', json={'script': revised['script']}).json()
        assert rerun['status'] == 'ok', rerun
        assert_valid_glb(rerun['glb'])
