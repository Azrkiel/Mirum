"""Defect C: mate joints must deliver the declared relation, freedom and clearance.

Native two-body fixtures with a grounded base go through the real delivery
path: generated script -> AST-gated sandbox -> STEP acceptance -> independent
joint re-measurement of the accepted STEP bytes. Allowed and rejected motions
are applied to the delivered bodies and re-verified.
"""
import asyncio
import hashlib
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joint_contract as jc  # noqa: E402

native = pytest.mark.skipif(sys.platform == "win32", reason="Native OCP geometry runs in Linux Docker")

EXPECTED_CONSTRAINED = {"FASTENED": 6, "REVOLUTE": 5, "CYLINDRICAL": 4, "PLANAR": 3, "BALL": 3}


# ---------------------------------------------------------------------------
# Pure contract: residual rank and controlled perturbations (no kernel)
# ---------------------------------------------------------------------------

def _frames(normal=(0.0, 0.0, 1.0), origin=(1.0, -2.0, 5.0), clearance=0.0):
    a = (origin, normal)
    b = (jc.add(origin, jc.scale(normal, clearance)), jc.scale(normal, -1.0))
    return a, b


@pytest.mark.parametrize("joint", sorted(EXPECTED_CONSTRAINED))
@pytest.mark.parametrize("normal", [(0.0, 0.0, 1.0), jc.unit((1.0, 2.0, -0.5))])
def test_residual_rank_matches_declared_freedom(joint, normal):
    clearance = 0.0 if joint == "BALL" else 0.7
    a, b = _frames(normal, clearance=clearance)
    ref = jc.plane_basis(b[1])[0]
    result = jc.freedom_analysis(joint, a, b, clearance, 50.0, ref, ref)
    assert result["constrained_dof"] == EXPECTED_CONSTRAINED[joint]
    assert result["free_motion_matches"] is True


@pytest.mark.parametrize("joint", sorted(EXPECTED_CONSTRAINED))
def test_finite_allowed_motion_keeps_residuals_and_rejected_motion_breaks_them(joint):
    clearance = 0.0 if joint == "BALL" else 0.5
    a, b = _frames(clearance=clearance)
    ref = jc.plane_basis(b[1])[0]
    tol = jc.tolerances(50.0)
    for twist in jc.expected_free_twists(joint, a[1]):
        finite = tuple(x * (0.6 if i < 3 else 4.0) for i, x in enumerate(twist))
        mb, md = jc.moved_frames(b, ref, a[0], finite)
        assert jc.residuals_within(jc.joint_residuals(joint, a, mb, clearance, (ref, md)), tol), twist
    rejected = {
        "tilt": (0.02, 0, 0, 0, 0, 0), "spin": (0, 0, 0.02, 0, 0, 0),
        "axial": (0, 0, 0, 0, 0, 0.25), "radial": (0, 0, 0, 0.3, 0, 0),
    }
    blocked = {"FASTENED": ["tilt", "spin", "axial", "radial"], "REVOLUTE": ["tilt", "axial", "radial"],
               "CYLINDRICAL": ["tilt", "radial"], "PLANAR": ["tilt", "axial"],
               "BALL": ["axial", "radial"]}[joint]
    for name in blocked:
        mb, md = jc.moved_frames(b, ref, a[0], rejected[name])
        assert not jc.residuals_within(jc.joint_residuals(joint, a, mb, clearance, (ref, md)), tol), name


@pytest.mark.parametrize("joint", ["SLIDER", "GEAR", "SCREW", "RACK_PINION", "CAM", "WELD"])
def test_unsupported_joint_types_fail_closed(joint):
    with pytest.raises(ValueError, match="Unsupported joint"):
        jc.require_supported(joint)


def test_unsupported_mate_type_is_rejected_before_execution():
    import agents
    from schemas import AssemblyManifest
    manifest = AssemblyManifest.model_validate(_manifest("SLIDER", 0.0))
    with pytest.raises(agents.ScriptError, match="Unsupported joint"):
        agents.run_assembler_constraint(manifest, {"base": "/tmp/b.step", "cap": "/tmp/c.step"})


