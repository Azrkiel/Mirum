"""Delivered-geometry measurement worker (F07). Run as a script in an owned subprocess.

Stdlib plus OCP only; never imported by the server. Usage: _occt_measure.py STEP_PATH [GLB_PATH].
Prints exactly one JSON object on stdout (<= 1 MiB): {"table": {...}, "mesh": {...}} where "mesh" is
absent without a GLB path and {"error": "..."} when the GLB is unparsable or out of budget, or
{"error": "too_many_faces"} / {"error": "native_failed"} / {"error": "import"} for the STEP part.
There are no thresholds and no pass/fail here: the server evaluates against the reviewed check plan.

The GLB parser (parse_glb) is pure struct/json and imports no OCP, so host tests import this module
by path and call it directly. OCP is imported only inside measure_step.
"""
import json
import math
import os
import struct
import sys

MAX_FACES = 64
MAX_STEP_BYTES = 500 * 1024 * 1024
MAX_TOPOLOGY_ELEMENTS = 100000

# ---- GLB budgets (lead decision D12) ----
MAX_GLB_BYTES = 64 * 1024 * 1024
MAX_GLB_JSON_BYTES = 1024 * 1024
MAX_NODES = 256
MAX_MESHES = 256
MAX_ACCESSORS = 1024
MAX_PRIMITIVES = 1024
MAX_POSITION_VERTICES = 2000000
MAX_NODE_DEPTH = 16
MAX_NODE_NAMES = 64
MAX_NAME_LEN = 64

_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_FLOAT = 5126
_TRIANGLES = 4

# Frame convention, pinned by backend/tests/test_program_delivered_native.py on a known box.
# CadQuery 2.6.1 Assembly.save(exportType='GLB') (exportGLTF) applies
# assy.loc *= Location((0, 0, 0), (1, 0, 0), -90) as a node transform and no unit scaling, so a
# CadQuery point (x, y, z) mm lands at glTF (X, Y, Z) = (x, z, -y). The inverse used here is
# x = X, y = -Z, z = Y.
GLTF_TO_CADQUERY = 'x=X y=-Z z=Y'


def _to_cadquery(point):
    return (point[0], -point[2], point[1])


# --------------------------------------------------------------------------- GLB


class _GlbError(Exception):
    pass


def _ascii_name(value):
    if not isinstance(value, str):
        return None
    text = ''.join(c if 0x20 <= ord(c) <= 0x7e else '_' for c in value[:MAX_NAME_LEN])
    return text


def _matrix_mul(a, b):
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def _number_list(value, size):
    if not isinstance(value, list) or len(value) != size:
        raise _GlbError('bad_transform')
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise _GlbError('bad_transform')
        out.append(float(item))
    return out


def _node_matrix(node):
    if 'matrix' in node:
        if any(k in node for k in ('translation', 'rotation', 'scale')):
            raise _GlbError('bad_transform')
        return _number_list(node['matrix'], 16)
    t = _number_list(node['translation'], 3) if 'translation' in node else [0.0, 0.0, 0.0]
    q = _number_list(node['rotation'], 4) if 'rotation' in node else [0.0, 0.0, 0.0, 1.0]
    s = _number_list(node['scale'], 3) if 'scale' in node else [1.0, 1.0, 1.0]
    norm = math.sqrt(sum(c * c for c in q))
    if abs(norm - 1.0) > 1e-3:
        raise _GlbError('bad_transform')
    x, y, z, w = (c / norm for c in q)
    r = (
        (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)),
        (2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)),
        (2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)),
    )  # r[col][row]: column-major rotation
    m = [0.0] * 16
    for col in range(3):
        for row in range(3):
            m[col * 4 + row] = r[col][row] * s[col]
    m[12], m[13], m[14], m[15] = t[0], t[1], t[2], 1.0
    return m


def _index(value, size):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < size:
        raise _GlbError('bad_reference')
    return value


def _read_chunks(data):
    if len(data) > MAX_GLB_BYTES:
        raise _GlbError('glb_too_large')
    if len(data) < 20:
        raise _GlbError('bad_header')
    magic, version, length = struct.unpack_from('<4sII', data, 0)
    if magic != b'glTF' or version != 2 or length != len(data):
        raise _GlbError('bad_header')
    offset, json_bytes, bin_bytes = 12, None, None
    while offset < len(data):
        if offset + 8 > len(data):
            raise _GlbError('bad_chunk')
        size, kind = struct.unpack_from('<II', data, offset)
        offset += 8
        if size > len(data) - offset:
            raise _GlbError('bad_chunk')
        body = data[offset:offset + size]
        offset += size
        if json_bytes is None:
            if kind != _CHUNK_JSON:
                raise _GlbError('bad_chunk')
            if size > MAX_GLB_JSON_BYTES:
                raise _GlbError('json_too_large')
            json_bytes = body
        elif bin_bytes is None and kind == _CHUNK_BIN:
            bin_bytes = body
        else:
            raise _GlbError('bad_chunk')
    if json_bytes is None:
        raise _GlbError('bad_chunk')
    return json_bytes, bin_bytes


