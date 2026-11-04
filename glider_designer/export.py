"""
Outputs for the best designs: STL meshes (per part + assembled), a three-view
drawing, 1:1 SVG cutting templates and a build spec sheet.
"""
from __future__ import annotations

import math
import os
import struct

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from assembly import Glider
from components import Surface, naca


# ---------------------------------------------------------------------- #
# STL
# ---------------------------------------------------------------------- #
def write_stl(path: str, points: np.ndarray, faces: np.ndarray, scale: float = 1000.0):
    """Binary STL. `faces` may be triangles or quads (quads are split). Units: mm."""
    pts = np.asarray(points, dtype=float) * scale
    faces = np.asarray(faces)
    if faces.shape[1] == 4:
        faces = np.vstack([faces[:, [0, 1, 2]], faces[:, [0, 2, 3]]])
    tri = pts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nn = np.linalg.norm(n, axis=1)
    keep = nn > 1e-12
    tri, n = tri[keep], n[keep] / nn[keep, None]
    with open(path, "wb") as f:
        f.write(b"glider_designer STL (mm)".ljust(80, b" "))
        f.write(struct.pack("<I", len(tri)))
        for normal, t in zip(n, tri):
            f.write(struct.pack("<12fH", *normal, *t[0], *t[1], *t[2], 0))


def export_stls(gl: Glider, folder: str):
    ap = gl.airplane(tail_incidence_deg=gl.tail_incidence_deg)
    for w in ap.wings:
        p, f = w.mesh_body(method="tri")
        write_stl(os.path.join(folder, f"part_{w.name}.stl"), p, f)
    p, f = ap.fuselages[0].mesh_body(method="tri")
    write_stl(os.path.join(folder, "part_fuselage.stl"), p, f)
    p, f = ap.mesh_body(method="tri")
    write_stl(os.path.join(folder, "assembly.stl"), p, f)


# ---------------------------------------------------------------------- #
# Three-view drawing with CG / NP marked
# ---------------------------------------------------------------------- #
def three_view(gl: Glider, res: dict, path: str):
    ap = gl.airplane(tail_incidence_deg=gl.tail_incidence_deg)
    axs = ap.draw_three_view(show=False)
    fig = plt.gcf()
    fig.set_size_inches(10, 10)
    axs = np.asarray(axs)
    cg, npx = res["cg_x_m"], res["np_x_m"]
    for ax, (xi, yi) in [(axs[0, 0], (cg, 0)), (axs[1, 0], (cg, 0))]:
        ax.plot([cg], [0], marker="o", color="#d62728", ms=9, zorder=10, label="CG")
        ax.plot([npx], [0], marker="x", color="#1f77b4", ms=10, mew=2.5, zorder=10, label="Neutral point")
    h, l = axs[1, 0].get_legend_handles_labels()
    fig.legend(h[:2], l[:2], loc="upper right", fontsize=9)
    fig.suptitle(
        f"{gl.id}   |   L/D {res['L_over_D']:.1f}   sink {res['sink_rate_m_s']:.2f} m/s   "
        f"speed {res['glide_speed_m_s']:.1f} m/s   mass {res['mass_kg']*1000:.0f} g",
        fontsize=11)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close("all")


# ---------------------------------------------------------------------- #
# 1:1 SVG cutting templates
# ---------------------------------------------------------------------- #
def _panel_outline(surf: Surface):
    """Unrolled half-planform: (s, x) where s = distance along the panel from the root."""
    s_acc, le, te, joints = 0.0, [], [], []
    prev = None
    for sec in surf.sections:
        if prev is not None:
            s_acc += math.hypot(sec.y - prev.y, sec.z - prev.z)
        le.append((s_acc, sec.x))
        te.append((s_acc, sec.x + sec.chord))
        joints.append(s_acc)
        prev = sec
    return le, te, joints


def _svg(width_mm, height_mm, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.1f}mm" height="{height_mm:.1f}mm" '
            f'viewBox="0 0 {width_mm:.1f} {height_mm:.1f}">\n'
            f'<rect width="100%" height="100%" fill="white"/>\n{body}</svg>\n')


def _polyline(pts, **attrs) -> str:
    a = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    return '<polyline points="' + " ".join(f"{x:.2f},{y:.2f}" for x, y in pts) + f'" fill="none" {a}/>\n'


