"""Defect B: a valid AssemblyManifest must be a coherent, placeable assembly graph.

Every case goes through the real Pydantic model (``model_validate``), not an
extracted validator function.
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from schemas import AssemblyManifest, sanitize_part_id  # noqa: E402


def part(pid, anchors=(">Z", "<Z")):
    return {"part_id": pid, "description": f"{pid} probe", "anchor_tags": list(anchors)}


def mate(src, tgt, kind="FASTENED", sa=">Z", ta="<Z", clearance=0.0, **extra):
    rule = {"source_part_id": src, "target_part_id": tgt, "source_anchor": sa,
            "target_anchor": ta, "mate_type": kind, "clearance": clearance}
    rule.update(extra)
    return rule


def place(src, tgt, xyz="0, 0, 0", sa=">Z", ta="<Z", clearance=0.0):
    return {"source_part_id": src, "target_part_id": tgt, "source_anchor": sa,
            "target_anchor": ta, "translation": xyz, "clearance": clearance}


def build(parts, rules):
    return AssemblyManifest.model_validate({"assembly_name": "probe", "parts": parts, "mating_rules": rules})


def rejected(parts, rules, match):
    with pytest.raises(ValidationError, match=match):
        build(parts, rules)


# --- Recorded false acceptances --------------------------------------------

def test_empty_manifest_is_rejected():
    rejected([], [], "at least one part")


def test_duplicate_part_ids_are_rejected_not_merged():
    rejected([part("same"), part("same")], [], "[Dd]uplicate part_id")


# --- Valid controls ---------------------------------------------------------

def test_single_part_without_rules_is_valid():
    assert len(build([part("solo")], []).parts) == 1


def test_single_part_legacy_self_placement_is_valid():
    assert build([part("solo")], [place("solo", "solo")]).mating_rules[0].translation == "0, 0, 0"


def test_multipart_mated_chain_is_valid():
    m = build([part("base"), part("mid"), part("top")],
              [mate("base", "mid"), mate("mid", "top", kind="REVOLUTE", clearance=0.5)])
    assert [p.part_id for p in m.parts] == ["base", "mid", "top"]


def test_multipart_explicit_translation_placement_is_valid():
    build([part("tabletop"), part("leg_1"), part("leg_2")],
          [place("tabletop", "leg_1", "10, 10, -20"), place("tabletop", "leg_2", "-10, 10, -20")])


def test_mate_cycle_is_left_to_the_assembler_consistency_check():
    build([part("a"), part("b"), part("c")],
          [mate("a", "b"), mate("b", "c"), mate("a", "c")])


# --- Identity in the generated-assembly namespace ----------------------------

def test_sanitizer_is_the_shared_assembly_namespace():
    assert sanitize_part_id("bolt-1") == "bolt_1"
    assert sanitize_part_id("") == "unnamed_part"


@pytest.mark.parametrize("ids", [
    ("bolt-1", "bolt_1"),             # punctuation collapses
    ("a" * 64 + "x", "a" * 64 + "y"),  # truncation collapses
    ("bolt.1", "bolt 1"),              # two different raw ids, one STEP label
])
def test_normalisation_collisions_are_rejected(ids):
    a, b = ids
    rejected([part(a), part(b)], [place(a, b)], "collide")


# --- References, anchors, self-reference -------------------------------------

def test_undefined_reference_is_rejected():
    rejected([part("a"), part("b")], [mate("a", "ghost")], "undefined part")


def test_self_referencing_mate_is_rejected():
    rejected([part("a")], [mate("a", "a")], "[Ss]elf-referenc")


@pytest.mark.parametrize("anchor", ["hole_center", "faces('>Z')", ">Z'); import os; ('", "%CYLINDER", "|Z", ""])
def test_mate_anchor_must_be_a_supported_planar_face_selector(anchor):
    rejected([part("a", [">Z", anchor]), part("b")], [mate("a", "b", sa=anchor)], "anchor")


def test_named_anchor_must_be_declared_on_its_part():
    rejected([part("a"), part("b")], [place("a", "b", sa="flange_top")], "not declared")


def test_declared_named_anchor_is_allowed_for_explicit_placement():
    build([part("a", ["flange_top"]), part("b")], [place("a", "b", sa="flange_top")])


def test_undeclared_native_selector_still_resolves_against_geometry():
    # Existing planner output may mate on '>Z' without listing it; native
    # selectors are resolved on the delivered body, so this stays valid.
    build([part("a", ["hole_center"]), part("b", ["<Z"])], [mate("a", "b")])


# --- Connectivity / placement policy -----------------------------------------

def test_disconnected_mate_component_is_rejected():
    rejected([part("base"), part("a"), part("b")], [mate("a", "b")], "not connected")


def test_unplaced_extra_part_is_rejected():
    rejected([part("base"), part("a"), part("loose")], [mate("base", "a")], "no placement")


def test_part_with_two_conflicting_placements_is_rejected():
    rejected([part("base"), part("a")], [mate("base", "a"), place("base", "a", "0, 0, 5")],
             "conflicting placement")


def test_mate_rule_must_not_also_carry_an_absolute_translation():
    rejected([part("base"), part("a")], [mate("base", "a", translation="0, 0, 5")], "translation")


# --- Clearance contract -------------------------------------------------------

@pytest.mark.parametrize("value", [-0.1, float("nan"), float("inf"), 1e6])
def test_clearance_must_be_finite_nonnegative_and_bounded(value):
    rejected([part("a"), part("b")], [mate("a", "b", clearance=value)], "clearance")


def test_translation_rule_cannot_carry_an_unapplied_clearance():
    rejected([part("a"), part("b")], [place("a", "b", clearance=0.5)], "clearance")


def test_ball_clearance_is_unsupported_rather_than_ignored():
    rejected([part("a"), part("b")], [mate("a", "b", kind="BALL", clearance=0.2)], "clearance")