def test_rotation_round_trip_including_half_turn():
    for axis, angle in [((0, 0, 1), 0.3), (jc.unit((1, 1, 0)), math.pi), (jc.unit((0.2, -1, 3)), 2.9)]:
        back_axis, back_angle = jc.matrix_axis_angle(jc.axis_angle_matrix(axis, angle))
        m1, m2 = jc.axis_angle_matrix(axis, angle), jc.axis_angle_matrix(back_axis, back_angle)
        assert max(abs(x - y) for r1, r2 in zip(m1, m2) for x, y in zip(r1, r2)) < 1e-9


# ---------------------------------------------------------------------------
# Native end-to-end fixtures
# ---------------------------------------------------------------------------

def _manifest(joint, clearance, sa=">Z", ta="<Z", extra_parts=(), extra_rules=()):
    parts = [{"part_id": "base", "description": "base block", "anchor_tags": [sa]},
             {"part_id": "cap", "description": "cap block", "anchor_tags": [ta]}]
    rules = [{"source_part_id": "base", "target_part_id": "cap", "source_anchor": sa,
              "target_anchor": ta, "mate_type": joint, "clearance": clearance}]
    return {"assembly_name": "pair", "parts": parts + list(extra_parts), "mating_rules": rules + list(extra_rules)}


def _write(tmp_path, shapes):
    import cadquery as cq
    paths = {}
    for name, wp in shapes.items():
        path = tmp_path / f"{name}.step"
        cq.exporters.export(wp, str(path))
        paths[name] = str(path)
    return paths


def _default_shapes(cap_transform=None):
    import cadquery as cq
    cap = cq.Workplane("XY").box(20, 10, 6)
    if cap_transform:
        cap = cap_transform(cap)
    return {"base": cq.Workplane("XY").box(40, 30, 10), "cap": cap}


def _deliver(tmp_path, manifest_dict, shapes):
    """Real path: assembler script -> sandbox -> STEP acceptance -> joint verifier."""
    import agents
    from schemas import AssemblyManifest
    from verification.acceptance import accept_script
    from verification.joints import verify_assembly_joints
    manifest = AssemblyManifest.model_validate(manifest_dict)
    paths = _write(tmp_path, shapes)
    script = asyncio.run(agents.run_assembly_critic_loop(manifest, paths))
    delivery = asyncio.run(accept_script(script, output_variable="assembly"))
    report = asyncio.run(verify_assembly_joints(delivery["step"], manifest))
    return manifest, script, delivery, report


def _load(step_bytes, tmp_path, name="delivered.step"):
    import cadquery as cq
    path = tmp_path / name
    path.write_bytes(step_bytes)
    return cq.Assembly.load(str(path), importType="STEP")


def _bodies(assembly):
    return {c.name: c.obj.moved(c.loc) for c in assembly.children}


@native
@pytest.mark.parametrize("joint,clearance", [
    ("FASTENED", 0.0), ("FASTENED", 0.7), ("REVOLUTE", 0.0), ("REVOLUTE", 0.7),
    ("CYLINDRICAL", 0.7), ("PLANAR", 0.0), ("PLANAR", 0.7), ("BALL", 0.0),
])
def test_supported_joint_relation_is_verified_on_the_delivered_step(tmp_path, joint, clearance):
    manifest, script, delivery, report = _deliver(tmp_path, _manifest(joint, clearance), _default_shapes())
    assert "assemble_mated_parts(" in script and "constrain(" not in script
    assert report["passed"] is True, report
    entry = report["joints"][0]
    assert entry["status"] == "verified"
    assert entry["measured_axial_gap_mm"] == pytest.approx(clearance, abs=1e-6)
    assert entry["measured_radial_offset_mm"] == pytest.approx(0.0, abs=1e-6)
    assert entry["freedom"]["constrained_dof"] == EXPECTED_CONSTRAINED[joint]
    assert report["interference_free"] is True
    # Independent of the verifier: the gap between the delivered bodies.
    bodies = _bodies(_load(delivery["step"], tmp_path))
    gap = bodies["cap"].BoundingBox().zmin - bodies["base"].BoundingBox().zmax
    assert gap == pytest.approx(clearance, abs=1e-6)
    # Evidence is bound to this manifest and these exact STEP bytes.
    from verification.joints import manifest_digest
    assert report["bindings"]["manifest_sha256"] == manifest_digest(manifest)
    assert report["bindings"]["step_sha256"] == hashlib.sha256(delivery["step"]).hexdigest()
    assert report["engineering_certified"] is False


