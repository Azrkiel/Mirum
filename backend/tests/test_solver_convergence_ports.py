"""A real limited IPOPT solve must not be mislabeled/exported as successful.

The mate-joint assembler no longer uses the CadQuery solver (joints are
placed in closed form, see test_joint_semantics.py), but generated scripts
that build a constrained cq.Assembly themselves still go through
cad_utils.solve_assembly_checked, which must reject a non-raising failure.
"""
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='Native OCP solver gate runs in Linux Docker')


def test_nonraising_native_iteration_limit_cannot_export_success(tmp_path):
    script = '''import casadi as ca
_original_solver = ca.Opti.solver
def _limited_solver(self, name, plugin_options, solver_options):
    return _original_solver(self, name, plugin_options, dict(solver_options, max_iter=0))
ca.Opti.solver = _limited_solver
import cadquery as cq
from cad_utils import solve_assembly_checked
box = cq.Workplane('XY').box(10, 20, 3)
assembly = cq.Assembly()
assembly.add(box, name='base')
assembly.add(box, name='cap', loc=cq.Location(cq.Vector(5, 5, 9)))
assembly.constrain('base@faces@>Z', 'cap@faces@<Z', 'Plane')
solve_assembly_checked(assembly)
print('SOLVER_SUCCESS')
assembly.save('output.glb')
'''
    from pathlib import Path
    backend = str(Path(__file__).resolve().parents[1])
    completed = subprocess.run([sys.executable, '-'], input=script, text=True, cwd=tmp_path,
        capture_output=True, timeout=45, env={'PYTHONPATH': backend, 'PATH': '/usr/local/bin:/usr/bin:/bin'})
    assert completed.returncode != 0, completed.stdout+completed.stderr
    assert 'SOLVER_SUCCESS' not in completed.stdout
    assert not (tmp_path/'output.glb').exists()


def test_mate_assembler_does_not_depend_on_the_iterative_solver(tmp_path):
    import cadquery as cq
    from agents import run_assembler_constraint
    from schemas import AssemblyManifest, MatingRule, PartDefinition
    paths = {}
    for name in ['base', 'cap']:
        path = tmp_path / (name+'.step')
        cq.exporters.export(cq.Workplane('XY').box(10, 20, 3), str(path))
        paths[name] = str(path)
    manifest = AssemblyManifest(assembly_name='pair', parts=[
        PartDefinition(part_id='base', description='base', anchor_tags=['>Z']),
        PartDefinition(part_id='cap', description='cap', anchor_tags=['<Z']),
    ], mating_rules=[MatingRule(source_part_id='base', target_part_id='cap', source_anchor='>Z',
        target_anchor='<Z', mate_type='FASTENED', clearance=0)])
    script = run_assembler_constraint(manifest, paths)
    assert 'solve(' not in script and 'constrain(' not in script