def surface_template(surf: Surface, title: str, path: str, note: str = ""):
    le, te, joints = _panel_outline(surf)
    mm = 1000.0
    x0 = min(p[1] for p in le)
    pad = 15.0
    outline = [(p[0] * mm + pad, (p[1] - x0) * mm + pad + 20) for p in le] + \
              [(p[0] * mm + pad, (p[1] - x0) * mm + pad + 20) for p in reversed(te)]
    outline.append(outline[0])
    W = max(p[0] for p in outline) + pad + 10
    H = max(p[1] for p in outline) + pad + 40
    body = _polyline(outline, stroke="black", stroke_width=0.4)
    for (s, xl), (_, xt) in zip(le[1:-1], te[1:-1]):
        body += _polyline([(s * mm + pad, (xl - x0) * mm + pad + 20), (s * mm + pad, (xt - x0) * mm + pad + 20)],
                          stroke="#d62728", stroke_width=0.4, stroke_dasharray="3,2")
    body += f'<text x="{pad}" y="{pad}" font-size="6" font-family="sans-serif">{title} (root at left, 1:1 scale)</text>\n'
    if note:
        body += f'<text x="{pad}" y="{pad + 8}" font-size="4.5" font-family="sans-serif">{note}</text>\n'
    # 100 mm scale bar to check print scaling
    yb = H - 12
    body += _polyline([(pad, yb), (pad + 100, yb)], stroke="black", stroke_width=0.8)
    body += f'<text x="{pad}" y="{yb + 7}" font-size="4.5" font-family="sans-serif">100 mm - measure this after printing</text>\n'
    with open(path, "w") as f:
        f.write(_svg(W, H, body))


def airfoil_template(code: str, chord_m: float, title: str, path: str):
    coords = naca(code).coordinates * chord_m * 1000.0
    pad = 15.0
    pts = [(x + pad, -y + pad + 25) for x, y in coords]
    W, H = chord_m * 1000 + 2 * pad, 60 + max(abs(c[1]) for c in coords) * 2
    body = _polyline(pts + [pts[0]], stroke="black", stroke_width=0.4)
    body += f'<text x="{pad}" y="{pad}" font-size="6" font-family="sans-serif">{title}: NACA {code}, chord {chord_m*1000:.0f} mm (1:1)</text>\n'
    with open(path, "w") as f:
        f.write(_svg(W, H, body))


def export_templates(gl: Glider, folder: str):
    w = gl.wing_surface()
    dih = [math.degrees(math.atan2(b.z - a.z, b.y - a.y)) for a, b in zip(w.sections[:-1], w.sections[1:])]
    surface_template(w, "Wing half-panel (cut 2, mirror one)", os.path.join(folder, "template_wing_half.svg"),
                     note="Red dashed = dihedral breaks. Panel dihedral angles from root: "
                          + ", ".join(f"{a:.1f} deg" for a in dih))
    airfoil_template(gl.wing.naca, gl.wing.root_chord, "Wing root rib", os.path.join(folder, "template_wing_root_airfoil.svg"))
    for s in gl.tail_surfaces():
        label = {"htail": "Stabiliser half (cut 2)", "vtail": "Fin (cut 1)",
                 "vee": f"V-tail panel (cut 2), {s.dihedral_deg:.0f} deg from horizontal"}[s.role]
        surface_template(s, label, os.path.join(folder, f"template_{s.name}.svg"))


