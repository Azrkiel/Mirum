"""Native shared delivery checks; no model calls or missing-kernel skips."""
import asyncio
import hashlib
import importlib
import importlib.util
import os
import struct

import pytest

os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')


def service():
    assert importlib.util.find_spec('verification.acceptance') is not None, 'Missing shared acceptance service'
    return importlib.import_module('verification.acceptance')


def test_native_delivery_is_measured_and_bound_to_step_and_glb():
    acceptance = service()
    source = "import cadquery as cq\nresult = cq.Workplane('XY').box(10,20,30)\n"
    delivery = asyncio.run(acceptance.accept_script(source))
    cert = delivery['geometry_certificate']
    assert cert['passed'] is True
    assert cert['scope'] == 'structural_solid_set_v1'
    assert cert['solid_count'] == 1
    assert cert['volume_mm3'] == pytest.approx(6000)
    assert cert['engineering_certified'] is False
    assert delivery['glb'][:4] == b'glTF'
    assert struct.unpack_from('<I', delivery['glb'], 8)[0] == len(delivery['glb'])
    assert b'ISO-10303-21' in delivery['step']
    assert cert['artifacts']['step_sha256'] == hashlib.sha256(delivery['step']).hexdigest()
    assert cert['artifacts']['glb_sha256'] == hashlib.sha256(delivery['glb']).hexdigest()
    assert cert['artifacts']['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()


def test_named_multi_body_assembly_survives_trusted_step_rendering():
    import json
    source = ("import cadquery as cq\nresult = cq.Assembly(name='wing')\n"
              "result.add(cq.Workplane('XY').box(10,20,3), name='skin')\n"
              "result.add(cq.Workplane('XY').box(2,20,2).translate((20,0,0)), name='spar')\n")
    delivery = asyncio.run(service().accept_script(source))
    assert delivery['geometry_certificate']['solid_count'] == 2
    assert delivery['geometry_certificate']['volume_mm3'] == pytest.approx(680)
    size = struct.unpack_from('<I', delivery['glb'], 12)[0]
    scene = json.loads(delivery['glb'][20:20+size])
    names = {n.get('name') for n in scene['nodes']}
    assert 'skin' in names and 'spar' in names, names


def test_capture_executes_generated_source_only_once():
    from compiler import execute_cad_script
    from unittest.mock import AsyncMock
    execute = AsyncMock(side_effect=execute_cad_script)
    delivery = asyncio.run(service().accept_script(
        "import cadquery as cq\nresult = cq.Workplane('XY').box(1,2,3)\n", execute=execute))
    assert delivery['geometry_certificate']['passed'] is True
    execute.assert_awaited_once()