def _reject_constant(_name):
    raise ValueError('non-finite JSON constant')


def _mesh_positions(gltf, bin_bytes, mesh_index, cache):
    """Return (vertex_triples, triangle_count) for one mesh, validating every budget."""
    if mesh_index in cache:
        return cache[mesh_index]
    meshes, accessors = gltf.get('meshes', []), gltf.get('accessors', [])
    views = gltf.get('bufferViews', [])
    mesh = meshes[mesh_index]
    primitives = mesh.get('primitives') if isinstance(mesh, dict) else None
    if not isinstance(primitives, list) or not primitives:
        raise _GlbError('bad_mesh')
    vertices, triangles = [], 0
    for prim in primitives:
        if not isinstance(prim, dict) or prim.get('mode', _TRIANGLES) != _TRIANGLES:
            raise _GlbError('bad_primitive_mode')
        attributes = prim.get('attributes')
        if not isinstance(attributes, dict) or 'POSITION' not in attributes:
            raise _GlbError('bad_primitive')
        acc = accessors[_index(attributes['POSITION'], len(accessors))]
        if not isinstance(acc, dict) or acc.get('componentType') != _FLOAT or acc.get('type') != 'VEC3':
            raise _GlbError('bad_position_accessor')
        if 'sparse' in acc or acc.get('normalized'):
            raise _GlbError('bad_position_accessor')
        count = acc.get('count')
        if isinstance(count, bool) or not isinstance(count, int) or not 0 < count <= MAX_POSITION_VERTICES:
            raise _GlbError('too_many_vertices')
        view = views[_index(acc.get('bufferView'), len(views))]
        if not isinstance(view, dict) or view.get('buffer', 0) != 0 or bin_bytes is None:
            raise _GlbError('bad_buffer_view')
        stride = view.get('byteStride', 12)
        base = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0
               for v in (stride, base)) or stride < 12 or stride > 252:
            raise _GlbError('bad_buffer_view')
        span = base + (count - 1) * stride + 12
        if span > len(bin_bytes):
            raise _GlbError('accessor_past_buffer')
        if stride == 12:
            points = list(struct.iter_unpack('<fff', bin_bytes[base:base + count * 12]))
        else:
            points = [struct.unpack_from('<fff', bin_bytes, base + i * stride) for i in range(count)]
        for p in points:
            if not all(math.isfinite(c) for c in p):
                raise _GlbError('non_finite_position')
        vertices.extend(points)
        if 'indices' in prim:
            idx = accessors[_index(prim['indices'], len(accessors))]
            if not isinstance(idx, dict) or idx.get('type') != 'SCALAR' or idx.get('componentType') not in (
                    5121, 5123, 5125):
                raise _GlbError('bad_index_accessor')
            n = idx.get('count')
        else:
            n = count
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0 or n % 3:
            raise _GlbError('bad_triangle_count')
        triangles += n // 3
        if len(vertices) > MAX_POSITION_VERTICES:
            raise _GlbError('too_many_vertices')
    cache[mesh_index] = (vertices, triangles)
    return cache[mesh_index]


def parse_glb(data):
    """Bounded GLB parse. Returns {"triangle_count", "vertex_count", "bounds_mm", "node_names"} with
    bounds in the CadQuery frame, or {"error": "<code>"}. Never raises."""
    try:
        return _parse_glb(data)
    except _GlbError as exc:
        return {'error': str(exc)}
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, struct.error,
            RecursionError, MemoryError):
        return {'error': 'glb_invalid'}


