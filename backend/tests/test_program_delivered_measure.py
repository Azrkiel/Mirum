"""F07 host tests: the bounded GLB parser of the measurement worker and the stdout parsing.

Pure host tests (no OCP): the worker script is imported by path and only parse_glb is called.
"""
import importlib.util
import json
import math
import struct
from pathlib import Path

import pytest

from engineering.program.adapters.occt import parse_measure_stdout

SCRIPT = Path(__file__).resolve().parents[1] / 'engineering' / 'program' / 'adapters' / '_occt_measure.py'
_spec = importlib.util.spec_from_file_location('occt_measure_under_test', SCRIPT)
measure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(measure)

S = math.sqrt(0.5)
ROT_X_NEG_90 = [-S, 0.0, 0.0, S]
BOX = [(x, y, z) for x in (0, 1) for y in (0, 2) for z in (0, 3)]


def pack(gltf, bin_bytes=b'', *, length=None, json_size=None, bin_size=None, bin_chunk=True):
    body = json.dumps(gltf).encode('ascii')
    body += b' ' * (-len(body) % 4)
    out = struct.pack('<II', len(body) if json_size is None else json_size, 0x4E4F534A) + body
    if bin_chunk:
        out += struct.pack('<II', len(bin_bytes) if bin_size is None else bin_size, 0x004E4942) + bin_bytes
    total = 12 + len(out)
    return struct.pack('<4sII', b'glTF', 2, total if length is None else length) + out


def positions(points):
    return b''.join(struct.pack('<fff', *p) for p in points)


