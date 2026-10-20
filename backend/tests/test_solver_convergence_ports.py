"""A real limited IPOPT solve must not be mislabeled/exported as successful."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='Native OCP solver gate runs in Linux Docker')


def test_nonraising_native_iteration_limit_cannot_export_success(tmp_path):
    import cadquery as cq
    from agents import run_assembler_constraint
    from schemas import AssemblyManifest, MatingRule, PartDefinition

    paths = {}
    for name in ['base', 'cap']:
        path = tmp_path / (name+'.step')
        cq.exporters.export(cq.Workplane('XY').box(10, 20, 3), str(path))
        paths[name] = str(path)
    manifest = AssemblyManifest(assembly_name='limited_pair', parts=[
        PartDefinition(part_id='base',description='base',anchor_tags=['>Z']),
        PartDefinition(part_id='cap',description='cap',anchor_tags=['<Z']),
    ],mating_rules=[MatingRule(source_part_id='base',target_part_id='cap',source_anchor='>Z',
        target_anchor='<Z',mate_type='FASTENED',clearance=0)])
    script = run_assembler_constraint(manifest, paths)
    bootstrap = '''import casadi as ca
_original_solver = ca.Opti.solver
def _limited_solver(self, name, plugin_options, solver_options):
    return _original_solver(self, name, plugin_options, dict(solver_options, max_iter=0))
ca.Opti.solver = _limited_solver
'''
    completed = subprocess.run([sys.executable, '-'], input=bootstrap+script, text=True,
        cwd=tmp_path, capture_output=True, timeout=45)
    assert completed.returncode != 0, completed.stdout+completed.stderr
    assert 'SOLVER_SUCCESS' not in completed.stdout
    assert not (tmp_path/'output.glb').exists()
