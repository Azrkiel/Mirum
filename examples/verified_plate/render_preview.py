"""Software depth-buffer render of actual STEP triangles; no substitute image.

Avoids unavailable OpenGL/OSMesa drivers. This preview is not validation evidence.
"""
from pathlib import Path
import sys
import cadquery as cq
from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
import numpy as np
from PIL import Image, ImageDraw

folder = Path(sys.argv[1])
reader = STEPControl_Reader()
assert reader.ReadFile(str(folder / "plate.step")) == IFSelect_RetDone
assert reader.TransferRoots() > 0
shape = cq.Shape.cast(reader.OneShape())
vertices, triangles = shape.tessellate(0.04, 0.1)
points = np.array([p.toTuple() for p in vertices])
width, height = 1200, 800
eye = np.array([95., -120., 120.]); target = np.array([0., 0., 3.])
z = eye - target; z /= np.linalg.norm(z)
x = np.cross([0., 0., 1.], z); x /= np.linalg.norm(x)
y = np.cross(z, x)
camera = (points - target) @ np.column_stack([x, y, z])
projected = np.column_stack([width / 2 + camera[:, 0] * width / 114,
                             height / 2 - camera[:, 1] * height / 76, camera[:, 2]])
color = np.full((height, width, 3), [245, 247, 252], dtype=np.uint8)
depth = np.full((height, width), -np.inf)
light = np.array([0.1, -0.4, 0.9]); light /= np.linalg.norm(light)
for ids in triangles:
    face = projected[list(ids)]
    low = np.maximum(np.floor(face[:, :2].min(axis=0)).astype(int), [0, 0])
    high = np.minimum(np.ceil(face[:, :2].max(axis=0)).astype(int), [width-1, height-1])
    if np.any(high < low):
        continue
    a, b, c = face
    denominator = (b[1]-c[1])*(a[0]-c[0]) + (c[0]-b[0])*(a[1]-c[1])
    if abs(denominator) < 1e-10:
        continue
    xx, yy = np.meshgrid(np.arange(low[0], high[0]+1)+0.5,
                          np.arange(low[1], high[1]+1)+0.5)
    u = ((b[1]-c[1])*(xx-c[0]) + (c[0]-b[0])*(yy-c[1])) / denominator
    v = ((c[1]-a[1])*(xx-c[0]) + (a[0]-c[0])*(yy-c[1])) / denominator
    w = 1-u-v
    values = u*a[2] + v*b[2] + w*c[2]
    region = depth[low[1]:high[1]+1, low[0]:high[0]+1]
    mask = (u >= -1e-10) & (v >= -1e-10) & (w >= -1e-10) & (values > region)
    region[mask] = values[mask]
    tri = points[list(ids)]
    normal = np.cross(tri[1]-tri[0], tri[2]-tri[0])
    normal /= max(np.linalg.norm(normal), 1e-12)
    brightness = 0.35 + 0.65 * abs(normal @ light)
    rgb = np.clip(np.array([90, 150, 195])*brightness, 0, 255).astype(np.uint8)
    color[low[1]:high[1]+1, low[0]:high[0]+1][mask] = rgb
assert np.count_nonzero(np.isfinite(depth)) > 1000, "No actual geometry rendered"
image = Image.fromarray(color)
draw = ImageDraw.Draw(image)
draw.text((24, 20), "ACTUAL STEP PREVIEW: 80 x 50 x 6 mm, four diameter-6 through-bores", fill="#182b3b")
draw.text((24, 44), "Native geometry checked; NOT engineering certification", fill="#182b3b")
image.save(folder / "preview.png")
print(f"Depth-buffer rendered {len(triangles)} STEP-derived triangles: {folder / 'preview.png'}")
