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
    saved, constraints = [], []
    class Assembly:
        def add(self, *args, **kwargs): return self
        def constrain(self, *args): constraints.append(args); return self
        def solve(self):
            if fail: raise RuntimeError("fixture unsatisfied constraints")
            self._solve_result = {"success": True, "return_status": "Solve_Succeeded"}
            return self
        def save(self, path): saved.append(path)
    cq = SimpleNamespace(Assembly=Assembly, importers=SimpleNamespace(importStep=lambda path: object()))
    monkeypatch.setitem(sys.modules, "cadquery", cq)
    # Execute the real trusted helper against this lightweight assembly double;
    # the separate native solver regression exercises actual OCP/CasADi.
    import ast
    tree = ast.parse((Path(__file__).resolve().parents[1]/'cad_utils.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'solve_assembly_checked')
    ns = {'cq': cq}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<trusted-helper-test>', 'exec'), ns)
    monkeypatch.setitem(sys.modules, 'cad_utils', SimpleNamespace(solve_assembly_checked=ns['solve_assembly_checked']))
    script = agents.run_assembler_constraint(manifest(), {"base": "/tmp/base.step", "cap": "/tmp/cap.step"})
    return script, saved, constraints


def test_solver_error_prevents_export(monkeypatch):
    script, saved, _ = execute_generated(monkeypatch, fail=True)
    with pytest.raises(RuntimeError, match="fixture unsatisfied"):
        exec(compile(script, "<generated-assembly-test>", "exec"), {})
    assert saved == []


def test_enum_selects_the_declared_constraint_not_fallback_plane(monkeypatch):
    script, saved, constraints = execute_generated(monkeypatch)
    exec(compile(script, "<generated-assembly-test>", "exec"), {})
    assert [call[-1] for call in constraints] == ["Axis"]
    assert saved == ["output.glb"]


def test_metadata_coupling_is_not_silently_enforced_as_plane():
    with pytest.raises(agents.ScriptError, match="[Uu]nsupported|metadata"):
        agents.run_assembler_constraint(manifest(MateType.GEAR), {"base": "/tmp/base.step", "cap": "/tmp/cap.step"})


def test_assembly_critic_rejects_solver_failure_even_with_zero_exit(monkeypatch):
    monkeypatch.setattr(agents, "check_assembly_interpenetration", lambda _: [])
    monkeypatch.setattr(agents, "execute_cad_script", AsyncMock(return_value={
        "status": "success", "output": "SOLVER_FAILURE: fixture constraints unsatisfied"}))
    with pytest.raises(agents.ScriptError, match="[Ss]olv"):
        asyncio.run(agents.run_assembly_critic_loop(manifest(), {"base": "/tmp/base.step", "cap": "/tmp/cap.step"}))
