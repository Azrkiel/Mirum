"""Trusted bounded native STEP inspection/render entrypoint (no generated code).

ProcessRunner supplies deadline, memory cap, output cap and cancellation ownership.
No API/provider credentials are required. This worker is not a hostile-file OS sandbox.
"""
from pathlib import Path
import hashlib
import json
import math
import struct
import sys

# Executed as an absolute script by the resource-limited runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    step_path, glb_path, report_path = map(Path, sys.argv[1:])
    from verification.solid_set import inspect_step
    report = inspect_step(str(step_path))
    if report.get('passed') is True:
        import cadquery as cq
        import OCP
        # Preserve imported STEP assembly labels/hierarchy instead of rendering
        # the generated source's unverified GLB or selecting only the first root.
        try:
            assembly = cq.Assembly.load(str(step_path), importType='STEP')
        except ValueError as exc:
            if str(exc) != 'Step file does not contain an assembly':
                raise
            # Plain non-XCAF STEP has no assembly tree. Import every root;
            # selecting Workplane.val() would silently drop additional roots.
            roots = cq.importers.importStep(str(step_path)).vals()
            assembly = cq.Assembly(cq.Compound.makeCompound(roots), name='model')
        imported = assembly.toCompound()
        volume = imported.Volume()
        if (len(imported.Solids()) != report['solid_count'] or not math.isfinite(volume)
                or abs(volume - report['volume_mm3']) > max(1e-6, report['volume_mm3'] * 1e-8)):
            raise ValueError('STEP scene import changed body count or measured volume')
        assembly.save(str(glb_path), exportType='GLB')
        data = glb_path.read_bytes()
        if len(data) < 20 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
            raise ValueError('Native GLB header is invalid')
        json_size, kind = struct.unpack_from('<II', data, 12)
        if kind != 0x4E4F534A or json_size > len(data) - 20:
            raise ValueError('Native GLB JSON chunk is invalid')
        scene = json.loads(data[20:20+json_size])
        if not scene.get('meshes') or not scene.get('scenes'):
            raise ValueError('Native GLB contains no rendered geometry')
        report['artifacts'] = {'step_sha256': hashlib.sha256(step_path.read_bytes()).hexdigest(),
                               'glb_sha256': hashlib.sha256(data).hexdigest()}
        report['versions'] = {'cadquery': cq.__version__, 'ocp': OCP.__version__}
        report['rendered_from_validated_step'] = True
        report['engineering_certified'] = False
    report_path.write_text(json.dumps(report, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
