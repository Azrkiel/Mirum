"""Fail-closed assembly regressions inspired by Text-2-CAD acceptance gates."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agents
from schemas import AssemblyManifest, MateType, MatingRule, PartDefinition


def manifest(kind=MateType.REVOLUTE):
    return AssemblyManifest(
        assembly_name="pair",
        parts=[PartDefinition(part_id="base", description="base box", anchor_tags=[">Z"]),
               PartDefinition(part_id="cap", description="cap box", anchor_tags=["<Z"])],
        mating_rules=[MatingRule(source_part_id="base", target_part_id="cap",
            source_anchor=">Z", target_anchor="<Z", mate_type=kind, clearance=0)],
    )


def execute_generated(monkeypatch, *, fail=False):
    saved, placed = [], []
    class Assembly:
        def save(self, path): saved.append(path)
    def assemble_mated_parts(bodies, ground, joints, placements=None):
        # Lightweight double of the trusted helper; the native joint tests
        # (test_joint_semantics.py) exercise the real OCP placement.
        if fail:
            raise ValueError("fixture overconstrained joints")
        placed.append((ground, list(joints), dict(placements or {})))
        return Assembly()
    cq = SimpleNamespace(importers=SimpleNamespace(importStep=lambda path: object()))
    monkeypatch.setitem(sys.modules, "cadquery", cq)
    monkeypatch.setitem(sys.modules, "cad_utils", SimpleNamespace(assemble_mated_parts=assemble_mated_parts))
    script = agents.run_assembler_constraint(manifest(), {"base": "/tmp/base.step", "cap": "/tmp/cap.step"})
    return script, saved, placed


def test_placement_error_prevents_export(monkeypatch):
    script, saved, _ = execute_generated(monkeypatch, fail=True)
    with pytest.raises(ValueError, match="fixture overconstrained"):
        exec(compile(script, "<generated-assembly-test>", "exec"), {})
    assert saved == []


def test_enum_selects_the_declared_joint_not_a_fallback(monkeypatch):
    script, saved, placed = execute_generated(monkeypatch)
    exec(compile(script, "<generated-assembly-test>", "exec"), {})
    assert placed == [("base", [("base", ">Z", "cap", "<Z", "REVOLUTE", 0.0)], {})]
    assert saved == ["output.glb"]


def test_metadata_coupling_is_not_silently_enforced_as_plane():
    with pytest.raises(agents.ScriptError, match="[Uu]nsupported|metadata"):
        agents.run_assembler_constraint(manifest(MateType.GEAR), {"base": "/tmp/base.step", "cap": "/tmp/cap.step"})


def test_assembly_critic_rejects_unreported_placement_even_with_zero_exit(monkeypatch):
    monkeypatch.setattr(agents, "check_assembly_interpenetration", lambda _: [])
    monkeypatch.setattr(agents, "execute_cad_script", AsyncMock(return_value={
        "status": "success", "output": "fixture exited without placing joints"}))
    with pytest.raises(agents.ScriptError, match="placement"):
        asyncio.run(agents.run_assembly_critic_loop(manifest(), {"base": "/tmp/base.step", "cap": "/tmp/cap.step"}))