# ---------------------------------------------------------------------- #
# Spec sheet
# ---------------------------------------------------------------------- #
def spec_sheet(gl: Glider, res: dict, rank: int, path: str):
    ms = gl.mass_summary()
    w, f, t = gl.wing, gl.fuse, gl.tail
    lines = [
        f"# Rank {rank}: {gl.id}",
        "",
        "## Performance (predicted, still air)",
        f"- Glide ratio L/D: **{res['L_over_D']:.1f}**  (distance from {gl.cfg['mission']['launch_height_m']:.0f} m: {res['glide_distance_m']:.0f} m)",
        f"- Sink rate: {res['sink_rate_m_s']:.2f} m/s  (time aloft: {res['time_aloft_s']:.1f} s)",
        f"- Glide / landing speed: {res['glide_speed_m_s']:.1f} m/s, landing energy {res['landing_energy_J']:.2f} J",
        f"- Trim: alpha {res['alpha_trim_deg']:.1f} deg, CL {res['CL_trim']:.2f}, Reynolds {res['reynolds_mac']:.0f}",
        "",
        "## Build and balance",
        f"- Total mass: {ms['mass']*1000:.0f} g  ("
        + ", ".join(f"{k} {v*1000:.0f} g" for k, v in ms["by_part"].items()) + ")",
        (f"- **Nose ballast: {gl.ballast_kg*1000:.1f} g** at {f.ballast_station()*1000:.0f} mm from the nose tip"
         if gl.ballast_kg > 5e-4 else "- Nose ballast: none needed"),
        *( [f"- **Payload centre: {gl.payload_position()*1000:.0f} mm from the nose tip** "
            f"({gl.cfg['mission']['payload_mass_kg']*1000:.0f} g)"] if gl.cfg["mission"]["payload_mass_kg"] > 0 else [] ),
        f"- **Balance point (CG): {res['cg_x_m']*1000:.0f} mm from the nose tip** "
        f"(neutral point {res['np_x_m']*1000:.0f} mm, static margin {res['static_margin']*100:.0f}% MAC)",
        f"- **Stabiliser incidence: {res['tail_incidence_deg']:+.2f} deg** relative to the fuselage "
        f"(wing at {w.incidence_deg:+.2f} deg, decalage {res['decalage_deg']:.2f} deg)",
        f"- **Wing leading edge at {gl.wing_position()[0]*1000:.0f} mm from the nose tip**, on top of the fuselage",
        "",
        "## Stability",
        f"- Static margin {res['static_margin']*100:.0f}% MAC, Cm_alpha {res['Cma']:.2f}/rad",
        f"- Cl_beta {res['Clb']:.3f} (dihedral effect), Cn_beta {res['Cnb']:.3f} (weathercock)",
        f"- Dutch roll damping ratio {res['dutch_roll_zeta']:.2f}; spiral "
        + ("stable" if res["spiral_real"] <= 0 else f"time-to-double {res['spiral_time_to_double_s']:.0f} s"),
        f"- Tail volumes: horizontal {res['Vh']:.2f}, vertical {res['Vv']:.3f}",
        "",
        "## Parts",
        f"- Wing {w.id}: {w.style}, span {w.span*1000:.0f} mm, root chord {w.root_chord*1000:.0f} mm, "
        f"taper {w.taper:.2f}, sweep {w.sweep_qc_deg:.1f} deg, dihedral {w.dihedral_deg:.1f} deg, "
        f"washout {w.washout_deg:.1f} deg, NACA {w.naca}, area {res['wing_area_m2']*1e4:.0f} cm^2, AR {res['aspect_ratio']:.1f}",
        f"- Fuselage {f.id}: {f.style}, length {f.length*1000:.0f} mm, max diameter {f.max_diameter*1000:.0f} mm",
        f"- Tail {t.id}: {t.kind}" + (
            f", panels {t.vt_panel_span*1000:.0f} mm x {t.vt_root_chord*1000:.0f} mm at {t.vt_dihedral_deg:.0f} deg"
            if t.kind == "V" else
            f", stab span {t.h_span*1000:.0f} mm x chord {t.h_root_chord*1000:.0f} mm, "
            f"fin height {t.v_height*1000:.0f} mm x chord {t.v_root_chord*1000:.0f} mm") + f", NACA {t.airfoil}",
        "",
        "## Files",
        "- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)",
        "- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)",
        "- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)",
        "",
        "Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence "
        "(or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------- #
# Gallery: top-view outlines of the best designs side by side
# ---------------------------------------------------------------------- #
def gallery(items: list, path: str):
    n = len(items)
    if n == 0:
        return
    cols = min(4, n)
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(3.6 * cols, 3.4 * rows), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    # same scale in every panel so sizes can be compared
    half = max(g.wing.span / 2 for _, g, _ in items) * 1.05
    length = max(g.fuse.length for _, g, _ in items) * 1.03
    for ax, (rank, gl, res) in zip(axes.flat, items):
        ax.set_xlim(-half, half)
        ax.set_ylim(length, -0.03)
        for surf in [gl.wing_surface()] + gl.tail_surfaces():
            if surf.role == "vtail":
                continue
            le = [(s.y, s.x) for s in surf.sections]
            te = [(s.y, s.x + s.chord) for s in surf.sections]
            outline = le + te[::-1] + [le[0]]
            ys, xs = zip(*outline)
            ax.fill(ys, xs, color="#4c72b0", alpha=0.85, lw=0)
            ax.fill([-y for y in ys], xs, color="#4c72b0", alpha=0.85, lw=0)
        st = gl.fuse.stations()
        xs = [s[0] for s in st]
        ws = [s[1] / 2 for s in st]
        ax.fill(ws + [-w for w in ws[::-1]], xs + xs[::-1], color="#55575c", lw=0)
        ax.plot([0], [res["cg_x_m"]], "o", color="#d62728", ms=4)
        ax.set_aspect("equal")
        ax.set_title(f"#{rank} {gl.id}\n{gl.wing.style} / {gl.fuse.style} / {gl.tail.kind}\n"
                     f"L/D {res['L_over_D']:.1f}, sink {res['sink_rate_m_s']:.2f} m/s", fontsize=8.5)
    fig.suptitle("Top designs, seen from above (red dot = balance point)", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
