"""Provider-free B01 probe. Run in the Linux verification image, offline.

Usage: python scripts/checked_engineering_baseline.py /evidence <source-sha>
The output directory is local evidence, never a public accepted bundle.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request


def main():
    destination = Path(sys.argv[1])
    destination.mkdir(parents=True, exist_ok=True)
    assert platform.system() == 'Linux', 'Use the Linux/OCP verification runtime'
    assert not any(os.environ.get(k) for k in (
        'GEMINI_API_KEY_HEAVY', 'GEMINI_API_KEY_PRO', 'GOOGLE_API_KEY'))
    source_sha = sys.argv[2]
    from backend.tests.test_engineering_spec import spec_data
    import cadquery as cq
    import trimesh

    records = []
    log = (destination / 'server.log').open('w')
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app',
                               '--host', '127.0.0.1', '--port', '8765'],
                              stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen('http://127.0.0.1:8765/openapi.json', timeout=1) as response:
                    assert response.status == 200
                break
            except (urllib.error.URLError, TimeoutError):
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Loopback server did not start; inspect server.log')
                time.sleep(0.2)

        cases = [
            ('invalid', {'specification': spec_data(height_mm=-1.0)}, 422, None),
            ('accepted', {'specification': spec_data(height_mm=5.0, tip_force_n=50.0),
                          'candidate_heights_mm': [5.0, 10.0, 15.0]}, 200, 'accepted'),
            ('rejected', {'specification': spec_data(tip_force_n=50.0, allowable_stress_mpa=1.0),
                          'candidate_heights_mm': [5.0, 10.0, 15.0]}, 200, 'rejected'),
        ]
        for name, payload, expected_http, expected_status in cases:
            (destination / f'{name}-request.json').write_text(json.dumps(payload, indent=2))
            request = urllib.request.Request('http://127.0.0.1:8765/engineering/generate',
                data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            start = time.monotonic()
            try:
                response = urllib.request.urlopen(request, timeout=420)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                status = response.status
                result = json.load(response)
            elapsed = time.monotonic() - start
            assert status == expected_http, (name, status, result)
            if expected_status:
                assert result['status'] == expected_status, result
            if name == 'accepted':
                assert [a['status'] for a in result['attempts']] == ['rejected', 'rejected', 'accepted']
                assert result['specification']['height_mm'] == 15.0
                for key, filename in [('step', 'model.step'), ('glb', 'preview.glb')]:
                    (destination / filename).write_bytes(base64.b64decode(result[key], validate=True))
                report = result['engineering_report']
                assert report['step_sha256'] == hashlib.sha256((destination/'model.step').read_bytes()).hexdigest()
                assert report['physics']['state'] == 'passed'
                shape = cq.importers.importStep(str(destination/'model.step')).val()
                assert shape.isValid() and len(shape.Solids()) == 1
                assert abs(shape.Volume() - 30000) < 1e-6
                scene = trimesh.load(io.BytesIO((destination/'preview.glb').read_bytes()), file_type='glb')
                mesh = scene.dump(concatenate=True)
                assert len(mesh.faces) > 0
                # Inspect the actual GLB mesh, retaining a human-viewable image.
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                from mpl_toolkits.mplot3d.art3d import Poly3DCollection
                figure = plt.figure(figsize=(10, 4))
                axis = figure.add_subplot(projection='3d')
                axis.add_collection3d(Poly3DCollection(mesh.triangles, facecolor='#5ca5ce',
                                                      edgecolor='#224b67', alpha=0.9))
                bounds = mesh.bounds
                axis.auto_scale_xyz(*[(bounds[0, i], bounds[1, i]) for i in range(3)])
                axis.set_box_aspect(mesh.extents.copy())
                axis.set_title('B01: actual accepted GLB (200 x 10 x 15 mm beam)')
                figure.savefig(destination/'preview.png', dpi=140)
                plt.close(figure)
                records.append(dict(case=name, http=status, seconds=elapsed,
                    step_volume_mm3=shape.Volume(), glb_bounds=scene.bounds.tolist(),
                    glb_faces=len(mesh.faces), physics=report['physics']['solution']))
            else:
                assert not result.get('step') and not result.get('glb')
                records.append(dict(case=name, http=status, seconds=elapsed))
            (destination / f'{name}-response.json').write_text(json.dumps(result, indent=2))
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        log.close()
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True,
                   stdout=(destination/'pip-check.txt').open('w'))
    freeze = subprocess.check_output([sys.executable, '-m', 'pip', 'freeze'], text=True)
    (destination/'pip-freeze.txt').write_text(freeze)
    manifest = dict(source_sha=source_sha, platform=platform.platform(), python=sys.version,
        provider_calls=0, network='Docker --network none; loopback only', cases=records,
        versions={k: metadata.version(k) for k in (
            'cadquery', 'cadquery-ocp', 'numpy', 'fastapi', 'pydantic', 'pytest', 'httpx', 'uvicorn', 'trimesh')},
        files={p.name: dict(bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest())
               for p in sorted(destination.iterdir()) if p.is_file() and p.name != 'manifest.json'})
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