@native
def test_changing_clearance_changes_the_measured_relation(tmp_path):
    gaps, scripts = [], []
    for c in (0.0, 7.0):
        sub = tmp_path / f"c{c}"
        sub.mkdir()
        _, script, delivery, report = _deliver(sub, _manifest("REVOLUTE", c), _default_shapes())
        assert report["passed"] is True
        bodies = _bodies(_load(delivery["step"], sub))
        gaps.append(bodies["cap"].BoundingBox().zmin - bodies["base"].BoundingBox().zmax)
        scripts.append(script)
    assert scripts[0] != scripts[1], "clearance must reach the generated assembly"
    assert gaps[1] - gaps[0] == pytest.approx(7.0, abs=1e-6)


@native
@pytest.mark.parametrize("transform", [
    lambda w: w.translate((13, -7, 5)),
    lambda w: w.rotate((0, 0, 0), (0, 0, 1), 30).translate((-4, 9, 2)),
    lambda w: w.rotate((0, 0, 0), (1, 0, 0), 180),          # '<Z' now faces the other way
])
def test_relation_holds_for_different_modelled_part_frames(tmp_path, transform):
    _, _, delivery, report = _deliver(tmp_path, _manifest("FASTENED", 1.5), _default_shapes(transform))
    assert report["passed"] is True, report
    entry = report["joints"][0]
    assert entry["measured_axial_gap_mm"] == pytest.approx(1.5, abs=1e-6)
    assert entry["measured_radial_offset_mm"] == pytest.approx(0.0, abs=1e-6)


@native
def test_equal_normals_use_the_documented_half_turn(tmp_path):
    _, _, _, report = _deliver(tmp_path, _manifest("FASTENED", 0.0, sa=">Z", ta=">Z"), _default_shapes())
    assert report["passed"] is True, report
    assert report["joints"][0]["orientation_convention"].startswith("half-turn")


def _moved_step(tmp_path, delivery, motion, name):
    import cadquery as cq
    loaded = _load(delivery["step"], tmp_path, name + "-in.step")
    out = cq.Assembly()
    for child in loaded.children:
        loc = motion * child.loc if child.name == "cap" else child.loc
        out.add(child.obj, name=child.name, loc=loc)
    path = tmp_path / (name + ".step")
    out.save(str(path), exportType="STEP")
    return path


def _about(pivot, axis, angle_deg, shift=(0, 0, 0)):
    import cadquery as cq
    p = cq.Vector(*pivot)
    return (cq.Location(p + cq.Vector(*shift)) * cq.Location(cq.Vector(), cq.Vector(*axis), angle_deg)
            * cq.Location(-p))