def box_gltf(points=BOX, *, component=5126, mode=4, count=None, nodes=None, mesh_nodes=None, names=None):
    n = len(points) if count is None else count
    gltf = {
        'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': nodes or [{'name': 'root', 'rotation': ROT_X_NEG_90, 'children': [1]},
                           {'name': 'part', 'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'mode': mode}]}],
        'accessors': [{'bufferView': 0, 'componentType': component, 'count': n, 'type': 'VEC3'},
                      {'componentType': 5123, 'count': 36, 'type': 'SCALAR'}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': n * 12}],
        'buffers': [{'byteLength': n * 12}],
    }
    return gltf


def box_glb(**kw):
    return pack(box_gltf(**kw), positions(BOX))


def test_box_with_the_minus_90_x_rotation_maps_back_to_the_cadquery_frame():
    out = measure.parse_glb(box_glb())
    assert 'error' not in out
    assert out['bounds_mm'] == pytest.approx([0, 0, 0, 1, 2, 3], abs=1e-9)
    assert out['vertex_count'] == 8
    assert out['triangle_count'] == 12


def test_node_names_collected_ascii_and_bounded():
    nodes = [{'name': 'caf' + chr(0xe9) + 'x' * 200, 'rotation': ROT_X_NEG_90, 'children': [1]},
             {'name': 'part', 'mesh': 0}]
    out = measure.parse_glb(pack(box_gltf(nodes=nodes), positions(BOX)))
    assert all(len(n) <= 64 and all(0x20 <= ord(c) <= 0x7e for c in n) for n in out['node_names'])
    many = [{'name': f'n{i}', 'children': [i + 1]} for i in range(15)] + [{'name': 'leaf', 'mesh': 0}]
    out = measure.parse_glb(pack(box_gltf(nodes=many), positions(BOX)))
    assert len(out['node_names']) == 16


def test_header_length_mismatch():
    assert measure.parse_glb(pack(box_gltf(), positions(BOX), length=99999)) == {'error': 'bad_header'}
    assert measure.parse_glb(b'glTF') == {'error': 'bad_header'}
    assert measure.parse_glb(b'') == {'error': 'bad_header'}


def test_chunk_length_past_eof():
    out = measure.parse_glb(pack(box_gltf(), positions(BOX), bin_size=10 ** 6))
    assert out == {'error': 'bad_chunk'}
    out = measure.parse_glb(pack(box_gltf(), positions(BOX), json_size=10 ** 6))
    assert out == {'error': 'bad_chunk'}


def test_oversize_json_chunk():
    gltf = box_gltf()
    gltf['extras'] = 'x' * (1024 * 1024 + 8)
    assert measure.parse_glb(pack(gltf, positions(BOX))) == {'error': 'json_too_large'}


def test_depth_17_chain_rejected_and_16_accepted():
    def chain(n):
        nodes = [{'children': [i + 1]} for i in range(n - 1)] + [{'mesh': 0}]
        return pack(box_gltf(nodes=nodes), positions(BOX))
    assert measure.parse_glb(chain(17)) == {'error': 'node_depth'}
    assert 'error' not in measure.parse_glb(chain(16))


def test_node_cycle_rejected():
    nodes = [{'children': [1]}, {'children': [0], 'mesh': 0}]
    assert measure.parse_glb(pack(box_gltf(nodes=nodes), positions(BOX))) == {'error': 'node_cycle'}


def test_too_many_nodes_and_vertices():
    nodes = [{} for _ in range(257)]
    nodes[0] = {'mesh': 0}
    assert measure.parse_glb(pack(box_gltf(nodes=nodes), positions(BOX))) == {'error': 'too_many_nodes'}
    big = box_gltf(count=2_000_001)
    assert measure.parse_glb(pack(big, positions(BOX))) == {'error': 'too_many_vertices'}


def test_accessor_past_the_bin_chunk():
    assert measure.parse_glb(pack(box_gltf(count=100), positions(BOX))) == {'error': 'accessor_past_buffer'}


def test_non_float_position_and_non_triangle_mode():
    assert measure.parse_glb(box_glb(component=5123)) == {'error': 'bad_position_accessor'}
    assert measure.parse_glb(box_glb(mode=1)) == {'error': 'bad_primitive_mode'}
    assert measure.parse_glb(box_glb(mode=5)) == {'error': 'bad_primitive_mode'}


def test_nan_position_and_nan_json_rejected():
    bad = positions(BOX[:-1] + [(float('nan'), 0.0, 0.0)])
    assert measure.parse_glb(pack(box_gltf(), bad)) == {'error': 'non_finite_position'}
    glb = pack(box_gltf(), positions(BOX))
    assert measure.parse_glb(glb.replace(b'"version": "2.0"', b'"version": NaN  ')) == {'error': 'json_invalid'}


def test_no_bin_chunk_is_an_error_not_a_crash():
    assert 'error' in measure.parse_glb(pack(box_gltf(), bin_chunk=False))


def test_garbage_never_raises():
    for blob in (b'\x00' * 40, b'glTF' + b'\xff' * 64, struct.pack('<4sII', b'glTF', 2, 12)):
        assert 'error' in measure.parse_glb(blob)


# ---- stdout parsing ----

def face(i):
    return {'surface': 'plane', 'normal': [0, 0, 1], 'center': [i, 0, 0], 'area': 1.0,
            'bounds': [i, 0, 0, i, 1, 1], 'name': None}


def table(n=6):
    return {'solid_count': 1, 'closed_solid_count': 1, 'bounds': [0, 0, 0, 1, 1, 1], 'volume': 1.0,
            'area': 6.0, 'max_topology_tolerance': 1e-7,
            'native_versions': [{'name': 'cadquery', 'version': '2.6.1'}, {'name': 'OCP', 'version': '7.8.1'}],
            'faces': [face(i) for i in range(n)]}


MESH = {'triangle_count': 12, 'vertex_count': 8, 'bounds_mm': [0, 0, 0, 1, 1, 1], 'node_names': ['a']}


def test_valid_stdout_yields_table_and_mesh():
    out = parse_measure_stdout(json.dumps({'table': table(), 'mesh': MESH}), want_mesh=True)
    assert out.reason is None and out.table is not None and out.mesh.mesh_bound_mm == 0.1
    assert out.mesh_error is None
    out = parse_measure_stdout(json.dumps({'table': table()}), want_mesh=False)
    assert out.table is not None and out.mesh is None


def test_nan_and_oversize_stdout_rejected():
    raw = json.dumps({'table': table()}).replace('"volume": 1.0', '"volume": NaN')
    assert parse_measure_stdout(raw, want_mesh=False).reason == 'native_failed'
    assert parse_measure_stdout('x' * (1024 * 1024 + 1), want_mesh=False).reason == 'native_failed'
    assert parse_measure_stdout('[]', want_mesh=False).reason == 'native_failed'
    assert parse_measure_stdout(None, want_mesh=False).reason == 'native_failed'
    assert parse_measure_stdout('{"error": "native_failed"}', want_mesh=False).table is None


def test_too_many_faces_has_no_table():
    out = parse_measure_stdout('{"error": "too_many_faces"}', want_mesh=True)
    assert out.table is None and out.reason == 'too_many_faces'
    # a table that smuggles 65 faces is refused by validation (no partial table)
    out = parse_measure_stdout(json.dumps({'table': table(65)}), want_mesh=False)
    assert out.table is None and out.reason == 'native_failed'
    assert parse_measure_stdout(json.dumps({'table': table(64)}), want_mesh=False).table is not None


def test_bad_glb_keeps_the_table_and_fails_the_mesh_closed():
    out = parse_measure_stdout(json.dumps({'table': table(), 'mesh': {'error': 'node_cycle'}}), want_mesh=True)
    assert out.table is not None and out.mesh_error == 'node_cycle'
    assert out.mesh.triangle_count == 0 and out.mesh.vertex_count == 0
    out = parse_measure_stdout(json.dumps({'table': table()}), want_mesh=True)
    assert out.mesh_error == 'glb_invalid'
