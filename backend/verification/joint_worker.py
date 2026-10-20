"""Trusted bounded joint verification of a delivered STEP assembly (no generated code).

Re-measures every declared joint on the reimported, transformed bodies: the
anchor faces are resolved in each part's modelled frame, moved by the
location stored in the STEP file, and measured natively. Also checks final
pairwise interference. ProcessRunner supplies deadline, memory and output
caps. This is geometric relation evidence, not a mechanism simulation or an
engineering certification.
"""
from pathlib import Path
import json
import math
import sys

# Executed as an absolute script by the resource-limited runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _walk(node, parent_loc):
    world = parent_loc * node.loc
    yield node, world
    for child in node.children:
        yield from _walk(child, world)


def _local_shape(node):
    import cadquery as cq
    obj = node.obj
    if isinstance(obj, cq.Workplane):
        values = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        return values[0] if len(values) == 1 else cq.Compound.makeCompound(values)
    return obj


def _move_point(point, loc):
    import cadquery as cq
    c = cq.Vertex.makeVertex(*point).moved(loc).Center()
    return (c.x, c.y, c.z)


def _move_dir(direction, loc):
    from joint_contract import sub, unit
    return unit(sub(_move_point(direction, loc), _move_point((0.0, 0.0, 0.0), loc)))


def _measured_face(shape, selector, loc):
    """Local anchor face moved to its delivered pose, measured natively."""
    import cadquery as cq
    from joint_contract import unit
    faces = cq.Workplane(obj=shape).faces(selector).vals()
    if len(faces) != 1 or faces[0].geomType() != "PLANE" or not faces[0].Area() > 1e-9:
        raise ValueError(f"anchor {selector!r} does not resolve to exactly one planar face")
    local = faces[0]
    lc = local.Center()
    ln = local.normalAt(lc)
    moved = local.moved(loc)
    c = moved.Center()
    n = moved.normalAt(c)
    return ((lc.x, lc.y, lc.z), unit((ln.x, ln.y, ln.z))), ((c.x, c.y, c.z), unit((n.x, n.y, n.z)))


def verify(step_path, spec):
    import cadquery as cq
    from joint_contract import (align_rotation, dot, freedom_analysis, joint_residuals, mat_vec,
                                norm, plane_basis, residuals_within, scale, sub, tolerances)

    assembly = cq.Assembly.load(str(step_path), importType="STEP")
    nodes = {}
    for node, world in _walk(assembly, cq.Location()):
        if node.name in spec["parts"] and node.obj is not None:
            if node.name in nodes:
                raise ValueError(f"STEP contains part label {node.name!r} more than once")
            nodes[node.name] = (_local_shape(node), world)
    missing = sorted(set(spec["parts"]) - set(nodes))
    failures = [f"part {name!r} missing from delivered STEP" for name in missing]

    results = []
    adjacency = {}
    for joint in spec["joints"] if not missing else []:
        src, tgt, kind, clearance = joint["source"], joint["target"], joint["joint"], joint["clearance_mm"]
        (shape_a, loc_a), (shape_b, loc_b) = nodes[src], nodes[tgt]
        entry = {key: joint[key] for key in ("source", "source_anchor", "target", "target_anchor", "joint")}
        entry["declared_clearance_mm"] = clearance
        try:
            local_a, a = _measured_face(shape_a, joint["source_anchor"], loc_a)
            local_b, b = _measured_face(shape_b, joint["target_anchor"], loc_b)
        except ValueError as exc:
            entry.update(status="indeterminate", reason=str(exc))
            failures.append(f"{src}->{tgt}: {exc}")
            results.append(entry)
            continue
        q, flip = align_rotation(local_b[1], scale(local_a[1], -1.0))
        ref = plane_basis(local_b[1])[0]
        expected_dir = _move_dir(mat_vec(q, ref), loc_a)
        actual_dir = _move_dir(ref, loc_b)
        length = max(shape_a.BoundingBox().DiagonalLength, shape_b.BoundingBox().DiagonalLength)
        tol = tolerances(length)
        residuals = joint_residuals(kind, a, b, clearance, spin_pair=(expected_dir, actual_dir))
        freedom = freedom_analysis(kind, a, b, clearance, length, expected_dir, actual_dir)
        d = sub(b[0], a[0])
        axial = dot(d, a[1])
        radial = norm(sub(d, scale(a[1], axial)))
        ok = residuals_within(residuals, tol) and freedom["free_motion_matches"]
        entry.update(
            status="verified" if ok else "failed",
            frame_world={"origin": list(a[0]), "axis": list(a[1])},
            measured_axial_gap_mm=axial,
            measured_radial_offset_mm=radial,
            normal_opposition_error_rad=math.acos(max(-1.0, min(1.0, -dot(a[1], b[1])))),
            residuals=residuals,
            tolerances=tol,
            freedom=freedom,
            orientation_convention="half-turn about " + str(list(flip)) if flip else "minimal rotation",
        )
        if not ok:
            failures.append(f"{src}->{tgt} ({kind}) relation not satisfied on delivered geometry")
        results.append(entry)
        adjacency.setdefault(src, set()).add(tgt)
        adjacency.setdefault(tgt, set()).add(src)

    # Final-pose interference between every pair of delivered bodies.
    overlaps = []
    placed = {name: shape.moved(loc) for name, (shape, loc) in nodes.items()}
    names = sorted(placed)
    for i, na in enumerate(names):
        for nb in names[i + 1:]:
            sa, sb = placed[na], placed[nb]
            ba, bb = sa.BoundingBox(), sb.BoundingBox()
            if (ba.xmax < bb.xmin or bb.xmax < ba.xmin or ba.ymax < bb.ymin or bb.ymax < ba.ymin
                    or ba.zmax < bb.zmin or bb.zmax < ba.zmin):
                continue
            volume = sa.intersect(sb).Volume()
            limit = max(1e-6, 1e-7 * min(sa.Volume(), sb.Volume()))
            if not math.isfinite(volume) or volume > limit:
                overlaps.append({"a": na, "b": nb, "volume_mm3": volume if math.isfinite(volume) else None})
    if overlaps:
        failures.append(f"{len(overlaps)} pair(s) of delivered bodies interpenetrate")

    edges = len(results)
    loops = edges - (len(nodes) - _components(nodes, adjacency)) if not missing else 0
    return {
        "passed": not failures,
        "method": "closed_form_joint_contract",
        "joints": results,
        "overlaps": overlaps,
        "interference_free": not overlaps,
        "mobility": (
            "open joint tree: relative freedoms are the per-joint declared free motions"
            if loops == 0 else
            f"{loops} closed loop(s): joints verified as consistent; mechanism mobility not computed"
        ),
        "kinematic_intent_note": (
            "The STEP/GLB carry one static pose. Joint freedoms are recorded here from the "
            "declared contract and residual rank analysis; the exchange files do not retain them."
        ),
        "failures": failures,
        "engineering_certified": False,
    }


def _components(nodes, adjacency):
    seen, count = set(), 0
    for name in nodes:
        if name in seen:
            continue
        count += 1
        stack = [name]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency.get(current, ()))
    return count


def main():
    step_path, spec_path, report_path = map(Path, sys.argv[1:])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    try:
        report = verify(step_path, spec)
    except Exception as exc:  # noqa: BLE001 — any failure is a failed verification
        report = {"passed": False, "failures": [f"{type(exc).__name__}: {exc}"],
                  "engineering_certified": False}
    report_path.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