@native
@pytest.mark.parametrize("joint", ["FASTENED", "REVOLUTE", "CYLINDRICAL", "PLANAR", "BALL"])
def test_allowed_motion_is_accepted_and_rejected_motion_is_detected(tmp_path, joint):
    from verification.joint_worker import verify
    from verification.joints import joint_spec
    clearance = 0.0 if joint == "BALL" else 0.5
    manifest, _, delivery, report = _deliver(tmp_path, _manifest(joint, clearance), _default_shapes())
    assert report["passed"] is True
    origin = report["joints"][0]["frame_world"]["origin"]
    spec = joint_spec(manifest)
    allowed = {
        "FASTENED": [],
        "REVOLUTE": [_about(origin, (0, 0, 1), 37)],
        "CYLINDRICAL": [_about(origin, (0, 0, 1), 37, shift=(0, 0, 4))],
        "PLANAR": [_about(origin, (0, 0, 1), 20, shift=(3, -2, 0))],
        "BALL": [_about(origin, (0, 0, 1), 25)],
    }[joint]
    rejected = {
        "tilt": _about(origin, (1, 0, 0), 1.0, shift=(0, 0, 1.0)),   # lifted so no overlap
        "spin": _about(origin, (0, 0, 1), 2.0),
        "axial": _about(origin, (0, 0, 1), 0.0, shift=(0, 0, 0.25)),
        "radial": _about(origin, (0, 0, 1), 0.0, shift=(0.3, 0, 0)),
    }
    blocked = {"FASTENED": ["tilt", "spin", "axial", "radial"], "REVOLUTE": ["tilt", "axial", "radial"],
               "CYLINDRICAL": ["tilt", "radial"], "PLANAR": ["tilt", "axial"],
               "BALL": ["axial", "radial"]}[joint]
    for i, motion in enumerate(allowed):
        moved = verify(_moved_step(tmp_path, delivery, motion, f"allowed{i}"), spec)
        assert moved["joints"][0]["status"] == "verified", moved["joints"][0]["residuals"]
    for name in blocked:
        moved = verify(_moved_step(tmp_path, delivery, rejected[name], name), spec)
        assert moved["joints"][0]["status"] == "failed", name
        assert moved["passed"] is False


@native
def test_redundant_consistent_loop_passes_and_conflicting_loop_is_rejected(tmp_path):
    import agents
    dup = {"source_part_id": "base", "target_part_id": "cap", "source_anchor": ">Z",
           "target_anchor": "<Z", "mate_type": "REVOLUTE", "clearance": 0.4}
    _, _, _, report = _deliver(tmp_path, _manifest("FASTENED", 0.4, extra_rules=[dup]), _default_shapes())
    assert report["passed"] is True
    assert "closed loop" in report["mobility"]
    conflict = dict(dup, clearance=1.4)
    sub = tmp_path / "conflict"
    sub.mkdir()
    with pytest.raises(agents.ScriptError, match="[Oo]verconstrained"):
        _deliver(sub, _manifest("FASTENED", 0.4, extra_rules=[conflict]), _default_shapes())


@native
@pytest.mark.parametrize("make_cap", [
    lambda cq: cq.Workplane("XY").sphere(5),                                   # non-planar '<Z'
    lambda cq: cq.Workplane("XY").pushPoints([(-6, 0), (6, 0)]).box(4, 4, 4),  # two '<Z' faces
])
def test_indeterminate_anchor_frame_is_rejected(tmp_path, make_cap):
    import agents
    import cadquery as cq
    shapes = {"base": cq.Workplane("XY").box(40, 30, 10), "cap": make_cap(cq)}
    with pytest.raises(agents.ScriptError):
        _deliver(tmp_path, _manifest("FASTENED", 0.0), shapes)


@native
def test_final_pose_interference_is_rejected_by_acceptance_and_by_the_joint_verifier(tmp_path):
    import cadquery as cq
    from cad_utils import assemble_mated_parts
    from schemas import AssemblyManifest
    from verification.acceptance import AcceptanceError
    from verification.joint_worker import verify
    from verification.joints import joint_spec
    shapes = dict(_default_shapes(), intruder=cq.Workplane("XY").box(6, 6, 6))
    extra_part = [{"part_id": "intruder", "description": "overlapping block", "anchor_tags": []}]
    extra_rule = [{"source_part_id": "base", "target_part_id": "intruder", "source_anchor": ">Z",
                   "target_anchor": "<Z", "translation": "0, 0, 4", "clearance": 0.0}]
    manifest_dict = _manifest("FASTENED", 0.0, extra_parts=extra_part, extra_rules=extra_rule)
    # Existing delivery gate: positive-volume interpenetration is never accepted.
    with pytest.raises(AcceptanceError, match="Structural"):
        _deliver(tmp_path, manifest_dict, shapes)
    # The joint verifier independently reports the same final-pose overlap.
    assembly = assemble_mated_parts(shapes, "base", [("base", ">Z", "cap", "<Z", "FASTENED", 0.0)],
                                    {"intruder": (0.0, 0.0, 4.0)})
    path = tmp_path / "overlap.step"
    assembly.save(str(path), exportType="STEP")
    report = verify(path, joint_spec(AssemblyManifest.model_validate(manifest_dict)))
    assert report["joints"][0]["status"] == "verified"
    assert report["passed"] is False and report["interference_free"] is False
    assert {(o["a"], o["b"]) for o in report["overlaps"]} >= {("base", "intruder")}