def _parse_glb(data):
    json_bytes, bin_bytes = _read_chunks(bytes(data))
    try:
        gltf = json.loads(json_bytes.decode('utf-8'), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise _GlbError('json_invalid')
    if not isinstance(gltf, dict):
        raise _GlbError('json_invalid')
    nodes = gltf.get('nodes', [])
    for key, cap in (('nodes', MAX_NODES), ('meshes', MAX_MESHES), ('accessors', MAX_ACCESSORS)):
        value = gltf.get(key, [])
        if not isinstance(value, list):
            raise _GlbError('json_invalid')
        if len(value) > cap:
            raise _GlbError('too_many_' + key)
    if sum(len(m.get('primitives', [])) for m in gltf.get('meshes', []) if isinstance(m, dict)
           and isinstance(m.get('primitives', []), list)) > MAX_PRIMITIVES:
        raise _GlbError('too_many_primitives')
    scenes = gltf.get('scenes')
    if not isinstance(scenes, list) or not scenes:
        raise _GlbError('no_scene')
    scene = scenes[_index(gltf.get('scene', 0), len(scenes))]
    roots = scene.get('nodes') if isinstance(scene, dict) else None
    if not isinstance(roots, list) or not roots or len(roots) > MAX_NODES:
        raise _GlbError('no_scene')

    identity = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    triangle_count = vertex_count = 0
    names = []
    visited = set()
    cache = {}
    # Iterative walk with an explicit depth cap: (node index, parent matrix, depth, path).
    stack = [(_index(r, len(nodes)), identity, 1, ()) for r in reversed(roots)]
    while stack:
        ni, parent, depth, path = stack.pop()
        if ni in path:
            raise _GlbError('node_cycle')
        if ni in visited:
            raise _GlbError('node_shared')
        if depth > MAX_NODE_DEPTH:
            raise _GlbError('node_depth')
        visited.add(ni)
        node = nodes[ni]
        if not isinstance(node, dict):
            raise _GlbError('bad_node')
        if len(names) < MAX_NODE_NAMES and isinstance(node.get('name'), str):
            names.append(_ascii_name(node['name']))
        world = _matrix_mul(parent, _node_matrix(node))
        if 'mesh' in node:
            mi = _index(node['mesh'], len(gltf.get('meshes', [])))
            points, tris = _mesh_positions(gltf, bin_bytes, mi, cache)
            triangle_count += tris
            vertex_count += len(points)
            if vertex_count > MAX_POSITION_VERTICES:
                raise _GlbError('too_many_vertices')
            m = world
            for px, py, pz in points:
                x = m[0] * px + m[4] * py + m[8] * pz + m[12]
                y = m[1] * px + m[5] * py + m[9] * pz + m[13]
                z = m[2] * px + m[6] * py + m[10] * pz + m[14]
                cx, cy, cz = _to_cadquery((x, y, z))
                if cx < lo[0]:
                    lo[0] = cx
                if cx > hi[0]:
                    hi[0] = cx
                if cy < lo[1]:
                    lo[1] = cy
                if cy > hi[1]:
                    hi[1] = cy
                if cz < lo[2]:
                    lo[2] = cz
                if cz > hi[2]:
                    hi[2] = cz
        children = node.get('children', [])
        if not isinstance(children, list) or len(children) > MAX_NODES:
            raise _GlbError('bad_node')
        for child in reversed(children):
            stack.append((_index(child, len(nodes)), world, depth + 1, path + (ni,)))
    if triangle_count == 0 or not all(math.isfinite(v) for v in lo + hi):
        raise _GlbError('empty_mesh')
    return {'triangle_count': triangle_count, 'vertex_count': vertex_count,
            'bounds_mm': [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]], 'node_names': names}


# --------------------------------------------------------------------------- STEP


def _version(module, dist):
    value = getattr(module, '__version__', None)
    if isinstance(value, str) and value:
        return value
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:
        return 'unknown'


