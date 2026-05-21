"""Pre-flight: try to reproduce the historical 'helical sweeps crash the BRep kernel' case.

Runs three independently-documented helix-construction approaches against the
currently-installed CadQuery/OCP stack. Prints one line per approach with the
result. Exit code 0 on a clean run regardless of whether the sweeps succeed -
the diagnostic value is in the printed log.
"""
from __future__ import annotations

import math
import sys
import traceback


def banner(name: str) -> None:
    print(f"\n=== {name} ===", flush=True)


def report(approach: str, result: object, error: str | None) -> None:
    status = "OK" if error is None else "FAIL"
    print(f"[{status}] {approach}: {result}", flush=True)
    if error:
        print(error, flush=True)


def approach_a_wire_makehelix() -> None:
    """cq.Wire.makeHelix(...).sweep(...) - the 'flagged' direct path."""
    banner("Approach A: cq.Wire.makeHelix + .sweep")
    try:
        import cadquery as cq

        pitch = 4.0
        height = 40.0
        radius = 10.0
        helix_wire = cq.Wire.makeHelix(pitch=pitch, height=height, radius=radius)
        path = cq.Workplane(obj=helix_wire)
        profile = (
            cq.Workplane("XZ")
            .center(radius, 0)
            .rect(1.5, 0.8)
        )
        solid = profile.sweep(path, isFrenet=True)
        solid_obj = solid.val()
        ok = solid_obj.isValid()
        vol = solid_obj.Volume()
        report(
            "Wire.makeHelix + sweep",
            {"isValid": ok, "volume_mm3": round(vol, 3), "type": type(solid_obj).__name__},
            None,
        )
    except Exception:
        report("Wire.makeHelix + sweep", "EXCEPTION", traceback.format_exc())


def approach_b_pipe_shell() -> None:
    """BRepOffsetAPI_MakePipeShell direct, Frenet, with C1 reinforcement."""
    banner("Approach B: BRepOffsetAPI_MakePipeShell direct (Frenet + ApproxC1)")
    try:
        import cadquery as cq
        from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
        from OCP.TopAbs import TopAbs_SOLID

        pitch = 4.0
        height = 40.0
        radius = 10.0
        spine_wire_cq = cq.Wire.makeHelix(pitch=pitch, height=height, radius=radius)
        spine = spine_wire_cq.wrapped

        # Rectangle cross-section, placed at helix start point on +X
        prof_pts = [
            cq.Vector(radius - 0.75, -0.4, 0),
            cq.Vector(radius + 0.75, -0.4, 0),
            cq.Vector(radius + 0.75, 0.4, 0),
            cq.Vector(radius - 0.75, 0.4, 0),
        ]
        edges = []
        n = len(prof_pts)
        for i in range(n):
            a = prof_pts[i]
            b = prof_pts[(i + 1) % n]
            edges.append(cq.Edge.makeLine(a, b).wrapped)
        wb = BRepBuilderAPI_MakeWire()
        for e in edges:
            wb.Add(e)
        profile_wire = wb.Wire()

        builder = BRepOffsetAPI_MakePipeShell(spine)
        builder.SetMode(True)          # Frenet
        builder.SetForceApproxC1(True)
        builder.Add(profile_wire, False, False)
        builder.Build()
        builder.MakeSolid()
        ok_done = builder.IsDone()
        shape = builder.Shape()
        is_solid = shape.ShapeType() == TopAbs_SOLID
        report(
            "BRepOffsetAPI_MakePipeShell",
            {"IsDone": ok_done, "is_TopAbs_SOLID": is_solid, "type": int(shape.ShapeType())},
            None,
        )
    except Exception:
        report("BRepOffsetAPI_MakePipeShell", "EXCEPTION", traceback.format_exc())


def approach_c_polyline_sweep() -> None:
    """80-segment polyline approximation of a helix, then .sweep(isFrenet=True)."""
    banner("Approach C: 80-segment polyline + .sweep(isFrenet=True)")
    try:
        import cadquery as cq

        pitch = 4.0
        height = 40.0
        radius = 10.0
        n_segments = 80
        turns = height / pitch
        pts = []
        for i in range(n_segments + 1):
            t = i / n_segments
            theta = 2 * math.pi * turns * t
            z = height * t
            pts.append((radius * math.cos(theta), radius * math.sin(theta), z))

        edges = []
        for i in range(n_segments):
            a = cq.Vector(*pts[i])
            b = cq.Vector(*pts[i + 1])
            edges.append(cq.Edge.makeLine(a, b))
        spine = cq.Wire.assembleEdges(edges)

        path_wp = cq.Workplane(obj=spine)
        profile = (
            cq.Workplane("XZ")
            .center(radius, 0)
            .rect(1.5, 0.8)
        )
        solid = profile.sweep(path_wp, isFrenet=True)
        solid_obj = solid.val()
        ok = solid_obj.isValid()
        vol = solid_obj.Volume()
        report(
            "polyline + sweep",
            {"isValid": ok, "volume_mm3": round(vol, 3), "type": type(solid_obj).__name__},
            None,
        )
    except Exception:
        report("polyline + sweep", "EXCEPTION", traceback.format_exc())


def approach_d_realistic_cooling_channel() -> None:
    """Realistic propulsion case: thin rectangular cooling-channel sweep around a 25 mm nozzle.

    Mirrors the geometry the production code path needs (Reference prompt #3).
    """
    banner("Approach D: realistic regen cooling-channel sweep (60 channels mock — single)")
    try:
        import cadquery as cq

        r_outer = 27.0
        channel_width = 2.0
        channel_depth = 3.0
        axial_length = 50.0
        helix_angle_deg = 15.0
        # pitch = 2*pi*r / tan(angle)
        pitch = 2 * math.pi * r_outer / math.tan(math.radians(helix_angle_deg))
        height = axial_length
        turns = max(0.05, height / pitch)
        actual_height = turns * pitch
        helix_wire = cq.Wire.makeHelix(pitch=pitch, height=actual_height, radius=r_outer)
        path = cq.Workplane(obj=helix_wire)
        profile = (
            cq.Workplane("XZ")
            .center(r_outer, 0)
            .rect(channel_width, channel_depth)
        )
        channel = profile.sweep(path, isFrenet=True)
        solid_obj = channel.val()
        ok = solid_obj.isValid()
        vol = solid_obj.Volume()
        report(
            "regen cooling-channel single helix",
            {
                "isValid": ok,
                "volume_mm3": round(vol, 3),
                "pitch_mm": round(pitch, 2),
                "turns": round(turns, 3),
            },
            None,
        )
    except Exception:
        report("regen cooling-channel single helix", "EXCEPTION", traceback.format_exc())


def main() -> int:
    print("Pre-flight: helix-sweep ban falsification")
    import cadquery
    print(f"cadquery version: {cadquery.__version__}")
    try:
        import OCP  # noqa: F401
        print("OCP: importable")
    except Exception as e:
        print(f"OCP: NOT importable ({e})")

    approach_a_wire_makehelix()
    approach_b_pipe_shell()
    approach_c_polyline_sweep()
    approach_d_realistic_cooling_channel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
