import sys, tempfile
from pathlib import Path
sys.path[:0] = ['/workspace']
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import cadquery as cq
from backend.dragsters.local import export_preview, preview_mesh
from backend.dragsters.styles import load_preset
from backend.dragsters.styles.narrow_waist import build

out = Path(sys.argv[1]); tag = sys.argv[2] if len(sys.argv) > 2 else 'preset'
spec = load_preset('narrow_waist')
work = Path(tempfile.mkdtemp())
cq.exporters.export(build(spec), str(work / 'b.step'), exportType='STEP')
export_preview(work / 'b.step', work / 'p.glb')
mesh = preview_mesh((work / 'p.glb').read_bytes())
v = np.asarray(mesh.vertices) * 1000.0
v = np.stack([v[:, 0], -v[:, 2], v[:, 1]], axis=1)     # glTF Y-up -> CAD (x, y, z up)
print('bounds', v.min(0), v.max(0))
tris = v[np.asarray(mesh.faces)]
n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
light = np.array([-0.4, -0.6, 0.7]); light /= np.linalg.norm(light)
shade = 0.25 + 0.75 * np.clip(np.abs(n @ light), 0, 1)
colors = np.stack([0.30 * shade, 0.52 * shade, 0.85 * shade, np.ones_like(shade)], axis=1)
mid = (v.min(0) + v.max(0)) / 2; half = (v.max(0) - v.min(0)).max() / 2
for name, elev, azim, size in (('side', 0, -90, (9, 3.2)), ('top', 90, -90, (9, 3.2)), ('iso', 22, -55, (7, 5))):
    fig = plt.figure(figsize=size, dpi=180)
    ax = fig.add_subplot(111, projection='3d', proj_type='ortho')
    ax.add_collection3d(Poly3DCollection(tris, facecolors=colors, edgecolors=colors, linewidths=0.15))
    lo, hi = v.min(0), v.max(0)
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
    ax.set_box_aspect(tuple(hi - lo))
    ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
    fig.savefig(out / f'd4-narrow-waist-{name}{tag}.png', bbox_inches='tight', pad_inches=0.05); plt.close(fig)