def measure_step(step_path):
    """Read the STEP with OCP and return the bounded face table dict or {"error": code}."""
    try:
        import cadquery as cq
        import OCP
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.BRepBndLib import BRepBndLib
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.BRepGProp import BRepGProp
        from OCP.Bnd import Bnd_Box
        from OCP.GProp import GProp_GProps
        from OCP.GeomAbs import (GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane,
                                 GeomAbs_Sphere, GeomAbs_Torus)
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
        from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID, \
            TopAbs_VERTEX
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS
    except ImportError:
        return {'error': 'import'}
    if not os.path.isfile(step_path) or os.path.getsize(step_path) > MAX_STEP_BYTES:
        return {'error': 'native_failed'}
    reader = STEPControl_Reader()
    if reader.ReadFile(step_path) != IFSelect_RetDone:
        return {'error': 'native_failed'}
    roots = reader.NbRootsForTransfer()
    if roots <= 0 or reader.TransferRoots() != roots:
        return {'error': 'native_failed'}
    shape = reader.OneShape()
    if shape.IsNull():
        return {'error': 'native_failed'}

    def explore(root, kind, cast):
        out, explorer = [], TopExp_Explorer(root, kind)
        while explorer.More():
            out.append(cast(explorer.Current()))
            explorer.Next()
            if len(out) > MAX_TOPOLOGY_ELEMENTS:
                raise ValueError('topology limit')
        return out

    def bounds_of(item):
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(item, box, False, False)
        if box.IsVoid() or box.IsOpen():
            raise ValueError('void bounds')
        coords = tuple(float(c) for c in box.Get())
        if not all(math.isfinite(c) for c in coords):
            raise ValueError('bad bounds')
        return list(coords)

    try:
        solids = explore(shape, TopAbs_SOLID, TopoDS.Solid_s)
        faces = explore(shape, TopAbs_FACE, TopoDS.Face_s)
        if len(faces) > MAX_FACES:
            return {'error': 'too_many_faces'}
        if not solids or not faces:
            return {'error': 'native_failed'}
        closed = 0
        volume = area = 0.0
        for solid in solids:
            shells = explore(solid, TopAbs_SHELL, TopoDS.Shell_s)
            if shells and all(BRep_Tool.IsClosed_s(s) for s in shells) and BRepCheck_Analyzer(solid, True).IsValid():
                closed += 1
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(solid, props, True, False, False)
            volume += props.Mass()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(solid, props, False, False)
            area += props.Mass()
        tolerances = []
        for kind, cast in ((TopAbs_FACE, TopoDS.Face_s), (TopAbs_EDGE, TopoDS.Edge_s),
                           (TopAbs_VERTEX, TopoDS.Vertex_s)):
            tolerances.extend(BRep_Tool.Tolerance_s(item) for item in explore(shape, kind, cast))
        types = {GeomAbs_Plane: 'plane', GeomAbs_Cylinder: 'cylinder', GeomAbs_Cone: 'cone',
                 GeomAbs_Sphere: 'sphere', GeomAbs_Torus: 'torus', GeomAbs_BSplineSurface: 'bspline'}
        transfer = None
        try:
            transfer = reader.WS().TransferReader()
        except Exception:
            transfer = None
        records = []
        for face in faces:
            adaptor = BRepAdaptor_Surface(face)
            surface = types.get(adaptor.GetType(), 'other')
            normal = None
            if surface == 'plane':
                d = adaptor.Plane().Axis().Direction()
                normal = [d.X(), d.Y(), d.Z()]
                if face.Orientation() == TopAbs_REVERSED:  # outward normal of the oriented face
                    normal = [-c for c in normal]
                length = math.sqrt(sum(c * c for c in normal))
                normal = [c / length for c in normal]
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props, False, False)
            centre = props.CentreOfMass()
            name = None
            if transfer is not None:
                try:
                    entity = transfer.EntityFromShapeResult(face, 1)
                    text = entity.Name().ToCString() if entity is not None else ''
                    name = _ascii_name(text) or None
                except Exception:
                    name = None
            records.append({'surface': surface, 'normal': normal,
                            'center': [centre.X(), centre.Y(), centre.Z()], 'area': props.Mass(),
                            'bounds': bounds_of(face), 'name': name})
        table = {
            'solid_count': len(solids), 'closed_solid_count': closed, 'bounds': bounds_of(shape),
            'volume': volume, 'area': area, 'max_topology_tolerance': max(tolerances) if tolerances else 0.0,
            'native_versions': [{'name': 'cadquery', 'version': _version(cq, 'cadquery')},
                                {'name': 'OCP', 'version': _version(OCP, 'cadquery-ocp')}],
            'faces': records,
        }
        return {'table': table}
    except Exception:
        return {'error': 'native_failed'}


def main(argv):
    if len(argv) not in (2, 3):
        return 2
    # OCCT writes diagnostics to the C-level stdout; keep fd 1 clean for the one JSON object.
    real_stdout = os.dup(1)
    os.dup2(2, 1)
    result = measure_step(argv[1])
    if 'table' in result and len(argv) == 3:
        try:
            with open(argv[2], 'rb') as handle:
                data = handle.read(MAX_GLB_BYTES + 1)
            result['mesh'] = parse_glb(data)
        except OSError:
            result['mesh'] = {'error': 'glb_unreadable'}
    try:
        payload = json.dumps(result, allow_nan=False).encode('ascii')
    except ValueError:
        payload = b'{"error": "native_failed"}'
    os.write(real_stdout, payload + b'\n')
    if result.get('error') == 'import':
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