@native
def test_chain_reversed_edge_rotated_middle_and_translated_root(tmp_path):
    import cadquery as cq
    shapes = {
        "base": cq.Workplane("XY").box(40, 30, 10),
        # Middle part modelled rotated and offset in its own file.
        "mid": cq.Workplane("XY").box(20, 20, 8).rotate((0, 0, 0), (0, 0, 1), 30).translate((5, -3, 2)),
        "top": cq.Workplane("XY").box(10, 10, 4),
        "side": cq.Workplane("XY").box(8, 8, 8),
        "side_cap": cq.Workplane("XY").box(6, 6, 3),
    }
    parts = [{"part_id": n, "description": n, "anchor_tags": [">Z", "<Z"]} for n in shapes]
    rules = [
        {"source_part_id": "base", "target_part_id": "mid", "source_anchor": ">Z",
         "target_anchor": "<Z", "mate_type": "FASTENED", "clearance": 0.5},
        # Listed target->source: BFS from the base places 'top' from 'mid'.
        {"source_part_id": "top", "target_part_id": "mid", "source_anchor": "<Z",
         "target_anchor": ">Z", "mate_type": "FASTENED", "clearance": 0.25},
        # A second component rooted at an explicitly translated part.
        {"source_part_id": "base", "target_part_id": "side", "source_anchor": ">Z",
         "target_anchor": "<Z", "translation": "100, 0, 0", "clearance": 0.0},
        {"source_part_id": "side", "target_part_id": "side_cap", "source_anchor": ">Z",
         "target_anchor": "<Z", "mate_type": "PLANAR", "clearance": 0.0},
    ]
    _, _, delivery, report = _deliver(tmp_path, {"assembly_name": "chain", "parts": parts,
                                                 "mating_rules": rules}, shapes)
    assert report["passed"] is True, report
    assert [j["status"] for j in report["joints"]] == ["verified"] * 3
    bodies = _bodies(_load(delivery["step"], tmp_path))
    bb = {n: s.BoundingBox() for n, s in bodies.items()}
    assert bb["mid"].zmin - bb["base"].zmax == pytest.approx(0.5, abs=1e-6)
    assert bb["top"].zmin - bb["mid"].zmax == pytest.approx(0.25, abs=1e-6)
    assert bb["side"].center.x == pytest.approx(100.0, abs=1e-6)
    assert bb["side_cap"].zmin == pytest.approx(bb["side"].zmax, abs=1e-6)
    # FASTENED keeps the modelled orientation: mid's 30 degree yaw survives.
    assert bb["mid"].xlen == pytest.approx(bb["mid"].ylen, abs=1e-6)
    assert bb["mid"].xlen == pytest.approx(20 * (math.cos(math.radians(30)) + math.sin(math.radians(30))), abs=1e-6)


def test_half_turn_axis_is_stable_under_normal_noise():
    clean, _ = jc.align_rotation((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))
    noisy, _ = jc.align_rotation((2e-17, -1e-17, 1.0), (0.0, 0.0, -1.0))
    assert max(abs(x - y) for r1, r2 in zip(clean, noisy) for x, y in zip(r1, r2)) < 1e-12
